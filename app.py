import imaplib
import email
import os
import time
import sqlite3
import threading
import re
from pathlib import Path
from datetime import datetime, timedelta
from email.header import decode_header

import resend
from openai import OpenAI
from bs4 import BeautifulSoup
from flask import Flask, redirect, render_template_string, url_for, request

print("APP STARTER NU!!!")

IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.one.com")
MAILBOX = os.getenv("MAILBOX", "INBOX")
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))
PORT = int(os.getenv("PORT", "8080"))
DB_PATH = os.getenv("DB_PATH", "mailbot.db")

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
AI_FROM_EMAIL = os.getenv("AI_FROM_EMAIL")
MAIL_USER = os.getenv("MAIL_USER", "")

COMPANY_CONTEXT_FILE = os.getenv("COMPANY_CONTEXT_FILE", "company_context.txt")
PRODUCT_VINTERGUIDE_FILE = os.getenv("PRODUCT_VINTERGUIDE_FILE", "product_vinterguide.txt")
PRODUCT_SLUSHBOOK_FILE = os.getenv("PRODUCT_SLUSHBOOK_FILE", "product_slushbook.txt")

REPLY_CATEGORIES = {"kunde", "vigtig", "ukendt"}
file_lock = threading.Lock()
app = Flask(__name__)

HTML_TEMPLATE = """
<!doctype html>
<html lang="da">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="12">
  <title>Mailbot indbakke</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: Arial, sans-serif; margin: 0; background: #f6f7f9; color: #222; }
    .wrap { max-width: 1500px; margin: 0 auto; padding: 20px; }
    h1 { margin: 0 0 8px 0; }
    .muted { color: #666; margin-bottom: 18px; }
    .topbar { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 18px; }
    .summary { background: white; border-radius: 10px; padding: 12px 16px; border: 1px solid #ddd; }
    .layout { display: grid; grid-template-columns: 420px 1fr; gap: 18px; align-items: start; }
    .panel { background: white; border: 1px solid #ddd; border-radius: 14px; overflow: hidden; }
    .panel h2 { margin: 0; padding: 16px 18px; border-bottom: 1px solid #e5e7eb; }
    .mail-list { max-height: 76vh; overflow-y: auto; }
    .mail-item { display:block; text-decoration:none; color:inherit; padding:14px 16px; border-bottom:1px solid #edf2f7; }
    .mail-item:hover { background:#f8fafc; }
    .mail-item.active { background:#eef6ff; }
    .mail-row-top { display:flex; justify-content:space-between; gap:10px; margin-bottom:6px; }
    .mail-from { font-weight:bold; }
    .mail-date { color:#6b7280; font-size:13px; white-space:nowrap; }
    .mail-subject { font-weight:600; margin-bottom:5px; }
    .mail-preview { color:#4b5563; font-size:14px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .detail { padding: 18px; }
    .row { margin-bottom: 8px; }
    .label { font-weight: bold; }
    .badge {
      display: inline-block; padding: 4px 8px; border-radius: 999px;
      font-size: 12px; background: #eef2ff; color: #334155; margin-right: 6px;
    }
    .badge-new { background:#ecfdf5; color:#047857; }
    .status-pending_approval { background: #fff7ed; color: #9a3412; }
    .status-approved_api { background: #eff6ff; color: #1d4ed8; }
    .status-sent { background: #ecfdf5; color: #047857; }
    .status-rejected { background: #fef2f2; color: #b91c1c; }
    .status-archived { background: #f1f5f9; color: #475569; }
    .status-send_failed { background: #fef2f2; color: #b91c1c; }
    .actions form { display: inline-block; margin-right: 8px; margin-top: 10px; vertical-align: top; }
    button { border: 0; border-radius: 8px; padding: 10px 14px; cursor: pointer; font-weight: bold; }
    .approve { background: #2563eb; color: white; }
    .reject { background: #dc2626; color: white; }
    .archive { background: #475569; color: white; }
    .send { background: #059669; color: white; }
    .retry { background: #ea580c; color: white; }
    .save { background: #0f766e; color: white; }
    pre { background: #f8fafc; padding: 12px; border-radius: 8px; white-space: pre-wrap; border: 1px solid #e2e8f0; overflow-x: auto; margin-top: 6px; }
    textarea { width: 100%; min-height: 180px; padding: 12px; border-radius: 8px; border: 1px solid #cbd5e1; font-family: Arial, sans-serif; font-size: 16px; box-sizing: border-box; resize: vertical; background: #f8fafc; }
    .empty { padding:16px; color:#64748b; }
    details { margin-top: 18px; }
    summary { cursor:pointer; font-weight:bold; margin-bottom:10px; }
    .history-item { background:white; border:1px solid #ddd; border-radius:10px; padding:12px 14px; margin-bottom:10px; }
  </style>
</head>
<body>
<div class="wrap">
  <h1>Mailbot indbakke</h1>
  <div class="muted">Kun mails der kræver handling vises i indbakken. Behandlede mails ligger i historik.</div>

  <div class="topbar">
    <div class="summary">Afventer: <strong>{{ pending_count }}</strong></div>
    <div class="summary">Godkendt til send: <strong>{{ approved_count }}</strong></div>
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
            <a class="mail-item {% if selected_mail_id == item.mail_id %}active{% endif %}" href="{{ url_for('dashboard', selected=item.mail_id) }}">
              <div class="mail-row-top">
                <div class="mail-from">{{ item.sender_name }}</div>
                <div class="mail-date">{{ item.saved_at_display }}</div>
              </div>
              <div style="margin-bottom:6px;">
                <span class="badge">{{ item.category }}</span>
                <span class="badge status-{{ item.status }}">{{ item.status }}</span>
                {% if item.seen == 0 %}
                  <span class="badge badge-new">NY</span>
                {% endif %}
              </div>
              <div class="mail-subject">{{ item.subject }}</div>
              <div class="mail-preview">{{ item.preview_line }}</div>
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
          <div class="row"><span class="label">Fra:</span> {{ selected_item.sender }}</div>
          <div class="row"><span class="label">Til:</span> {{ selected_item.recipient }}</div>
          <div class="row"><span class="label">Dato:</span> {{ selected_item.saved_at_display }}</div>
          <div class="row"><span class="label">Produktkontekst:</span> {{ selected_item.product_context }}</div>
          <div class="row"><span class="label">Emne:</span> {{ selected_item.subject }}</div>
          <div class="row"><span class="label">Kræver svar:</span> {{ selected_item.requires_reply }}</div>
          <div class="row"><span class="label">Resumé:</span> {{ selected_item.summary }}</div>

          <div class="row"><span class="label">Renset mailtekst:</span></div>
          <pre>{{ selected_item.original_preview }}</pre>

          <div class="row"><span class="label">Svarudkast:</span></div>
          <form method="post" action="{{ url_for('update_draft', mail_id=selected_item.mail_id) }}">
            <textarea name="draft_reply">{{ selected_item.draft_reply }}</textarea>
            <button class="save" type="submit">Gem ændringer</button>
          </form>

          {% if selected_item.send_error %}
            <div class="row"><span class="label">Sendefejl:</span> {{ selected_item.send_error }}</div>
          {% endif %}

          <div class="actions">
            {% if selected_item.status == "pending_approval" %}
              <form method="post" action="{{ url_for('approve_reply', mail_id=selected_item.mail_id) }}"><button class="approve" type="submit">Godkend til send</button></form>
              <form method="post" action="{{ url_for('reject_reply', mail_id=selected_item.mail_id) }}"><button class="reject" type="submit">Afvis</button></form>
              <form method="post" action="{{ url_for('archive_reply', mail_id=selected_item.mail_id) }}"><button class="archive" type="submit">Arkivér</button></form>
            {% elif selected_item.status == "approved_api" %}
              <form method="post" action="{{ url_for('send_reply', mail_id=selected_item.mail_id) }}"><button class="send" type="submit">Send nu</button></form>
              <form method="post" action="{{ url_for('reject_reply', mail_id=selected_item.mail_id) }}"><button class="reject" type="submit">Afvis</button></form>
              <form method="post" action="{{ url_for('archive_reply', mail_id=selected_item.mail_id) }}"><button class="archive" type="submit">Arkivér</button></form>
            {% elif selected_item.status == "send_failed" %}
              <form method="post" action="{{ url_for('send_reply', mail_id=selected_item.mail_id) }}"><button class="retry" type="submit">Prøv at sende igen</button></form>
              <form method="post" action="{{ url_for('reject_reply', mail_id=selected_item.mail_id) }}"><button class="reject" type="submit">Afvis</button></form>
              <form method="post" action="{{ url_for('archive_reply', mail_id=selected_item.mail_id) }}"><button class="archive" type="submit">Arkivér</button></form>
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
          <div><strong>{{ item.saved_at_display }}</strong> — {{ item.sender_name }} — {{ item.subject }}</div>
          <div style="margin-top:6px;">
            <span class="badge">{{ item.category }}</span>
            <span class="badge status-{{ item.status }}">{{ item.status }}</span>
          </div>
          <div style="margin-top:6px; color:#4b5563;">{{ item.preview_line }}</div>
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
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mail_id TEXT UNIQUE,
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
        if "seen" not in columns:
            cur.execute("ALTER TABLE replies ADD COLUMN seen INTEGER DEFAULT 0")

        conn.commit()
        conn.close()

def read_text_file(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except Exception:
        return ""

def get_company_context():
    return read_text_file(COMPANY_CONTEXT_FILE)

def get_product_context(recipient, subject, body):
    recipient_l = (recipient or "").lower()
    subject_l = (subject or "").lower()
    body_l = (body or "").lower()

    if "@vinterguide.dk" in recipient_l or "vinterguide" in recipient_l or "vinterguide" in subject_l or "vinterguide" in body_l:
        return "vinterguide", read_text_file(PRODUCT_VINTERGUIDE_FILE)

    if "@slushbook" in recipient_l or "slushbook" in recipient_l or "slushbook" in subject_l or "slushbook" in body_l:
        return "slushbook", read_text_file(PRODUCT_SLUSHBOOK_FILE)

    return "vweb", ""

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

    lines = [line.rstrip() for line in text.splitlines()]
    lines = [line for line in lines if line.strip()]
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

def strip_quoted_text(text):
    if not text:
        return "(intet indhold)"

    lines = text.splitlines()
    cleaned_lines = []

    break_patterns = [
        r"^Den .+ skrev",
        r"^On .+ wrote:$",
        r"^Fra:$",
        r"^Fra:",
        r"^From:$",
        r"^From:",
        r"^Sendt:$",
        r"^Sendt:",
        r"^Sent:$",
        r"^Sent:",
        r"^Til:$",
        r"^Til:",
        r"^To:$",
        r"^To:",
        r"^Emne:$",
        r"^Emne:",
        r"^Subject:$",
        r"^Subject:",
        r"^Start på videresendt besked:",
        r"^Forwarded message",
        r"^[-_]{5,}$",
    ]

    signature_patterns = [
        r"^Mvh\b",
        r"^Med venlig hilsen\b",
        r"^Venlig hilsen\b",
        r"^Best regards\b",
        r"^Kind regards\b",
        r"^Ulla Vase\b",
        r"^Syrenvej 5\b",
        r"^7200 Grindsted\b",
        r"^Tlf\.:",
        r"^E-mail:",
        r"^https?://",
        r"^<https?://",
    ]

    for raw_line in lines:
        line = raw_line.strip()

        if not line:
            if cleaned_lines:
                cleaned_lines.append("")
            continue

        if line.startswith(">"):
            break

        if any(re.match(pattern, line, flags=re.IGNORECASE) for pattern in break_patterns):
            break

        if any(re.match(pattern, line, flags=re.IGNORECASE) for pattern in signature_patterns):
            break

        cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned if cleaned else "(intet indhold)"

def extract_first_name(sender):
    name, addr = email.utils.parseaddr(sender or "")
    source = name.strip() or addr.split("@")[0].strip()

    if not source:
        return "der"

    source = re.sub(r'[\"<>]', "", source).strip()
    parts = source.split()
    if not parts:
        return "der"

    first = parts[0].strip(" ,.-")
    return first if first else "der"

def extract_sender_name(sender):
    name, addr = email.utils.parseaddr(sender or "")
    return name.strip() or addr or sender or "(ukendt)"

def format_display_datetime(value):
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return value

def preview_line(text):
    if not text:
        return ""
    first = text.splitlines()[0].strip()
    return first[:120]

def next_weekday_date(target_weekday: int) -> str:
    today = datetime.now().date()
    days_ahead = target_weekday - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    target = today + timedelta(days=days_ahead)
    return target.strftime("%d.%m.%Y")

def build_date_hint(body: str) -> str:
    lower = (body or "").lower()
    mapping = {
        "mandag i næste uge": 0,
        "tirsdag i næste uge": 1,
        "onsdag i næste uge": 2,
        "torsdag i næste uge": 3,
        "fredag i næste uge": 4,
        "lørdag i næste uge": 5,
        "søndag i næste uge": 6,
    }
    for phrase, weekday in mapping.items():
        if phrase in lower:
            day_name = phrase.split(" i næste uge")[0].capitalize()
            return f"{day_name} i næste uge er den {next_weekday_date(weekday)}."
    return ""

def load_replies_by_status(statuses):
    placeholders = ",".join("?" for _ in statuses)
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(f"""
            SELECT mail_id, saved_at, sender, recipient, product_context, subject, category, requires_reply,
                   summary, draft_reply, original_preview, status, sent_at, send_error, seen
            FROM replies
            WHERE status IN ({placeholders})
            ORDER BY datetime(COALESCE(sent_at, saved_at)) DESC
        """, tuple(statuses))
        rows = [dict(row) for row in cur.fetchall()]
        conn.close()

    for row in rows:
        row["sender_name"] = extract_sender_name(row.get("sender", ""))
        row["saved_at_display"] = format_display_datetime(row.get("saved_at"))
        row["preview_line"] = preview_line(row.get("original_preview", ""))
        if row.get("seen") is None:
            row["seen"] = 0
    return rows

def already_saved_reply(mail_id):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM replies WHERE mail_id = ?", (str(mail_id),))
        row = cur.fetchone()
        conn.close()
        return row is not None

def save_pending_reply(mail_id, sender, recipient, product_context, subject, category, summary, reply_needed, draft_reply, original_preview, status="pending_approval"):
    if already_saved_reply(mail_id):
        return

    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO replies (
                mail_id, saved_at, sender, recipient, product_context, subject, category, requires_reply,
                summary, draft_reply, original_preview, status, sent_at, send_error, seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(mail_id),
            datetime.utcnow().isoformat() + "Z",
            sender,
            recipient,
            product_context,
            subject,
            category,
            reply_needed,
            summary,
            draft_reply,
            original_preview[:4000],
            status,
            None,
            None,
            0
        ))
        conn.commit()
        conn.close()

def update_reply_status(mail_id, new_status, send_error=None, sent_at=None):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE replies
            SET status = ?, send_error = ?, sent_at = ?, seen = 1
            WHERE mail_id = ?
        """, (new_status, send_error, sent_at, str(mail_id)))
        conn.commit()
        conn.close()

def mark_as_seen(mail_id):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE replies SET seen = 1 WHERE mail_id = ?", (str(mail_id),))
        conn.commit()
        conn.close()

def get_reply_by_id(mail_id):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT mail_id, saved_at, sender, recipient, product_context, subject, category, requires_reply,
                   summary, draft_reply, original_preview, status, sent_at, send_error, seen
            FROM replies
            WHERE mail_id = ?
        """, (str(mail_id),))
        row = cur.fetchone()
        conn.close()

    if row:
        item = dict(row)
        item["sender_name"] = extract_sender_name(item.get("sender", ""))
        item["saved_at_display"] = format_display_datetime(item.get("saved_at"))
        item["preview_line"] = preview_line(item.get("original_preview", ""))
        if item.get("seen") is None:
            item["seen"] = 0
        return item
    return None

def update_reply_draft(mail_id, new_text):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE replies SET draft_reply = ?, seen = 1 WHERE mail_id = ?", (new_text, str(mail_id)))
        conn.commit()
        conn.close()

def get_counts():
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        counts = {}
        for status in ["pending_approval", "approved_api", "sent", "rejected", "archived"]:
            cur.execute("SELECT COUNT(*) AS c FROM replies WHERE status = ?", (status,))
            counts[status] = cur.fetchone()["c"]
        conn.close()
        return counts

def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY mangler i Railway Variables")
    return OpenAI(api_key=api_key)

def parse_ai_result(ai_text):
    result = {
        "category": "ukendt",
        "requires_reply": "nej",
        "summary": "",
        "draft_reply": ""
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

def normalize_draft_reply(draft_reply, sender, product_key=""):
    text = (draft_reply or "").strip()
    first_name = extract_first_name(sender)

    replacements = {
        "Mvh [Dit navn]": "Mvh Ulla Vase",
        "Mvh [dit navn]": "Mvh Ulla Vase",
        "Med venlig hilsen [Dit navn]": "Mvh Ulla Vase",
        "Med venlig hilsen [dit navn]": "Mvh Ulla Vase",
        "[Dit navn]": "Ulla Vase",
        "[dit navn]": "Ulla Vase",
        "Hej Ulla,": f"Hej {first_name},",
        "Hej ulla,": f"Hej {first_name},",
        "Hej Mailbot,": f"Hej {first_name},",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    if text:
        text = re.sub(r"^Hej\s+[^,\n]+,", f"Hej {first_name},", text, count=1, flags=re.IGNORECASE)

    if text.startswith(f"Hej {first_name},") and "\n\n" not in text:
        text = text.replace(f"Hej {first_name},", f"Hej {first_name},\n\n", 1)

    if text and "Mvh Ulla Vase" not in text:
        text = text.rstrip() + "\n\nMvh Ulla Vase"

    text = text.replace(" Mvh Ulla Vase", "\n\nMvh Ulla Vase")
    return text.strip()

def is_weak_reply(text):
    if not text:
        return True
    normalized = text.strip()
    if len(normalized) < 35:
        return True
    lowered = normalized.lower()
    weak_patterns = [
        "hej kim,\n\nmvh ulla vase",
        "hej,\n\nmvh ulla vase",
        "hej kim,\nmvh ulla vase",
        "hej,\nmvh ulla vase",
    ]
    if lowered in weak_patterns:
        return True
    content = lowered.replace("mvh ulla vase", "").replace("hej kim,", "").replace("hej,", "").strip()
    return len(content) < 20

def fallback_vinterguide_price_reply(sender):
    first_name = extract_first_name(sender)
    return f"""Hej {first_name},

VinterGuide findes i tre løsninger:

Starter:
129 kr pr. bruger pr. måned
Minimum 5 brugere
7.740 kr pr. år

Pro:
179 kr pr. bruger pr. måned
Minimum 10 brugere
21.480 kr pr. år

Business:
229 kr pr. bruger pr. måned
Minimum 20 brugere
54.960 kr pr. år

Der betales for et år ad gangen.

Du kan læse mere her:
https://vinterguide.dk/intro.html#priser

Mvh Ulla Vase""".strip()

def fallback_vinterguide_offer_reply(sender):
    first_name = extract_first_name(sender)
    return f"""Hej {first_name},

VinterGuide er en digital platform til virksomheder og organisationer, der arbejder med snerydning, saltning og vintertjeneste.

Systemet samler planlægning, chauffører, opgaver og dokumentation i ét system, så man får bedre overblik over hvad der er lavet, hvornår det er lavet, og af hvem.

Det bruges typisk til planlægning af ruter, styring af chauffører, dokumentation af udført arbejde og overblik i realtid.

Hvis du også vil have priserne med, så findes VinterGuide i tre løsninger:
Starter: 129 kr pr. bruger pr. måned
Pro: 179 kr pr. bruger pr. måned
Business: 229 kr pr. bruger pr. måned

Der betales for et år ad gangen, og du kan læse mere her:
https://vinterguide.dk/intro.html#priser

Mvh Ulla Vase""".strip()

def needs_offer_reply(body, subject):
    combined = f"{(subject or '').lower()} {(body or '').lower()}"
    patterns = [
        "hvad kan i tilbyde", "hvad tilbyder i", "hvad er det for et firma",
        "hvad er det for en virksomhed", "hvad laver i",
        "høre mere om hvad i kan tilbyde", "høre mere om systemet", "hvad kan i"
    ]
    return any(p in combined for p in patterns)

def looks_like_price_question(body, subject):
    combined = f"{(subject or '').lower()} {(body or '').lower()}"
    patterns = ["pris", "priser", "bruger", "brugere", "starter", "pro", "business", "platform", "platforme", "årligt"]
    return any(p in combined for p in patterns)

def should_auto_archive(sender, subject, body):
    combined = f"{(sender or '').lower()} {(subject or '').lower()} {(body or '').lower()}"
    auto_patterns = [
        "no-reply", "noreply", "order_acknowledgment", "orders.apple.com",
        "verify your identity", "verify sign-in", "signin.aws",
        "bekræft videresendelse", "videresendelse af e-mails",
        "instagram", "facebook", "meta business", "apple store",
        "ordrenummer", "verification", "confirm", "password reset",
        "reset password", "support@dk.one.com", "skat.dk", "email-service.skat.dk",
        "donotreply@email-service.skat.dk"
    ]
    return any(p in combined for p in auto_patterns)

def ai_analyze_email(sender, recipient, subject, body):
    client = get_openai_client()
    company_context = get_company_context()
    product_key, product_context = get_product_context(recipient, subject, body)
    date_hint = build_date_hint(body)
    body_preview = body[:5000] if body else "(intet indhold)"

    prompt = f"""
Du er en skarp mailassistent for virksomheden Vweb.

Overordnet virksomhedskontekst:
{company_context if company_context else "Ingen company_context.txt fundet."}

Aktiv produktkontekst:
Produktnøgle: {product_key}
{product_context if product_context else "Ingen specifik produktkontekst fundet. Brug kun virksomhedskontekst."}

Baggrund:
- Du svarer som Ulla Vase.
- Svar skal være konkrete, hjælpsomme, menneskelige og direkte.
- Du skal bruge det faktiske spørgsmål i mailen aktivt og svare på det, ikke bare skrive et standardsvar.

Vigtige regler:
- Hvis afsenderen hedder Kim, skal svaret begynde med "Hej Kim,"
- Du må ikke forveksle afsender med egne navne fra gamle mails eller signaturer.
- Hvis mailen indeholder et konkret spørgsmål, skal du forsøge at give et konkret svar.
- Brug kun fallback-svar hvis du reelt mangler oplysninger.
- Hvis der spørges om datoer eller dage, og du har et hjælpespor, så brug det.
- Hvis spørgsmålet handler om et produkt, så svar ud fra den relevante produktkontekst.
- Svarudkast må ikke lyde som AI.
- Svarudkast skal være kort, naturligt og direkte.
- Ingen punktopstilling i selve svarudkastet, medmindre det er nødvendigt for at vise priser tydeligt.
- Svarudkast må ALDRIG indeholde pladsholdere som [Dit navn], [Navn], [Firmanavn] eller lignende.
- Du må ALDRIG opfinde eller gætte priser.
- Du må KUN bruge priser fra produktkonteksten.
- Hvis der i produktkonteksten findes konkrete priser, SKAL du skrive de konkrete priser direkte i svaret.
- Du må IKKE nøjes med at henvise til hjemmesiden, hvis priserne allerede findes i produktkonteksten.
- Hvis kunden spørger om pris eller platforme, skal du nævne Starter, Pro og Business med de konkrete tal.
- Når du skriver priser, SKAL du skrive "kr pr. bruger pr. måned".
- Du SKAL skrive at der betales for et år ad gangen.
- Du SKAL inkludere linket https://vinterguide.dk/intro.html#priser ved prisforespørgsler.
- Du SKAL lave linjeskift mellem afsnit.
- Du SKAL altid udfylde SVARUDKAST med et konkret svar hvis KRÆVER_SVAR er ja.
- SVARUDKAST må ALDRIG være tomt.
- Svarudkast skal afsluttes med: "Mvh Ulla Vase"
- Hvis der ikke skal svares, skriv "intet".

Hjælpespor:
{date_hint if date_hint else "Ingen særlige dato-hints."}

Returnér KUN i dette format:

KATEGORI: <spam|nyhedsbrev|automatisk|kunde|vigtig|ukendt>
KRÆVER_SVAR: <ja|nej>
RESUMÉ: <kort opsummering>
SVARUDKAST: <kort svar på dansk, eller skriv "intet">

Afsender:
{sender}

Sendt til:
{recipient}

Emne:
{subject}

Renset mailindhold:
{body_preview}
""".strip()

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt
    )
    return response.output_text.strip(), product_key

def choose_fallback_reply(sender, subject, body, product_key):
    if product_key == "vinterguide":
        if needs_offer_reply(body, subject):
            return fallback_vinterguide_offer_reply(sender)
        if looks_like_price_question(body, subject):
            return fallback_vinterguide_price_reply(sender)
    return f"Hej {extract_first_name(sender)},\n\nTak for din mail. Jeg vender tilbage med et konkret svar.\n\nMvh Ulla Vase"

def send_via_resend(to_email, original_subject, draft_reply, sender):
    if not RESEND_API_KEY:
        raise ValueError("RESEND_API_KEY mangler i Railway Variables")
    if not AI_FROM_EMAIL:
        raise ValueError("AI_FROM_EMAIL mangler i Railway Variables")

    resend.api_key = RESEND_API_KEY
    subject = original_subject if original_subject.lower().startswith("re:") else f"Re: {original_subject}"

    signature_html = """
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

    html = f"<p>{draft_reply.replace(chr(10), '<br>')}</p>{signature_html}"

    return resend.Emails.send({
        "from": AI_FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "html": html
    })

def extract_reply_email(sender):
    return email.utils.parseaddr(sender)[1]

def extract_recipient(msg):
    for header_name in ["Delivered-To", "Envelope-To", "X-Original-To", "To"]:
        value = msg.get(header_name)
        if value:
            decoded = decode_mime_text(value)
            _, addr = email.utils.parseaddr(decoded)
            if addr:
                return addr
    return ""

def check_mail():
    print("Mail-bot starter...")
    print("Tjekker mail...")

    email_user = os.getenv("MAIL_USER")
    email_pass = os.getenv("MAIL_PASS")

    if not email_user or not email_pass:
        raise ValueError("MAIL_USER eller MAIL_PASS mangler i Railway Variables")

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

    recent_mail_ids = mail_ids[-100:]
    print(f"Scanner {len(recent_mail_ids)} mails...")

    for mail_id in recent_mail_ids:
        mail_id_int = int(mail_id)

        if already_saved_reply(mail_id_int):
            continue

        status, msg_data = mail.fetch(mail_id, "(RFC822)")
        if status != "OK":
            print(f"Kunde ikke hente mail {mail_id_int}")
            continue

        for response_part in msg_data:
            if not isinstance(response_part, tuple):
                continue

            msg = email.message_from_bytes(response_part[1])

            sender = decode_mime_text(msg.get("From"))
            recipient = extract_recipient(msg) or email_user
            subject = decode_mime_text(msg.get("Subject"))
            full_body = get_plain_text_body(msg)
            cleaned_body = strip_quoted_text(full_body)

            print("========================================")
            print(f"NY MAIL (ID {mail_id_int})")
            print(f"Fra: {sender}")
            print(f"Emne: {subject}")
            print("----------------------------------------")

            try:
                if should_auto_archive(sender, subject, cleaned_body):
                    save_pending_reply(
                        mail_id=mail_id_int,
                        sender=sender,
                        recipient=recipient,
                        product_context="automatisk",
                        subject=subject,
                        category="automatisk",
                        summary="Systemmail / automatisk mail der ikke kræver svar.",
                        reply_needed="nej",
                        draft_reply="intet",
                        original_preview=cleaned_body,
                        status="archived"
                    )
                    update_reply_status(mail_id_int, "archived")
                    continue

                if cleaned_body.strip() == "(intet indhold)":
                    save_pending_reply(
                        mail_id=mail_id_int,
                        sender=sender,
                        recipient=recipient,
                        product_context="tom",
                        subject=subject,
                        category="automatisk",
                        summary="Tom mail uden indhold.",
                        reply_needed="nej",
                        draft_reply="intet",
                        original_preview=cleaned_body,
                        status="archived"
                    )
                    update_reply_status(mail_id_int, "archived")
                    continue

                ai_result, product_key = ai_analyze_email(
                    sender=sender,
                    recipient=recipient,
                    subject=subject,
                    body=cleaned_body
                )

                print("AI RAW RESULT:")
                print(ai_result)

                parsed = parse_ai_result(ai_result)

                category = parsed["category"]
                requires_reply = parsed["requires_reply"]
                summary = parsed["summary"]
                raw_reply = parsed.get("draft_reply", "").strip()

                if not raw_reply or raw_reply.lower() == "intet" or is_weak_reply(raw_reply):
                    raw_reply = choose_fallback_reply(sender, subject, cleaned_body, product_key)

                draft_reply = normalize_draft_reply(raw_reply, sender, product_key)

                if is_weak_reply(draft_reply):
                    draft_reply = normalize_draft_reply(
                        choose_fallback_reply(sender, subject, cleaned_body, product_key),
                        sender,
                        product_key
                    )

                no_reply_markers = [
                    "ingen direkte spørgsmål",
                    "ingen handling nødvendig",
                    "kræver ikke svar",
                ]

                if requires_reply != "ja" or any(marker in summary.lower() for marker in no_reply_markers):
                    save_pending_reply(
                        mail_id=mail_id_int,
                        sender=sender,
                        recipient=recipient,
                        product_context=product_key,
                        subject=subject,
                        category=category,
                        summary=summary or "Ingen handling nødvendig.",
                        reply_needed="nej",
                        draft_reply="intet",
                        original_preview=cleaned_body,
                        status="archived"
                    )
                    update_reply_status(mail_id_int, "archived")
                    continue

                if category in REPLY_CATEGORIES and requires_reply == "ja":
                    save_pending_reply(
                        mail_id=mail_id_int,
                        sender=sender,
                        recipient=recipient,
                        product_context=product_key,
                        subject=subject,
                        category=category,
                        summary=summary,
                        reply_needed=requires_reply,
                        draft_reply=draft_reply,
                        original_preview=cleaned_body,
                        status="pending_approval"
                    )
                    print("SVARUDKAST GEMT")
                else:
                    save_pending_reply(
                        mail_id=mail_id_int,
                        sender=sender,
                        recipient=recipient,
                        product_context=product_key,
                        subject=subject,
                        category=category,
                        summary=summary or "Ingen handling nødvendig.",
                        reply_needed="nej",
                        draft_reply="intet",
                        original_preview=cleaned_body,
                        status="archived"
                    )
                    update_reply_status(mail_id_int, "archived")
                    print("INGEN SVAR NØDVENDIGT")

            except Exception as e:
                print(f"FEJL: {e}")

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
        HTML_TEMPLATE,
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
def update_draft(mail_id):
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

        result = send_via_resend(
            to_email=to_email,
            original_subject=item["subject"],
            draft_reply=item["draft_reply"],
            sender=item["sender"]
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
    init_db()
    ensure_replies_columns()
    print("KALDER CHECK_MAIL / STARTER WEB")
    worker = threading.Thread(target=polling_loop, daemon=True)
    worker.start()
    app.run(host="0.0.0.0", port=PORT)
