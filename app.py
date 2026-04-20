# =========================
# MAILBOT V4.8
# DEL 1
# =========================

import os
import imaplib
import email
import sqlite3
import re
from datetime import datetime
from flask import Flask, render_template_string, redirect, url_for, request
from openai import OpenAI
import resend
import threading

app = Flask(__name__)
file_lock = threading.Lock()

IMAP_SERVER = "imap.gmail.com"
MAILBOX = "INBOX"

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
AI_FROM_EMAIL = os.getenv("AI_FROM_EMAIL")
MAIL_USER = os.getenv("MAIL_USER", "")
MAIL_PASS = os.getenv("MAIL_PASS", "")

DB_FILE = "mailbot.db"

REPLY_CATEGORIES = ["kunde", "vigtig"]

# =========================
# DATABASE
# =========================

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
            is_self_test TEXT,
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

def save_last_mail_id(mail_id):
    with open("last_mail_id.txt", "w") as f:
        f.write(str(mail_id))

def get_last_mail_id():
    if not os.path.exists("last_mail_id.txt"):
        return None
    with open("last_mail_id.txt", "r") as f:
        return int(f.read().strip())

# =========================
# HJÆLP
# =========================

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

# =========================
# AI
# =========================

def get_openai_client():
    return OpenAI()

def get_product_context(subject):
    if "slush" in subject.lower():
        return "slushbook", open("product_slushbook.txt").read()
    return "vinterguide", open("product_vinterguide.txt").read()

def ai_generate_reply(sender, subject, body):
    client = get_openai_client()

    product_key, product_context = get_product_context(subject)

    prompt = f"""
Du svarer som Ulla Vase.

Produktinfo:
{product_context}

Mail:
{body}

Svar kort, konkret og korrekt.
Hvis det handler om pris:
- brug de rigtige priser fra teksten
- skriv pr. bruger pr. måned
- skriv at der betales årligt
- tilføj link: https://vinterguide.dk/intro.html#priser

Start med:
Hej {extract_first_name(sender)},

Afslut med:
Mvh Ulla Vase
"""

    res = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt
    )

    return res.output_text.strip()

# =========================
# GEM SVAR
# =========================

def save_pending_reply(mail_id, sender, subject, body):
    reply = ai_generate_reply(sender, subject, body)

    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
        INSERT OR REPLACE INTO replies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            str(mail_id),
            datetime.utcnow().isoformat(),
            sender,
            "",
            "",
            "nej",
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
            SELECT *
            FROM replies
            WHERE status IN ('pending_approval', 'approved_api', 'send_failed')
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
            SELECT *
            FROM replies
            WHERE status IN ('sent', 'rejected', 'archived')
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

def format_dt(value):
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value)
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return value

# =========================
# FILTRERING
# =========================

def is_system_mail(sender, subject, body):
    sender_l = (sender or "").lower()
    subject_l = (subject or "").lower()
    body_l = (body or "").lower()

    patterns = [
        "no-reply", "noreply", "support@", "instagram", "facebook",
        "apple store", "order_acknowledgment", "verify your identity",
        "signin.aws", "bekræft videresendelse", "videresendelse af e-mails",
        "ordrenummer", "verification", "password reset", "reset password"
    ]

    haystack = f"{sender_l} {subject_l} {body_l}"
    return any(p in haystack for p in patterns)

# =========================
# SEND MAIL
# =========================

def send_via_resend(to_email, subject, draft_reply):
    resend.api_key = RESEND_API_KEY

    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    html = f"""
    <p>{draft_reply.replace(chr(10), "<br>")}</p>
    <br><br>
    <hr style="border:none;border-top:1px solid #ddd;">
    <table style="font-family: Arial, sans-serif; font-size:14px; color:#222;">
      <tr>
        <td style="padding-right:15px; vertical-align:top;">
          <a href="https://vweb.info" target="_blank">
            <img src="https://vweb.info/images/vweb-logo.svg" alt="Vweb logo" width="150">
          </a>
        </td>
        <td style="vertical-align:top;">
          <b>Ulla Vase</b><br>
          Syrenvej 5<br>
          7200 Grindsted<br>
          Tlf.: 91 83 07 25<br>
          E-mail: <a href="mailto:ulla@vweb.info">ulla@vweb.info</a>
        </td>
      </tr>
    </table>
    """

    return resend.Emails.send({
        "from": AI_FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "html": html
    })

# =========================
# LÆS MAILS
# =========================

def check_mail():
    if not MAIL_USER or not MAIL_PASS:
        raise ValueError("MAIL_USER eller MAIL_PASS mangler")

    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(MAIL_USER, MAIL_PASS)
    mail.select(MAILBOX)

    status, messages = mail.search(None, "ALL")
    if status != "OK":
        mail.logout()
        return

    mail_ids = messages[0].split()
    if not mail_ids:
        mail.logout()
        return

    recent_mail_ids = mail_ids[-150:]

    for mail_id in recent_mail_ids:
        mail_id_int = int(mail_id)

        if get_reply(mail_id_int):
            continue

        status, msg_data = mail.fetch(mail_id, "(RFC822)")
        if status != "OK":
            continue

        for response_part in msg_data:
            if not isinstance(response_part, tuple):
                continue

            msg = email.message_from_bytes(response_part[1])

            sender = decode_mime_text(msg.get("From"))
            subject = decode_mime_text(msg.get("Subject"))
            body = strip_quoted_text(get_plain_text_body(msg))

            if is_system_mail(sender, subject, body):
                with file_lock:
                    conn = get_db_connection()
                    cur = conn.cursor()
                    cur.execute("""
                        INSERT OR REPLACE INTO replies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        str(mail_id_int),
                        datetime.utcnow().isoformat(),
                        sender,
                        "",
                        "automatisk",
                        "nej",
                        subject,
                        "automatisk",
                        "nej",
                        "Systemmail / automatisk mail",
                        "intet",
                        body[:1000],
                        "archived",
                        None,
                        None,
                        0
                    ))
                    conn.commit()
                    conn.close()
                continue

            save_pending_reply(mail_id_int, sender, subject, body)

    mail.logout()

def polling_loop():
    while True:
        try:
            check_mail()
        except Exception as e:
            print("Fejl i polling_loop:", e)
        time.sleep(CHECK_INTERVAL_SECONDS)

# =========================
# HTML
# =========================

PAGE = """
<!doctype html>
<html lang="da">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="15">
  <title>Mailbot indbakke</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: Arial, sans-serif; margin: 0; background: #f3f4f6; color: #1f2937; }
    .wrap { max-width: 1500px; margin: 0 auto; padding: 20px; }
    h1 { margin: 0 0 8px 0; font-size: 42px; }
    .muted { color: #6b7280; margin-bottom: 20px; font-size: 18px; }
    .topbar { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 24px; }
    .summary {
      background: white; border-radius: 14px; padding: 14px 18px;
      border: 1px solid #d1d5db; font-size: 20px;
    }
    .layout { display: grid; grid-template-columns: 420px 1fr; gap: 18px; align-items: start; }
    .panel { background: white; border: 1px solid #d1d5db; border-radius: 16px; overflow: hidden; }
    .panel h2 { margin: 0; padding: 18px 20px; border-bottom: 1px solid #e5e7eb; font-size: 22px; }
    .mail-list { max-height: 75vh; overflow-y: auto; }
    .mail-item {
      display: block; text-decoration: none; color: inherit; padding: 16px 18px;
      border-bottom: 1px solid #eef2f7; background: white;
    }
    .mail-item:hover { background: #f8fafc; }
    .mail-item.active { background: #eef6ff; }
    .mail-row-top { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 8px; align-items: center; }
    .mail-from { font-weight: 700; font-size: 18px; }
    .mail-date { color: #6b7280; font-size: 14px; white-space: nowrap; }
    .mail-subject { font-weight: 600; margin-bottom: 6px; font-size: 16px; }
    .mail-preview {
      color: #4b5563; font-size: 14px; white-space: nowrap;
      overflow: hidden; text-overflow: ellipsis;
    }
    .badge {
      display: inline-block; padding: 4px 10px; border-radius: 999px;
      font-size: 12px; margin-right: 6px;
    }
    .badge-blue { background: #e0e7ff; color: #334155; }
    .badge-orange { background: #fff7ed; color: #9a3412; }
    .badge-green { background: #ecfdf5; color: #047857; }
    .detail { padding: 20px; }
    .detail h3 { margin-top: 0; font-size: 20px; }
    .meta { margin-bottom: 16px; line-height: 1.7; font-size: 16px; }
    .label { font-weight: 700; }
    pre {
      background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px;
      padding: 14px; white-space: pre-wrap; font-size: 16px; margin: 6px 0 18px 0;
    }
    textarea {
      width: 100%; min-height: 240px; padding: 14px; border-radius: 12px;
      border: 1px solid #cbd5e1; font-family: Arial, sans-serif; font-size: 16px;
      box-sizing: border-box; resize: vertical; background: #f8fafc; margin-top: 6px; margin-bottom: 10px;
    }
    .actions form { display: inline-block; margin-right: 8px; margin-top: 8px; }
    button {
      border: 0; border-radius: 10px; padding: 12px 16px; cursor: pointer;
      font-weight: 700; font-size: 16px;
    }
    .save { background: #0f766e; color: white; }
    .approve { background: #2563eb; color: white; }
    .reject { background: #dc2626; color: white; }
    .archive { background: #475569; color: white; }
    .send { background: #059669; color: white; }
    .retry { background: #ea580c; color: white; }
    .empty { padding: 20px; color: #64748b; }
    details { margin-top: 20px; }
    summary { cursor: pointer; font-weight: 700; padding: 14px 0; font-size: 20px; color: #111827; }
    .history-item {
      background: white; border: 1px solid #d1d5db; border-radius: 12px;
      padding: 14px 16px; margin-bottom: 10px;
    }
    .hint { color: #475569; font-size: 14px; }
  </style>
</head>
<body>
<div class="wrap">
  <h1>Mailbot indbakke</h1>
  <div class="muted">Kun aktive mails vises her. Behandlede mails forsvinder fra indbakken og kan ses i historik.</div>

  <div class="topbar">
    <div class="summary">Afventer: <strong>{{ pending_count }}</strong></div>
    <div class="summary">Klar til send: <strong>{{ approved_count }}</strong></div>
    <div class="summary">Sendt: <strong>{{ sent_count }}</strong></div>
    <div class="summary">Afvist: <strong>{{ rejected_count }}</strong></div>
    <div class="summary">Arkiveret: <strong>{{ archived_count }}</strong></div>
  </div>

  <div class="layout">
    <div class="panel">
      <h2>Indbakke</h2>
      <div class="mail-list">
        {% if active_items %}
          {% for item in active_items %}
            <a class="mail-item {% if selected_mail_id == item['mail_id'] %}active{% endif %}" href="{{ url_for('dashboard', selected=item['mail_id']) }}">
              <div class="mail-row-top">
                <div class="mail-from">{{ item['sender_name'] }}</div>
                <div class="mail-date">{{ item['saved_at_display'] }}</div>
              </div>
              <div style="margin-bottom:6px;">
                <span class="badge badge-blue">{{ item['category'] }}</span>
                <span class="badge badge-orange">{{ item['status'] }}</span>
                {% if item['seen'] == 0 %}
                  <span class="badge badge-green">NY</span>
                {% endif %}
              </div>
              <div class="mail-subject">{{ item['subject'] }}</div>
              <div class="mail-preview">{{ item['preview_line'] }}</div>
            </a>
          {% endfor %}
        {% else %}
          <div class="empty">Ingen aktive mails lige nu.</div>
        {% endif %}
      </div>
    </div>

    <div class="panel">
      <h2>Mail</h2>
      {% if selected_item %}
        <div class="detail">
          <h3>{{ selected_item['subject'] }}</h3>
          <div class="meta">
            <div><span class="label">Fra:</span> {{ selected_item['sender'] }}</div>
            <div><span class="label">Dato:</span> {{ selected_item['saved_at_display'] }}</div>
            <div><span class="label">Resumé:</span> {{ selected_item['summary'] }}</div>
          </div>

          <div class="label">Renset mailtekst:</div>
          <pre>{{ selected_item['original_preview'] }}</pre>

          <div class="label">Svarudkast:</div>
          <form method="post" action="{{ url_for('update_draft_route', mail_id=selected_item['mail_id']) }}">
            <textarea name="draft_reply">{{ selected_item['draft_reply'] }}</textarea>
            <div class="hint">Du kan rette teksten før du godkender eller sender.</div>
            <button class="save" type="submit">Gem ændringer</button>
          </form>

          {% if selected_item['send_error'] %}
            <div class="meta" style="margin-top:16px;"><span class="label">Sendefejl:</span> {{ selected_item['send_error'] }}</div>
          {% endif %}

          <div class="actions">
            {% if selected_item['status'] == 'pending_approval' %}
              <form method="post" action="{{ url_for('approve_reply', mail_id=selected_item['mail_id']) }}">
                <button class="approve" type="submit">Godkend til send</button>
              </form>
              <form method="post" action="{{ url_for('reject_reply', mail_id=selected_item['mail_id']) }}">
                <button class="reject" type="submit">Afvis</button>
              </form>
              <form method="post" action="{{ url_for('archive_reply', mail_id=selected_item['mail_id']) }}">
                <button class="archive" type="submit">Arkivér</button>
              </form>
            {% elif selected_item['status'] == 'approved_api' %}
              <form method="post" action="{{ url_for('send_reply', mail_id=selected_item['mail_id']) }}">
                <button class="send" type="submit">Send nu</button>
              </form>
              <form method="post" action="{{ url_for('reject_reply', mail_id=selected_item['mail_id']) }}">
                <button class="reject" type="submit">Afvis</button>
              </form>
              <form method="post" action="{{ url_for('archive_reply', mail_id=selected_item['mail_id']) }}">
                <button class="archive" type="submit">Arkivér</button>
              </form>
            {% elif selected_item['status'] == 'send_failed' %}
              <form method="post" action="{{ url_for('send_reply', mail_id=selected_item['mail_id']) }}">
                <button class="retry" type="submit">Prøv at sende igen</button>
              </form>
              <form method="post" action="{{ url_for('reject_reply', mail_id=selected_item['mail_id']) }}">
                <button class="reject" type="submit">Afvis</button>
              </form>
              <form method="post" action="{{ url_for('archive_reply', mail_id=selected_item['mail_id']) }}">
                <button class="archive" type="submit">Arkivér</button>
              </form>
            {% endif %}
          </div>
        </div>
      {% else %}
        <div class="empty">Vælg en mail i indbakken.</div>
      {% endif %}
    </div>
  </div>

  <details>
    <summary>Historik</summary>
    {% if history_items %}
      {% for item in history_items %}
        <div class="history-item">
          <div><strong>{{ item['saved_at_display'] }}</strong> — {{ item['sender_name'] }} — {{ item['subject'] }}</div>
          <div style="margin-top:6px;">
            <span class="badge badge-blue">{{ item['category'] }}</span>
            <span class="badge badge-orange">{{ item['status'] }}</span>
          </div>
          <div style="margin-top:8px; color:#4b5563;">{{ item['preview_line'] }}</div>
        </div>
      {% endfor %}
    {% else %}
      <div class="empty">Ingen historik endnu.</div>
    {% endif %}
  </details>
</div>
</body>
</html>
"""

@app.route("/")
def dashboard():
    active_items = load_replies_by_status(["pending_approval", "approved_api", "send_failed"])
    history_items = load_replies_by_status(["sent", "rejected", "archived"])
    selected_mail_id = request.args.get("selected")

    selected_item = None
    if active_items:
        if selected_mail_id:
            selected_item = next((item for item in active_items if str(item["mail_id"]) == str(selected_mail_id)), None)
        if not selected_item:
            selected_item = active_items[0]
        if selected_item:
            mark_as_seen(selected_item["mail_id"])

    counts = get_counts()

    return render_template_string(
        PAGE,
        active_items=active_items,
        history_items=history_items[:100],
        selected_item=selected_item,
        selected_mail_id=str(selected_item["mail_id"]) if selected_item else None,
        pending_count=counts["pending_approval"],
        approved_count=counts["approved_api"],
        sent_count=counts["sent"],
        rejected_count=counts["rejected"],
        archived_count=counts["archived"]
    )

@app.route("/update_draft/<mail_id>", methods=["POST"])
def update_draft_route(mail_id):
    new_text = request.form.get("draft_reply", "").strip()
    if new_text:
        update_reply_draft(mail_id, new_text)
    return redirect(url_for("dashboard", selected=mail_id))

@app.route("/approve/<mail_id>", methods=["POST"])
def approve_reply(mail_id):
    update_reply_status(mail_id, "approved_api")
    return redirect(url_for("dashboard"))

@app.route("/reject/<mail_id>", methods=["POST"])
def reject_reply(mail_id):
    update_reply_status(mail_id, "rejected")
    return redirect(url_for("dashboard"))

@app.route("/archive/<mail_id>", methods=["POST"])
def archive_reply(mail_id):
    update_reply_status(mail_id, "archived")
    return redirect(url_for("dashboard"))

@app.route("/send/<mail_id>", methods=["POST"])
def send_reply(mail_id):
    item = get_reply_by_id(mail_id)
    if not item:
        return redirect(url_for("dashboard"))
    if item.get("status") not in {"approved_api", "send_failed"}:
        return redirect(url_for("dashboard"))
    try:
        to_email = extract_reply_email(item["sender"])
        if not to_email:
            raise ValueError("Kunne ikke udlede modtagerens mailadresse")
        send_via_resend(
            to_email=to_email,
            original_subject=item["subject"],
            draft_reply=item["draft_reply"]
        )
        update_reply_status(
            mail_id=mail_id,
            new_status="sent",
            send_error=None,
            sent_at=datetime.utcnow().isoformat() + "Z"
        )
    except Exception as e:
        update_reply_status(
            mail_id=mail_id,
            new_status="send_failed",
            send_error=str(e),
            sent_at=None
        )
    return redirect(url_for("dashboard"))

if __name__ == "__main__":
    init_db()
    ensure_replies_columns()
    worker = threading.Thread(target=polling_loop, daemon=True)
    worker.start()
    app.run(host="0.0.0.0", port=PORT)
