import os
import imaplib
import email
import sqlite3
import re
import time
import threading
from datetime import datetime, timedelta
from flask import Flask, render_template_string, redirect, url_for, request
from openai import OpenAI
import resend

app = Flask(__name__)
file_lock = threading.Lock()

IMAP_SERVER = "imap.gmail.com"
MAILBOX = "INBOX"

PORT = int(os.getenv("PORT", "8080"))

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
AI_FROM_EMAIL = os.getenv("AI_FROM_EMAIL")
MAIL_USER = os.getenv("MAIL_USER", "")
MAIL_PASS = os.getenv("MAIL_PASS", "")

DB_FILE = "mailbot.db"

REPLY_CATEGORIES = ["kunde", "vigtig"]

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS replies (
            mail_id TEXT PRIMARY KEY,
            saved_at TEXT,
            sender TEXT,
            recipient TEXT,
            product_context TEXT,
            subject TEXT,
            category TEXT,
            requires_reply TEXT,
            summary TEXT,
            draft_reply TEXT,
            original_preview TEXT,
            status TEXT,
            sent_at TEXT,
            send_error TEXT,
            seen INTEGER DEFAULT 0
        )
        """)

        conn.commit()
        conn.close()

def ensure_replies_columns():
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(replies)")
        columns = [row["name"] for row in cur.fetchall()]

        if "recipient" not in columns:
            cur.execute("ALTER TABLE replies ADD COLUMN recipient TEXT")
        if "product_context" not in columns:
            cur.execute("ALTER TABLE replies ADD COLUMN product_context TEXT")

        conn.commit()
        conn.close()

def extract_first_name(sender):
    name, _ = email.utils.parseaddr(sender)
    if name:
        return name.split(" ")[0]
    return "der"

def extract_reply_email(sender):
    _, addr = email.utils.parseaddr(sender)
    return addr

def strip_quoted_text(text):
    if not text:
        return ""
    return text.split("From:")[0]

def decode_mime_text(text):
    if not text:
        return ""
    return str(email.header.make_header(email.header.decode_header(text)))

def get_plain_text_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(errors="ignore")
    else:
        return msg.get_payload(decode=True).decode(errors="ignore")
    return ""

def get_openai_client():
    return OpenAI()

def ai_generate_reply(sender, subject, body):
    client = get_openai_client()

    prompt = f"""
Du svarer som Ulla Vase.

Mail:
{body}

Svar kort og konkret.

Hvis der spørges om pris:
- Starter: 129 kr pr. bruger pr. måned
- Pro: 179 kr pr. bruger pr. måned
- Business: 229 kr pr. bruger pr. måned
- skriv at der betales årligt
- tilføj link: https://vinterguide.dk/intro.html#priser

Start:
Hej {extract_first_name(sender)},

Slut:
Mvh Ulla Vase
"""

    res = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt
    )

    return res.output_text.strip()

def save_pending_reply(mail_id, sender, subject, body):
    reply = ai_generate_reply(sender, subject, body)

    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
        INSERT OR REPLACE INTO replies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            str(mail_id),
            datetime.utcnow().isoformat(),
            sender,
            "",
            "vinterguide",
            subject,
            "kunde",
            "ja",
            "",
            reply,
            body[:1000],
            "pending_approval",
            None,
            None,
            0
        ))

        conn.commit()
        conn.close()

def update_reply_status(mail_id, status, sent_at=None, send_error=None):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE replies
            SET status = ?, sent_at = ?, send_error = ?
            WHERE mail_id = ?
        """, (status, sent_at, send_error, str(mail_id)))
        conn.commit()
        conn.close()
        def mark_seen(mail_id):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE replies SET seen = 1 WHERE mail_id = ?", (str(mail_id),))
        conn.commit()
        conn.close()

def get_active_replies():
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM replies
            WHERE status IN ('pending_approval','approved_api','send_failed')
            ORDER BY datetime(saved_at) DESC
        """)
        rows = cur.fetchall()
        conn.close()
        return rows

def get_history():
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM replies
            WHERE status IN ('sent','rejected','archived')
            ORDER BY datetime(saved_at) DESC
            LIMIT 100
        """)
        rows = cur.fetchall()
        conn.close()
        return rows

def get_reply(mail_id):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM replies WHERE mail_id = ?", (str(mail_id),))
        row = cur.fetchone()
        conn.close()
        return row

def update_draft(mail_id, draft_reply):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE replies
            SET draft_reply = ?, seen = 1
            WHERE mail_id = ?
        """, (draft_reply, str(mail_id)))
        conn.commit()
        conn.close()

def send_via_resend(to_email, subject, draft_reply):
    resend.api_key = RESEND_API_KEY

    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    html = f"<p>{draft_reply.replace(chr(10), '<br>')}</p>"

    return resend.Emails.send({
        "from": AI_FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "html": html
    })

def check_mail():
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(MAIL_USER, MAIL_PASS)
    mail.select(MAILBOX)

    status, messages = mail.search(None, "ALL")
    mail_ids = messages[0].split()

    for mail_id in mail_ids[-50:]:
        mail_id_int = int(mail_id)

        if get_reply(mail_id_int):
            continue

        status, msg_data = mail.fetch(mail_id, "(RFC822)")

        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])

                sender = decode_mime_text(msg.get("From"))
                subject = decode_mime_text(msg.get("Subject"))
                body = strip_quoted_text(get_plain_text_body(msg))

                save_pending_reply(mail_id_int, sender, subject, body)

    mail.logout()

def polling_loop():
    while True:
        try:
            check_mail()
        except Exception as e:
            print("Fejl:", e)
        time.sleep(60)

HTML = """
<h1>Mailbot</h1>
{% for mail in mails %}
<p>{{mail['subject']}}</p>
{% endfor %}
"""

@app.route("/")
def dashboard():
    mails = get_active_replies()
    return render_template_string(HTML, mails=mails)

@app.route("/send/<mail_id>", methods=["POST"])
def send_reply(mail_id):
    item = get_reply(mail_id)

    try:
        send_via_resend(
            extract_reply_email(item["sender"]),
            item["subject"],
            item["draft_reply"]
        )

        update_reply_status(mail_id, "sent", datetime.utcnow().isoformat())

    except Exception as e:
        update_reply_status(mail_id, "send_failed", None, str(e))

    return redirect(url_for("dashboard"))

if __name__ == "__main__":
    init_db()
    ensure_replies_columns()

    worker = threading.Thread(target=polling_loop, daemon=True)
    worker.start()

    app.run(host="0.0.0.0", port=PORT)
