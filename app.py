import imaplib
import email
import os
import time
import sqlite3
import threading
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from email.header import decode_header

import resend
from openai import OpenAI
from bs4 import BeautifulSoup
from flask import Flask, redirect, render_template_string, url_for

print("APP STARTER NU!!!")

IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.one.com")
MAILBOX = os.getenv("MAILBOX", "INBOX")
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))
PORT = int(os.getenv("PORT", "8080"))
TIMEZONE_NAME = os.getenv("TIMEZONE_NAME", "Europe/Copenhagen")

DB_PATH = os.getenv("DB_PATH", "mailbot.db")

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
  <meta http-equiv="refresh" content="10">
  <title>Mailbot godkendelse</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; background: #f6f7f9; color: #222; }
    h1 { margin-bottom: 8px; }
    h2 { margin-top: 28px; margin-bottom: 10px; }
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

    .empty {
      background: white;
      border: 1px dashed #cbd5e1;
      border-radius: 10px;
      padding: 16px;
      color: #64748b;
    }
  </style>
</head>
<body>
  <h1>Mailbot godkendelse</h1>
  <div class="muted">Godkend først. Send bagefter. Siden opdaterer automatisk hvert 10. sekund.</div>

  <div class="topbar">
    <div class="summary">Afventer: <strong>{{ pending_count }}</strong></div>
    <div class="summary">Godkendt til send: <strong>{{ approved_count }}</strong></div>
    <div class="summary">Sendt: <strong>{{ sent_count }}</strong></div>
    <div class="summary">Afvist: <strong>{{ rejected_count }}</strong></div>
    <div class="summary">Arkiveret: <strong>{{ archived_count }}</strong></div>
  </div>

  <h2>Aktive mails</h2>
  {% if active_items %}
    {% for item in active_items %}
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

        <div class="row"><span class="label">Renset mailtekst:</span></div>
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
            Dette svar er godkendt og klar til afsendelse.
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
            <form method="post" action="{{ url_for('archive_reply', mail_id=item.mail_id) }}">
              <button class="archive" type="submit">Arkivér</button>
            </form>
          {% endif %}
        </div>
      </div>
    {% endfor %}
  {% else %}
    <div class="empty">Ingen aktive mails lige nu.</div>
  {% endif %}

  <h2>Historik</h2>
  {% if history_items %}
    {% for item in history_items %}
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

        <div class="row"><span class="label">Renset mailtekst:</span></div>
        <pre>{{ item.original_preview }}</pre>

        <div class="row"><span class="label">Svarudkast:</span></div>
        <pre>{{ item.draft_reply }}</pre>

        {% if item.sent_at %}
          <div class="row"><span class="label">Sendt:</span> {{ item.sent_at }}</div>
        {% endif %}

        {% if item.send_error %}
          <div class="row"><span class="label">Sendefejl:</span> {{ item.send_error }}</div>
        {% endif %}
      </div>
    {% endfor %}
  {% else %}
    <div class="empty">Ingen historik endnu.</div>
  {% endif %}
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
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mail_id TEXT UNIQUE,
                saved_at TEXT,
                sender TEXT,
                subject TEXT,
                category TEXT,
                requires_reply TEXT,
                summary TEXT,
                draft_reply TEXT,
                original_preview TEXT,
                status TEXT,
                sent_at TEXT,
                send_error TEXT
            )
        """)

        conn.commit()
        conn.close()


def get_state(key):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT value FROM app_state WHERE key = ?", (key,))
        row = cur.fetchone()
        conn.close()
        return row["value"] if row else None


def set_state(key, value):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO app_state (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, str(value)))
        conn.commit()
        conn.close()


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
        r"^Mailbot <",
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

        matched_break = any(re.match(pattern, line, flags=re.IGNORECASE) for pattern in break_patterns)
        if matched_break:
            break

        matched_signature = any(re.match(pattern, line, flags=re.IGNORECASE) for pattern in signature_patterns)
        if matched_signature:
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

    source = re.sub(r"[\"<>]", "", source).strip()
    parts = source.split()

    if not parts:
        return "der"

    first = parts[0].strip(" ,.-")
    return first if first else "der"


def get_local_now():
    try:
        return datetime.now(ZoneInfo(TIMEZONE_NAME))
    except Exception:
        return datetime.now()


def format_danish_date(dt):
    months = {
        1: "januar", 2: "februar", 3: "marts", 4: "april", 5: "maj", 6: "juni",
        7: "juli", 8: "august", 9: "september", 10: "oktober", 11: "november", 12: "december"
    }
    return f"{dt.day}. {months[dt.month]} {dt.year}"


def get_next_week_saturday(base_dt):
    weekday = base_dt.weekday()  # Monday=0
    days_until_next_monday = 7 - weekday
    next_week_monday = base_dt + timedelta(days=days_until_next_monday)
    next_week_saturday = next_week_monday + timedelta(days=5)
    return next_week_saturday


def maybe_rule_based_reply(sender, subject, body):
    first_name = extract_first_name(sender)
    lower_body = body.lower()
    lower_subject = subject.lower()

    if "lørdag i næste uge" in lower_body or "lørdag i næste uge" in lower_subject:
        saturday = get_next_week_saturday(get_local_now())
        date_text = format_danish_date(saturday)
        return {
            "category": "kunde",
            "requires_reply": "ja",
            "summary": f"Spørgsmål om hvilken dato lørdag i næste uge falder på. Datoen er {date_text}.",
            "draft_reply": f"Hej {first_name},\n\nLørdag i næste uge er den {date_text}. Passer det stadig for dig?\n\nMvh Kim Vase"
        }

    vinterguide_patterns = [
        "hvad er vinterguide",
        "hvad kan vinterguide",
        "hvad bruges vinterguide til",
        "hvad kan det bruges til",
        "fortæl mig om vinterguide",
        "hvad tænker på vinterguide",
        "kan du forklare vinterguide",
    ]

    if any(pattern in lower_body for pattern in vinterguide_patterns) or any(pattern in lower_subject for pattern in vinterguide_patterns):
        return {
            "category": "kunde",
            "requires_reply": "ja",
            "summary": "Spørgsmål om hvad VinterGuide er og hvad systemet bruges til i praksis.",
            "draft_reply": (
                f"Hej {first_name},\n\n"
                "VinterGuide er et system til at skabe overblik over vinterarbejdet i praksis. "
                "Det kan bruges til planlægning, koordinering, dokumentation og opfølgning, så man lettere kan styre opgaverne i hverdagen.\n\n"
                "Hvis du vil, kan jeg også forklare mere konkret hvordan det bruges i den daglige drift.\n\n"
                "Mvh Kim Vase"
            )
        }

    return None


def get_last_mail_id():
    value = get_state("last_mail_id")
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def save_last_mail_id(mail_id):
    set_state("last_mail_id", mail_id)


def load_replies_by_status(statuses):
    placeholders = ",".join("?" for _ in statuses)
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(f"""
            SELECT mail_id, saved_at, sender, subject, category, requires_reply,
                   summary, draft_reply, original_preview, status, sent_at, send_error
            FROM replies
            WHERE status IN ({placeholders})
            ORDER BY datetime(COALESCE(sent_at, saved_at)) DESC
        """, tuple(statuses))
        rows = cur.fetchall()
        conn.close()
        return [dict(row) for row in rows]


def already_saved_reply(mail_id):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM replies WHERE mail_id = ?", (str(mail_id),))
        row = cur.fetchone()
        conn.close()
        return row is not None


def save_pending_reply(mail_id, sender, subject, category, summary, reply_needed, draft_reply, original_preview):
    if already_saved_reply(mail_id):
        return

    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO replies (
                mail_id, saved_at, sender, subject, category, requires_reply,
                summary, draft_reply, original_preview, status, sent_at, send_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(mail_id),
            datetime.utcnow().isoformat() + "Z",
            sender,
            subject,
            category,
            reply_needed,
            summary,
            draft_reply,
            original_preview[:2000],
            "pending_approval",
            None,
            None
        ))
        conn.commit()
        conn.close()


def update_reply_status(mail_id, new_status, send_error=None, sent_at=None):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE replies
            SET status = ?, send_error = ?, sent_at = ?
            WHERE mail_id = ?
        """, (new_status, send_error, sent_at, str(mail_id)))
        updated = cur.rowcount > 0
        conn.commit()
        conn.close()
        return updated


def get_reply_by_id(mail_id):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT mail_id, saved_at, sender, subject, category, requires_reply,
                   summary, draft_reply, original_preview, status, sent_at, send_error
            FROM replies
            WHERE mail_id = ?
        """, (str(mail_id),))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None


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


def is_weak_reply(reply_text, first_name):
    if not reply_text:
        return True

    text = reply_text.strip().lower()

    weak_patterns = [
        "jeg vender tilbage",
        "mere præcist svar",
        "med det samme",
        "tak for din mail",
    ]

    if any(pattern in text for pattern in weak_patterns):
        return True

    stripped = text.replace(f"hej {first_name.lower()},", "").replace("mvh kim vase", "").strip()
    return len(stripped) < 35


def ai_analyze_email(sender, subject, body):
    client = get_openai_client()
    sender_first_name = extract_first_name(sender)
    body_preview = body[:5000] if body else "(intet indhold)"

    prompt = f"""
Du er en skarp mailassistent for virksomheden Vweb.

Baggrund:
- Vweb arbejder blandt andet med løsningen VinterGuide.
- VinterGuide bruges til planlægning, overblik, dokumentation og daglig styring i praksis.
- Du svarer som Kim Vase.
- Svar skal være konkrete, hjælpsomme og menneskelige.
- Hvis modtageren hedder Kim, skal svaret starte med "Hej Kim,"
- Du må ikke
