import imaplib
import email
import os
import time
import json
import threading
from datetime import datetime
from email.header import decode_header

import resend
from openai import OpenAI
from bs4 import BeautifulSoup
from flask import Flask, redirect, render_template_string, url_for

print("APP STARTER NU!!!")

IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.one.com")
MAILBOX = os.getenv("MAILBOX", "INBOX")
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))

STATE_FILE = "last_mail_id.txt"
PENDING_REPLIES_FILE = "pending_replies.json"
PORT = int(os.getenv("PORT", "8080"))

REPLY_CATEGORIES = {"kunde", "vigtig", "ukendt"}

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
AI_FROM_EMAIL = os.getenv("AI_FROM_EMAIL")

file_lock = threading.Lock()
app = Flask(__name__)

HTML_TEMPLATE = """
<!doctype html>
<html lang="da">
<head>
  <meta charset="utf-8">
  <title>Mailbot godkendelse</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; background: #f6f7f9; color: #222; }
    h1 { margin-bottom: 8px; }
    .muted { color: #666; margin-bottom: 20px; }
    .card {
      background: white;
      border: 1px solid #ddd;
      border-radius: 10px;
      padding: 16px;
      margin-bottom: 16px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    }
    .row { margin-bottom: 8px; }
    .label { font-weight: bold; }
    .badge {
      display: inline-block;
      padding: 4px 8px;
      border-radius: 999px;
      font-size: 12px;
      background: #eef2ff;
      color: #334155;
      margin-left: 6px;
    }
    .status-pending_approval { background: #fff7ed; color: #9a3412; }
    .status-approved_api { background: #eff6ff; color: #1d4ed8; }
    .status-sent { background: #ecfdf5; color: #047857; }
    .status-rejected { background: #fef2f2; color: #b91c1c; }
    .status-archived { background: #f1f5f9; color: #475569; }
    .status-send_failed { background: #fef2f2; color: #b91c1c; }

    .actions form { display: inline-block; margin-right: 8px; margin-top: 10px; }

    button {
      border: 0;
      border-radius: 8px;
      padding: 10px 14px;
      cursor: pointer;
      font-weight: bold;
    }

    .approve { background: #2563eb; color: white; }
    .reject { background: #dc2626; color: white; }
    .archive { background: #475569; color: white; }
    .send { background: #059669; color: white; }
    .retry { background: #ea580c; color: white; }
    .disabled { background: #94a3b8; color: white; cursor: not-allowed; }

    pre {
      background: #f8fafc;
      padding: 12px;
      border-radius: 8px;
      white-space: pre-wrap;
      border: 1px solid #e2e8f0;
      overflow-x: auto;
      margin-top: 6px;
    }

    .topbar {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 16px;
    }

    .summary {
      background: white;
      border-radius: 10px;
      padding: 12px 16px;
      border: 1px solid #ddd;
    }

    .copybox {
      background: #f8fafc;
      border: 1px dashed #94a3b8;
      border-radius: 8px;
      padding: 10px;
      font-size: 14px;
      color: #334155;
      margin-top: 8px;
    }
  </style>
</head>
<body>
  <h1>Mailbot godkendelse</h1>
  <div class="muted">Denne version sender ikke noget uden manuel handling. Godkend først, send bagefter.</div>

  <div class="topbar">
    <div class="summary">Afventer: <strong>{{ pending_count }}</strong></div>
    <div class="summary">Godkendt til API-send: <strong>{{ approved_count }}</strong></div>
    <div class="summary">Sendt: <strong>{{ sent_count }}</strong></div>
    <div class="summary">Afvist: <strong>{{ rejected_count }}</strong></div>
    <div class="summary">Arkiveret: <strong>{{ archived_count }}</strong></div>
  </div>

  {% if items %}
    {% for item in items %}
      <div class="card">
        <div class="row">
          <span class="label">Mail ID:</span> {{ item.mail_id }}
          <span class="badge">{{ item.category }}</span>
          <span class="badge status-{{ item.status }}">{{ item.status }}</span>
        </div>

        <div class="row"><span class="label">Fra:</span> {{ item.sender }}</div>
        <div class="row"><span class="label">Emne:</span> {{ item.subject }}</div>
        <div class="row"><span class="label">Kræver svar:</span> {{ item.requires_reply }}</div>
        <div class="row"><span class="label">Resumé:</span> {{ item.summary }}</div>

        <div class="row"><span class="label">Original preview:</span></div>
        <pre>{{ item.original_preview }}</pre>

        <div class="row"><span class="label">Svarudkast:</span></div>
        <pre>{{ item.draft_reply }}</pre>

        {% if item.sent_at %}
          <div class="row"><span class="label">Sendt:</span> {{ item.sent_at }}</div>
        {% endif %}

        {% if item.send_error %}
          <div class="row"><span class="label">Sendefejl:</span> {{ item.send_error }}</div>
        {% endif %}

        {% if item.status == "approved_api" %}
          <div class="copybox">
            Dette svar er godkendt og klar til API-afsendelse.
          </div>
        {% endif %}

        <div class="actions">
          {% if item.status == "pending_approval" %}
            <form method="post" action="{{ url_for('approve_reply', mail_id=item.mail_id) }}">
              <button class="approve" type="submit">Godkend til send</button>
            </form>
            <form method="post" action="{{ url_for('reject_reply', mail_id=item.mail_id) }}">
              <button class="reject" type="submit">Afvis</button>
            </form>
            <form method="post" action="{{ url_for('archive_reply', mail_id=item.mail_id) }}">
              <button class="archive" type="submit">Arkivér</button>
            </form>
          {% elif item.status == "approved_api" %}
            <form method="post" action="{{ url_for('send_reply', mail_id=item.mail_id) }}">
              <button class="send" type="submit">Send nu</button>
            </form>
            <form method="post" action="{{ url_for('reject_reply', mail_id=item.mail_id) }}">
              <button class="reject" type="submit">Afvis</button>
            </form>
            <form method="post" action="{{ url_for('archive_reply', mail_id=item.mail_id) }}">
              <button class="archive" type="submit">Arkivér</button>
            </form>
          {% elif item.status == "send_failed" %}
            <form method="post" action="{{ url_for('send_reply', mail_id=item.mail_id) }}">
              <button class="retry" type="submit">Prøv at sende igen</button>
            </form>
            <form method="post" action="{{ url_for('reject_reply', mail_id=item.mail_id) }}">
              <button class="reject" type="submit">Afvis</button>
            </form>
          {% elif item.status == "sent" %}
            <button class="disabled" disabled>Mail sendt</button>
          {% elif item.status == "rejected" %}
            <button class="disabled" disabled>Afvist</button>
          {% elif item.status == "archived" %}
            <button class="disabled" disabled>Arkiveret</button>
          {% endif %}
        </div>
      </div>
    {% endfor %}
  {% else %}
    <div class="card">Ingen svarudkast endnu.</div>
  {% endif %}
</body>
</html>
"""


def decode_mime_text(value):
    if not value:
        return "(intet emne)"

    parts = decode_header(value)
    result = []

    for part, charset in parts:
        if isinstance(part, bytes):
            try:
                result.append(part.decode(charset or "utf-8", errors="replace"))
            except Exception:
                result.append(part.decode("utf-8", errors="replace"))
        else:
            result.append(part)

    text = "".join(result).strip()
    return text if text else "(intet emne)"


def clean_text(text):
    if not text:
        return "(intet indhold)"

    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    cleaned = "\n".join(lines).strip()
    return cleaned if cleaned else "(intet indhold)"


def html_to_text(html):
    if not html:
        return "(intet indhold)"

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "head", "title", "meta", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    return clean_text(text)


def get_plain_text_body(msg):
    if msg.is_multipart():
        plain_body = None
        html_body = None

        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition") or "").lower()

            if "attachment" in content_disposition:
                continue

            payload = part.get_payload(decode=True)
            if not payload:
                continue

            charset = part.get_content_charset() or "utf-8"

            try:
                decoded = payload.decode(charset, errors="replace")
            except Exception:
                decoded = payload.decode("utf-8", errors="replace")

            if content_type == "text/plain" and not plain_body:
                plain_body = clean_text(decoded)
            elif content_type == "text/html" and not html_body:
                html_body = html_to_text(decoded)

        if plain_body:
            return plain_body
        if html_body:
            return html_body
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except Exception:
                decoded = payload.decode("utf-8", errors="replace")

            if msg.get_content_type() == "text/html":
                return html_to_text(decoded)

            return clean_text(decoded)

    return "(intet indhold)"


def get_last_mail_id():
    with file_lock:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return int(f.read().strip())
        except Exception:
            return None


def save_last_mail_id(mail_id):
    with file_lock:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            f.write(str(mail_id))


def load_pending_replies():
    with file_lock:
        try:
            with open(PENDING_REPLIES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []


def save_pending_replies(data):
    with file_lock:
        with open(PENDING_REPLIES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def already_saved_reply(mail_id):
    pending = load_pending_replies()
    return any(str(item.get("mail_id")) == str(mail_id) for item in pending)


def save_pending_reply(mail_id, sender, subject, category, summary, reply_needed, draft_reply, original_preview):
    if already_saved_reply(mail_id):
        return

    pending = load_pending_replies()

    pending.append(
        {
            "saved_at": datetime.utcnow().isoformat() + "Z",
            "mail_id": str(mail_id),
            "sender": sender,
            "subject": subject,
            "category": category,
            "requires_reply": reply_needed,
            "summary": summary,
            "draft_reply": draft_reply,
            "original_preview": original_preview[:1500],
            "status": "pending_approval",
            "sent_at": None,
            "send_error": None
        }
    )

    save_pending_replies(pending)


def update_reply_status(mail_id, new_status, send_error=None, sent_at=None):
    items = load_pending_replies()
    updated = False

    for item in items:
        if str(item.get("mail_id")) == str(mail_id):
            item["status"] = new_status
            item["send_error"] = send_error
            if sent_at:
                item["sent_at"] = sent_at
            updated = True
            break

    if updated:
        save_pending_replies(items)

    return updated


def get_reply_by_id(mail_id):
    items = load_pending_replies()
    for item in items:
        if str(item.get("mail_id")) == str(mail_id):
            return item
    return None


def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    print("OPENAI_API_KEY fundet:", bool(api_key))

    if not api_key:
        raise ValueError("OPENAI_API_KEY mangler i Railway Variables")

    return OpenAI(api_key=api_key)


def parse_ai_result(ai_text):
    result = {
        "category": "ukendt",
        "requires_reply": "nej",
        "summary": "",
        "draft_reply": "intet"
    }

    for line in ai_text.splitlines():
        line = line.strip()

        if line.upper().startswith("KATEGORI:"):
            result["category"] = line.split(":", 1)[1].strip().lower()
        elif line.upper().startswith("KRÆVER_SVAR:"):
            result["requires_reply"] = line.split(":", 1)[1].strip().lower()
        elif line.upper().startswith("RESUMÉ:"):
            result["summary"] = line.split(":", 1)[1].strip()
        elif line.upper().startswith("SVARUDKAST:"):
            result["draft_reply"] = line.split(":", 1)[1].strip()

    return result


def ai_analyze_email(sender, subject, body):
    client = get_openai_client()
    body_preview = body[:5000] if body else "(intet indhold)"

    prompt = f"""
Du er en skarp mailassistent for en mindre dansk virksomhed.

Vigtige regler:
- Du må gerne klassificere hårdt og ærligt.
- "no-reply", betalingsfejl, verifikationsmails, systemmails og abonnementsmails er typisk "automatisk".
- Nyhedsbreve, kampagner og information uden reel dialog er typisk "nyhedsbrev".
- Spam og åbenlyst irrelevant indhold er "spam".
- Kundemails og menneskelige henvendelser er "kunde" eller "vigtig".
- Svarudkast skal KUN laves hvis mailen reelt kræver svar.
- Svarudkast må ikke lyde som AI.
- Svarudkast skal være kort, naturligt og direkte.
- Ingen punktopstilling i selve svarudkastet.
- Hvis der ikke skal svares, skriv "intet".

Returnér KUN i dette format:

KATEGORI: <spam|nyhedsbrev|automatisk|kunde|vigtig|ukendt>
KRÆVER_SVAR: <ja|nej>
RESUMÉ: <kort opsummering>
SVARUDKAST: <kort svar på dansk, eller skriv "intet">

Afsender:
{sender}

Emne:
{subject}

Mailindhold:
{body_preview}
""".strip()

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt
    )

    return response.output_text.strip()


def send_via_resend(to_email, original_subject, draft_reply):
    if not RESEND_API_KEY:
        raise ValueError("RESEND_API_KEY mangler i Railway Variables")

    if not AI_FROM_EMAIL:
        raise ValueError("AI_FROM_EMAIL mangler i Railway Variables")

    resend.api_key = RESEND_API_KEY

    if original_subject.lower().startswith("re:"):
        subject = original_subject
    else:
        subject = f"Re: {original_subject}"

    html = f"<p>{draft_reply.replace(chr(10), '<br>')}</p>"

    params = {
        "from": AI_FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "html": html
    }

    result = resend.Emails.send(params)
    return result


def extract_reply_email(sender):
    parsed = email.utils.parseaddr(sender)
    return parsed[1]


def check_mail():
    print("Mail-bot starter...")
    print("Tjekker mail...")

    email_user = os.getenv("MAIL_USER")
    email_pass = os.getenv("MAIL_PASS")

    print("MAIL_USER fundet:", bool(email_user))
    print("MAIL_PASS fundet:", bool(email_pass))

    if not email_user or not email_pass:
        raise ValueError("MAIL_USER eller MAIL_PASS mangler i Railway Variables")

    last_seen = get_last_mail_id()

    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(email_user, email_pass)
    mail.select(MAILBOX)

    status, messages = mail.search(None, "ALL")
    if status != "OK":
        print("Kunne ikke hente mails")
        mail.logout()
        return

    mail_ids = messages[0].split()

    if not mail_ids:
        print("Ingen mails fundet")
        mail.logout()
        return

    newest_id = int(mail_ids[-1])
    print(f"Nyeste mail ID: {newest_id}")

    if last_seen is None:
        print("Ingen tidligere state fundet. Springer gamle mails over første gang.")
        save_last_mail_id(newest_id)
        mail.logout()
        return

    print(f"Sidst behandlet ID: {last_seen}")

    for mail_id in mail_ids:
        mail_id_int = int(mail_id)

        if mail_id_int <= last_seen:
            continue

        status, msg_data = mail.fetch(mail_id, "(RFC822)")
        if status != "OK":
            print(f"Kunne ikke hente mail {mail_id_int}")
            continue

        for response_part in msg_data:
            if not isinstance(response_part, tuple):
                continue

            msg = email.message_from_bytes(response_part[1])

            sender = decode_mime_text(msg.get("From"))
            subject = decode_mime_text(msg.get("Subject"))
            body = get_plain_text_body(msg)

            print("========================================")
            print(f"NY MAIL (ID {mail_id_int})")
            print(f"Fra: {sender}")
            print(f"Emne: {subject}")
            print("Indhold preview:")
            print(body[:600] if body else "(intet indhold)")
            print("----------------------------------------")
            print("AI analyserer mailen...")

            try:
                ai_result = ai_analyze_email(sender, subject, body)
                parsed = parse_ai_result(ai_result)

                print("AI RESULTAT:")
                print("=================================")
                print(ai_result)
                print("=================================")

                category = parsed["category"]
                requires_reply = parsed["requires_reply"]
                summary = parsed["summary"]
                draft_reply = parsed["draft_reply"]

                if category in REPLY_CATEGORIES and requires_reply == "ja" and draft_reply.lower() != "intet":
                    save_pending_reply(
                        mail_id=mail_id_int,
                        sender=sender,
                        subject=subject,
                        category=category,
                        summary=summary,
                        reply_needed=requires_reply,
                        draft_reply=draft_reply,
                        original_preview=body
                    )
                    print("SVARUDKAST GEMT TIL GODKENDELSE")
                else:
                    print("INGEN SVAR GEMT")

            except Exception as ai_error:
                print(f"AI-fejl: {ai_error}")

            print("========================================")

    save_last_mail_id(newest_id)
    mail.logout()


def polling_loop():
    while True:
        try:
            check_mail()
        except Exception as e:
            print(f"Fejl: {e}")

        print(f"Venter {CHECK_INTERVAL_SECONDS} sekunder...")
        time.sleep(CHECK_INTERVAL_SECONDS)


@app.route("/")
def dashboard():
    items = load_pending_replies()
    items = sorted(items, key=lambda x: (x.get("status") != "pending_approval", x.get("saved_at", "")))

    pending_count = sum(1 for i in items if i.get("status") == "pending_approval")
    approved_count = sum(1 for i in items if i.get("status") == "approved_api")
    sent_count = sum(1 for i in items if i.get("status") == "sent")
    rejected_count = sum(1 for i in items if i.get("status") == "rejected")
    archived_count = sum(1 for i in items if i.get("status") == "archived")

    return render_template_string(
        HTML_TEMPLATE,
        items=items,
        pending_count=pending_count,
        approved_count=approved_count,
        sent_count=sent_count,
        rejected_count=rejected_count,
        archived_count=archived_count
    )


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

        result = send_via_resend(
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

        print("RESEND RESULT:", result)

    except Exception as e:
        update_reply_status(
            mail_id=mail_id,
            new_status="send_failed",
            send_error=str(e),
            sent_at=None
        )

    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    print("KALDER CHECK_MAIL / STARTER WEB")
    worker = threading.Thread(target=polling_loop, daemon=True)
    worker.start()
    app.run(host="0.0.0.0", port=PORT)
