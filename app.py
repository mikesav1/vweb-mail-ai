import os
import re
import time
import imaplib
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import parseaddr

import resend
from bs4 import BeautifulSoup
from flask import Flask, redirect, render_template_string, request, url_for
from openai import OpenAI


# -----------------------------
# App config
# -----------------------------
app = Flask(__name__)
file_lock = threading.Lock()

IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.one.com")
MAILBOX = os.getenv("MAILBOX", "INBOX")
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))
PORT = int(os.getenv("PORT", "8080"))
DB_PATH = os.getenv("DB_PATH", "mailbot.db")

MAIL_USER = os.getenv("MAIL_USER", "")
MAIL_PASS = os.getenv("MAIL_PASS", "")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
AI_FROM_EMAIL = os.getenv("AI_FROM_EMAIL", "")

COMPANY_CONTEXT_FILE = os.getenv("COMPANY_CONTEXT_FILE", "company_context.txt")
PRODUCT_VINTERGUIDE_FILE = os.getenv("PRODUCT_VINTERGUIDE_FILE", "product_vinterguide.txt")
PRODUCT_SLUSHBOOK_FILE = os.getenv("PRODUCT_SLUSHBOOK_FILE", "product_slushbook.txt")

REPLY_CATEGORIES = {"kunde", "vigtig", "ukendt"}


# -----------------------------
# HTML
# -----------------------------
HTML_TEMPLATE = """
<!doctype html>
<html lang="da">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="20">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mailbot indbakke</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 30px; color: #111; }
    h1 { margin-bottom: 8px; }
    .summary { margin-bottom: 18px; font-size: 16px; }
    .mail-card { border: 1px solid #ccc; border-radius: 8px; padding: 16px; margin-bottom: 18px; }
    .meta { color: #444; line-height: 1.6; }
    pre { white-space: pre-wrap; background: #f7f7f7; padding: 12px; border-radius: 6px; }
    textarea { width: 100%; min-height: 190px; font-size: 16px; padding: 10px; box-sizing: border-box; }
    button { font-size: 16px; padding: 9px 13px; margin: 6px 4px 6px 0; cursor: pointer; }
    .send { background: #0b7; color: white; border: 0; border-radius: 5px; }
    .approve { background: #2563eb; color: white; border: 0; border-radius: 5px; }
    .remove { background: #666; color: white; border: 0; border-radius: 5px; }
    .save { background: #eee; border: 1px solid #aaa; border-radius: 5px; }
    .error { color: #b00020; font-weight: bold; }
  </style>
</head>
<body>
  <h1>Mailbot indbakke</h1>

  <div class="summary">
    Afventer: <strong>{{ pending_count }}</strong> |
    Klar til send: <strong>{{ approved_count }}</strong> |
    Fejlede: <strong>{{ failed_count }}</strong>
  </div>

  <hr>

  {% if active_items %}
    {% for item in active_items %}
      <div class="mail-card">
        <h2>{{ item['subject'] }}</h2>

        <div class="meta">
          <div><strong>Fra:</strong> {{ item['sender'] }}</div>
          <div><strong>Til:</strong> {{ item['recipient'] }}</div>
          <div><strong>Dato:</strong> {{ item['saved_at_display'] }}</div>
          <div><strong>Kategori:</strong> {{ item['category'] }}</div>
          <div><strong>Produkt:</strong> {{ item['product_context'] }}</div>
          <div><strong>Resumé:</strong> {{ item['summary'] }}</div>
        </div>

        <p><strong>Renset mailtekst:</strong></p>
        <pre>{{ item['original_preview'] }}</pre>

        <form method="post" action="{{ url_for('update_draft_route', mail_id=item['mail_id']) }}">
          <p><strong>Svarudkast:</strong></p>
          <textarea name="draft_reply">{{ item['draft_reply'] }}</textarea><br>
          <button class="save" type="submit">Gem ændringer</button>
        </form>

        {% if item['send_error'] %}
          <p class="error">Sendefejl: {{ item['send_error'] }}</p>
        {% endif %}

        {% if item['status'] == 'pending_approval' %}
          <form method="post" action="{{ url_for('approve_reply', mail_id=item['mail_id']) }}" style="display:inline;">
            <button class="approve" type="submit">Godkend til send</button>
          </form>
        {% endif %}

        {% if item['status'] == 'approved_api' or item['status'] == 'send_failed' %}
          <form method="post" action="{{ url_for('send_reply', mail_id=item['mail_id']) }}" style="display:inline;">
            <button class="send" type="submit">Send nu</button>
          </form>
        {% endif %}

        <form method="post" action="{{ url_for('remove_reply', mail_id=item['mail_id']) }}" style="display:inline;">
          <button class="remove" type="submit">Fjern fra botten</button>
        </form>
      </div>
    {% endfor %}
  {% else %}
    <p>Ingen mails der kræver handling lige nu.</p>
  {% endif %}
</body>
</html>
"""


# -----------------------------
# Database
# -----------------------------
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def now_utc_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
                is_new TEXT,
                sent_at TEXT,
                send_error TEXT,
                seen INTEGER DEFAULT 0
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS processed_mails (
                mail_id TEXT PRIMARY KEY,
                processed_at TEXT
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

        needed = {
            "recipient": "ALTER TABLE replies ADD COLUMN recipient TEXT",
            "product_context": "ALTER TABLE replies ADD COLUMN product_context TEXT",
            "is_new": "ALTER TABLE replies ADD COLUMN is_new TEXT",
            "seen": "ALTER TABLE replies ADD COLUMN seen INTEGER DEFAULT 0",
            "send_error": "ALTER TABLE replies ADD COLUMN send_error TEXT",
        }

        for col, sql in needed.items():
            if col not in columns:
                cur.execute(sql)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS processed_mails (
                mail_id TEXT PRIMARY KEY,
                processed_at TEXT
            )
        """)

        conn.commit()
        conn.close()


def mark_processed(mail_id):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO processed_mails (mail_id, processed_at) VALUES (?, ?)",
            (str(mail_id), now_utc_iso()),
        )
        conn.commit()
        conn.close()


def is_processed(mail_id):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM processed_mails WHERE mail_id = ?", (str(mail_id),))
        processed = cur.fetchone() is not None
        cur.execute("SELECT 1 FROM replies WHERE mail_id = ?", (str(mail_id),))
        active = cur.fetchone() is not None
        conn.close()
    return processed or active


def load_replies_by_status(statuses):
    placeholders = ",".join("?" for _ in statuses)

    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(f"""
            SELECT mail_id, saved_at, sender, recipient, product_context, subject, category, requires_reply,
                   summary, draft_reply, original_preview, status, is_new, sent_at, send_error, seen
            FROM replies
            WHERE status IN ({placeholders})
            ORDER BY datetime(saved_at) DESC
        """, tuple(statuses))
        rows = [dict(row) for row in cur.fetchall()]
        conn.close()

    for row in rows:
        row["sender_name"] = extract_sender_name(row.get("sender", ""))
        row["saved_at_display"] = format_display_datetime(row.get("saved_at"))
        if row.get("seen") is None:
            row["seen"] = 0

    return rows


def save_pending_reply(mail_id, sender, recipient, product_context, subject, category, summary, reply_needed, draft_reply, original_preview, status="pending_approval"):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO replies (
                mail_id, saved_at, sender, recipient, product_context, subject, category, requires_reply,
                summary, draft_reply, original_preview, status, is_new, sent_at, send_error, seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(mail_id),
            now_utc_iso(),
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
            "ja",
            None,
            None,
            0,
        ))
        conn.commit()
        conn.close()


def update_reply_status(mail_id, new_status, sent_at=None, send_error=None):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE replies
            SET status = ?, sent_at = ?, send_error = ?, is_new = 'nej', seen = 1
            WHERE mail_id = ?
        """, (new_status, sent_at, send_error, str(mail_id)))
        conn.commit()
        conn.close()


def delete_reply(mail_id):
    mark_processed(mail_id)
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM replies WHERE mail_id = ?", (str(mail_id),))
        conn.commit()
        conn.close()


def get_reply_by_id(mail_id):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT mail_id, saved_at, sender, recipient, product_context, subject, category, requires_reply,
                   summary, draft_reply, original_preview, status, is_new, sent_at, send_error, seen
            FROM replies
            WHERE mail_id = ?
        """, (str(mail_id),))
        row = cur.fetchone()
        conn.close()

    if not row:
        return None

    item = dict(row)
    item["sender_name"] = extract_sender_name(item.get("sender", ""))
    item["saved_at_display"] = format_display_datetime(item.get("saved_at"))
    return item


def update_reply_draft(mail_id, new_text):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE replies
            SET draft_reply = ?, is_new = 'nej', seen = 1
            WHERE mail_id = ?
        """, (new_text, str(mail_id)))
        conn.commit()
        conn.close()


def get_counts():
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        counts = {}
        for status in ["pending_approval", "approved_api", "send_failed"]:
            cur.execute("SELECT COUNT(*) AS c FROM replies WHERE status = ?", (status,))
            counts[status] = cur.fetchone()["c"]
        conn.close()
    return counts


# -----------------------------
# Utility
# -----------------------------
def read_text_file(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def decode_mime_text(value):
    if not value:
        return "(intet emne)"
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


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
    return clean_text(soup.get_text(separator="\n"))


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

        return plain_body or html_body or "(intet indhold)"

    payload = msg.get_payload(decode=True)
    if not payload:
        return "(intet indhold)"

    charset = msg.get_content_charset() or "utf-8"
    try:
        decoded = payload.decode(charset, errors="replace")
    except Exception:
        decoded = payload.decode("utf-8", errors="replace")

    if msg.get_content_type() == "text/html":
        return html_to_text(decoded)
    return clean_text(decoded)


def strip_quoted_text(text):
    if not text:
        return "(intet indhold)"

    lines = text.splitlines()
    cleaned_lines = []

    break_patterns = [
        r"^Den .+ skrev",
        r"^On .+ wrote:$",
        r"^Fra:",
        r"^From:",
        r"^Sendt:",
        r"^Sent:",
        r"^Til:",
        r"^To:",
        r"^Emne:",
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
    name, addr = parseaddr(sender or "")
    source = name.strip() or addr.split("@")[0].strip()
    if not source:
        return "der"
    source = re.sub(r'["<>]', "", source).strip()
    parts = source.split()
    if not parts:
        return "der"
    return parts[0].strip(" ,.-") or "der"


def extract_sender_name(sender):
    name, addr = parseaddr(sender or "")
    return name.strip() or addr or sender or "(ukendt)"


def extract_reply_email(sender):
    return parseaddr(sender or "")[1]


def extract_recipient(msg):
    for header_name in ["Delivered-To", "Envelope-To", "X-Original-To", "To"]:
        value = msg.get(header_name)
        if value:
            _, addr = parseaddr(decode_mime_text(value))
            if addr:
                return addr
    return ""


def format_display_datetime(value):
    if not value:
        return ""
    try:
        if value.endswith("Z"):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(value)
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return value


def next_weekday_date(target_weekday):
    today = datetime.now().date()
    days_ahead = target_weekday - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    target = today + timedelta(days=days_ahead)
    return target.strftime("%d.%m.%Y")


def build_date_hint(body):
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
            return f"{phrase.split(' i næste uge')[0].capitalize()} i næste uge er den {next_weekday_date(weekday)}."
    return ""


# -----------------------------
# Product context
# -----------------------------
def get_product_context(recipient, subject, body):
    recipient_l = (recipient or "").lower()
    subject_l = (subject or "").lower()
    body_l = (body or "").lower()
    combined = f"{recipient_l} {subject_l} {body_l}"

    vinterguide_keywords = [
        "vinterguide", "snerydning", "saltning", "vintertjeneste", "beredskab",
        "ruter", "chauffører", "platform", "platforme", "bruger", "brugere",
        "pris", "priser", "starter", "pro", "business",
    ]

    slushbook_keywords = ["slushbook", "slush", "opskrift", "opskrifter"]

    if "@vinterguide.dk" in recipient_l or any(word in combined for word in vinterguide_keywords):
        return "vinterguide", read_text_file(PRODUCT_VINTERGUIDE_FILE)

    if "@slushbook" in recipient_l or any(word in combined for word in slushbook_keywords):
        return "slushbook", read_text_file(PRODUCT_SLUSHBOOK_FILE)

    return "vweb", ""


# -----------------------------
# AI
# -----------------------------
def get_openai_client():
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY mangler")
    return OpenAI(api_key=OPENAI_API_KEY)


def parse_ai_result(ai_text):
    result = {
        "category": "ukendt",
        "requires_reply": "nej",
        "summary": "",
        "draft_reply": "",
    }

    current_key = None
    draft_lines = []

    for raw_line in ai_text.splitlines():
        line = raw_line.strip()
        upper = line.upper()

        if upper.startswith("KATEGORI:"):
            current_key = "category"
            result["category"] = line.split(":", 1)[1].strip().lower()
        elif upper.startswith("KRÆVER_SVAR:"):
            current_key = "requires_reply"
            result["requires_reply"] = line.split(":", 1)[1].strip().lower()
        elif upper.startswith("RESUMÉ:"):
            current_key = "summary"
            result["summary"] = line.split(":", 1)[1].strip()
        elif upper.startswith("SVARUDKAST:"):
            current_key = "draft_reply"
            first = line.split(":", 1)[1].strip()
            if first:
                draft_lines.append(first)
        elif current_key == "draft_reply":
            draft_lines.append(raw_line.rstrip())

    result["draft_reply"] = "\n".join(draft_lines).strip()
    return result


def normalize_draft_reply(draft_reply, sender):
    text = (draft_reply or "").strip()
    first_name = extract_first_name(sender)

    if not text or text.lower() == "intet":
        return "intet"

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

    if re.match(r"^Hej\s*,", text, flags=re.IGNORECASE):
        text = re.sub(r"^Hej\s*,", f"Hej {first_name},", text, count=1, flags=re.IGNORECASE)
    elif text:
        text = re.sub(r"^Hej\s+[^,\n]+,", f"Hej {first_name},", text, count=1, flags=re.IGNORECASE)

    if text.startswith(f"Hej {first_name},") and "\n\n" not in text:
        text = text.replace(f"Hej {first_name},", f"Hej {first_name},\n\n", 1)

    if "Mvh Ulla Vase" not in text:
        text = text.rstrip() + "\n\nMvh Ulla Vase"

    text = text.replace(" Mvh Ulla Vase", "\n\nMvh Ulla Vase")
    return text.strip()


def fallback_reply(sender):
    first_name = extract_first_name(sender)
    return f"Hej {first_name},\n\nTak for din mail. Jeg vender tilbage med et konkret svar.\n\nMvh Ulla Vase"


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


def should_auto_ignore(sender, subject, body):
    sender_l = (sender or "").lower()
    subject_l = (subject or "").lower()
    body_l = (body or "").lower()
    combined = f"{sender_l} {subject_l} {body_l}"
  # SlushBook og VinterGuide mails må aldrig auto-ignoreres
if "slushbook" in combined or "vinterguide" in combined:
    return False
    patterns = [
    "no-reply",
    "noreply",
    "donotreply",
    "do-not-reply",

    "dmarc",
    "report domain",
    "delivery delayed",
    "undeliverable",

    "unsubscribe",
    "afmeld",

    "password reset",
    "reset password",
    "validation code",

    "verify sign-in",
    "verify your identity",

    "support@dk.one.com",
]

    return any(p in combined for p in patterns)


def ai_analyze_email(sender, recipient, subject, body):
    client = get_openai_client()
    company_context = read_text_file(COMPANY_CONTEXT_FILE)
    product_key, product_context = get_product_context(recipient, subject, body)
    date_hint = build_date_hint(body)
    body_preview = body[:5000] if body else "(intet indhold)"

    prompt = f"""
Du er mailassistent for Vweb.

Overordnet virksomhedskontekst:
{company_context if company_context else "Ingen company_context.txt fundet."}

Aktiv produktkontekst:
Produktnøgle: {product_key}
{product_context if product_context else "Ingen specifik produktkontekst fundet. Brug kun virksomhedskontekst."}

Du svarer som Ulla Vase.
Svar skal være korte, konkrete, hjælpsomme og menneskelige.
Du må ikke skrive som en AI.
Du må ikke opfinde priser.
Hvis der ikke er behov for svar, skal KRÆVER_SVAR være nej og SVARUDKAST være intet.
Hvis kunden spørger om pris på VinterGuide, skal du nævne Starter, Pro og Business med konkrete tal, hvis de findes i konteksten.
Svarudkast skal afsluttes med Mvh Ulla Vase.

Hjælpespor:
{date_hint if date_hint else "Ingen særlige dato-hints."}

Returnér KUN i dette format:

KATEGORI: <spam|nyhedsbrev|automatisk|kunde|vigtig|ukendt>
KRÆVER_SVAR: <ja|nej>
RESUMÉ: <kort opsummering>
SVARUDKAST: <kort svar på dansk, eller intet>

Afsender:
{sender}

Sendt til:
{recipient}

Emne:
{subject}

Renset mailindhold:
{body_preview}
""".strip()

    response = client.responses.create(model="gpt-4.1-mini", input=prompt)
    return response.output_text.strip(), product_key


# -----------------------------
# Sending
# -----------------------------
def send_via_resend(to_email, original_subject, draft_reply):
    if not RESEND_API_KEY:
        raise ValueError("RESEND_API_KEY mangler")
    if not AI_FROM_EMAIL:
        raise ValueError("AI_FROM_EMAIL mangler")

    resend.api_key = RESEND_API_KEY
    subject = original_subject if original_subject.lower().startswith("re:") else f"Re: {original_subject}"
    html = f"<p>{draft_reply.replace(chr(10), '<br>')}</p>"

    return resend.Emails.send({
        "from": AI_FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "html": html,
    })


# -----------------------------
# Mail reading
# -----------------------------
def check_mail():
    print("Checking mail...", flush=True)

    if not MAIL_USER or not MAIL_PASS:
        raise ValueError("MAIL_USER eller MAIL_PASS mangler")

    print("Connecting to IMAP:", IMAP_SERVER, flush=True)
    mail = imaplib.IMAP4_SSL(IMAP_SERVER, 993)

    print("Logging in as:", MAIL_USER, flush=True)
    mail.login(MAIL_USER, MAIL_PASS)
    print("IMAP login OK", flush=True)

    status, _ = mail.select(MAILBOX)
    print("Mailbox select status:", status, flush=True)

    if status != "OK":
        mail.logout()
        raise ValueError(f"Kunne ikke vælge mailbox: {MAILBOX}")

    status, messages = mail.search(None, "ALL")
    print("IMAP search status:", status, flush=True)

    if status != "OK":
        mail.logout()
        return

    mail_ids = messages[0].split()
    print("Found mails:", len(mail_ids), flush=True)

    if not mail_ids:
        mail.logout()
        return

    recent_mail_ids = mail_ids[-150:]

    for mail_id in recent_mail_ids:
        mail_id_int = int(mail_id)

        if is_processed(mail_id_int):
            continue

        print("Reading new mail id:", mail_id_int, flush=True)

        status, msg_data = mail.fetch(mail_id, "(RFC822)")
        if status != "OK":
            print("Fetch failed for:", mail_id_int, flush=True)
            continue

        for response_part in msg_data:
            if not isinstance(response_part, tuple):
                continue

            msg = message_from_bytes(response_part[1])
            sender = decode_mime_text(msg.get("From"))
            recipient = extract_recipient(msg) or MAIL_USER
            subject = decode_mime_text(msg.get("Subject"))
            full_body = get_plain_text_body(msg)
            cleaned_body = strip_quoted_text(full_body)

            print("Mail from:", sender, flush=True)
            print("Mail subject:", subject, flush=True)

            if should_auto_ignore(sender, subject, cleaned_body):
                print("Auto ignored:", subject, flush=True)
                mark_processed(mail_id_int)
                continue

            try:
                ai_result, product_key = ai_analyze_email(
                    sender=sender,
                    recipient=recipient,
                    subject=subject,
                    body=cleaned_body,
                )
                parsed = parse_ai_result(ai_result)

                category = parsed["category"]
                requires_reply = parsed["requires_reply"]
                summary = parsed["summary"] or "Ingen opsummering."
                raw_reply = parsed.get("draft_reply", "").strip()

                if category in REPLY_CATEGORIES and requires_reply == "ja":
                    if not raw_reply or raw_reply.lower() == "intet":
                        if product_key == "vinterguide":
                            raw_reply = fallback_vinterguide_price_reply(sender)
                        else:
                            raw_reply = fallback_reply(sender)

                    draft_reply = normalize_draft_reply(raw_reply, sender)

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
                        status="pending_approval",
                    )
                else:
                    print("No action needed:", subject, flush=True)
                    mark_processed(mail_id_int)

            except Exception as e:
                print("AI/processing error:", str(e), flush=True)
                save_pending_reply(
                    mail_id=mail_id_int,
                    sender=sender,
                    recipient=recipient,
                    product_context="ukendt",
                    subject=subject,
                    category="ukendt",
                    summary=f"Fejl ved AI-behandling: {e}",
                    reply_needed="ja",
                    draft_reply=fallback_reply(sender),
                    original_preview=cleaned_body,
                    status="pending_approval",
                )

    mail.logout()
    print("Mail check done", flush=True)


def polling_loop():
    print("Polling loop started", flush=True)

    while True:
        try:
            check_mail()
        except Exception as e:
            print("Polling error:", str(e), flush=True)

        time.sleep(CHECK_INTERVAL_SECONDS)


# -----------------------------
# Routes
# -----------------------------
@app.route("/")
def dashboard():
    active_items = load_replies_by_status(["pending_approval", "approved_api", "send_failed"])
    counts = get_counts()

    return render_template_string(
        HTML_TEMPLATE,
        active_items=active_items,
        pending_count=counts["pending_approval"],
        approved_count=counts["approved_api"],
        failed_count=counts["send_failed"],
    )


@app.route("/update_draft/<mail_id>", methods=["POST"])
def update_draft_route(mail_id):
    new_text = request.form.get("draft_reply", "").strip()
    if new_text:
        update_reply_draft(mail_id, new_text)
    return redirect(url_for("dashboard"))


@app.route("/approve/<mail_id>", methods=["POST"])
def approve_reply(mail_id):
    update_reply_status(mail_id, "approved_api")
    return redirect(url_for("dashboard"))


@app.route("/remove/<mail_id>", methods=["POST"])
def remove_reply(mail_id):
    delete_reply(mail_id)
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
            draft_reply=item["draft_reply"],
        )

        delete_reply(mail_id)

    except Exception as e:
        update_reply_status(
            mail_id=mail_id,
            new_status="send_failed",
            send_error=str(e),
            sent_at=None,
        )

    return redirect(url_for("dashboard"))


# -----------------------------
# Start
# -----------------------------
if __name__ == "__main__":
    print("Starting mailbot app...", flush=True)
    print("IMAP_SERVER:", IMAP_SERVER, flush=True)
    print("MAILBOX:", MAILBOX, flush=True)
    print("MAIL_USER:", MAIL_USER, flush=True)
    print("MAIL_PASS SET:", bool(MAIL_PASS), flush=True)
    print("OPENAI_API_KEY SET:", bool(OPENAI_API_KEY), flush=True)
    print("RESEND_API_KEY SET:", bool(RESEND_API_KEY), flush=True)
    print("AI_FROM_EMAIL:", AI_FROM_EMAIL, flush=True)

    init_db()
    ensure_replies_columns()

    print("Starting polling thread...", flush=True)
    worker = threading.Thread(target=polling_loop, daemon=True)
    worker.start()

    app.run(host="0.0.0.0", port=PORT)
