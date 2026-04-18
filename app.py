import imaplib
import email
import os
import time
import sqlite3
import threading
import re
from datetime import datetime, timedelta
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

    source = re.sub(r"[\"<>]", "", source).strip()
    parts = source.split()

    if not parts:
        return "der"

    first = parts[0].strip(" ,.-")
    return first if first else "der"


def next_weekday_date(target_weekday: int) -> str:
    today = datetime.now().date()
    days_ahead = target_weekday - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    target = today + timedelta(days=days_ahead)
    return target.strftime("%d.%m.%Y")


def build_date_hint(body: str) -> str:
    lower = body.lower()

    if "lørdag i næste uge" in lower:
        return f"Lørdag i næste uge er den {next_weekday_date(5)}."
    if "søndag i næste uge" in lower:
        return f"Søndag i næste uge er den {next_weekday_date(6)}."
    if "mandag i næste uge" in lower:
        return f"Mandag i næste uge er den {next_weekday_date(0)}."
    if "tirsdag i næste uge" in lower:
        return f"Tirsdag i næste uge er den {next_weekday_date(1)}."
    if "onsdag i næste uge" in lower:
        return f"Onsdag i næste uge er den {next_weekday_date(2)}."
    if "torsdag i næste uge" in lower:
        return f"Torsdag i næste uge er den {next_weekday_date(3)}."
    if "fredag i næste uge" in lower:
        return f"Fredag i næste uge er den {next_weekday_date(4)}."

    return ""


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


def ai_analyze_email(sender, subject, body):
    client = get_openai_client()
    sender_first_name = extract_first_name(sender)
    date_hint = build_date_hint(body)
    body_preview = body[:5000] if body else "(intet indhold)"

    prompt = f"""
Du er en skarp mailassistent for virksomheden Vweb.

Baggrund:
- Vweb arbejder blandt andet med løsningen VinterGuide.
- VinterGuide bruges til planlægning, overblik, dokumentation og daglig styring i praksis.
- Du svarer som Kim Vase.
- Svar skal være konkrete, hjælpsomme, menneskelige og direkte.
- Du skal bruge det faktiske spørgsmål i mailen aktivt og svare på det, ikke bare skrive et standardsvar.

Vigtige regler:
- Hvis afsenderen hedder Kim, skal svaret begynde med "Hej Kim,"
- Du må ikke forveksle afsender med egne navne fra gamle mails eller signaturer.
- Hvis mailen indeholder et konkret spørgsmål, skal du forsøge at give et konkret svar.
- Brug kun fallback-svar som "jeg vender tilbage" hvis du reelt mangler oplysninger.
- Hvis der spørges om datoer eller dage, og du har et hjælpespor, så brug det.
- Svarudkast må ikke lyde som AI.
- Svarudkast skal være kort, naturligt og direkte.
- Ingen punktopstilling i selve svarudkastet.
- Svarudkast må ALDRIG indeholde pladsholdere som [Dit navn], [Navn], [Firmanavn] eller lignende.
- Svarudkast skal afsluttes med: "Mvh Kim Vase"
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

Emne:
{subject}

Renset mailindhold:
{body_preview}
""".strip()

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt
    )

    return response.output_text.strip()


def normalize_draft_reply(draft_reply, sender):
    if not draft_reply:
        return ""

    text = draft_reply.strip()
    first_name = extract_first_name(sender)

    replacements = {
        "Mvh [Dit navn]": "Mvh Kim Vase",
        "Mvh [dit navn]": "Mvh Kim Vase",
        "Med venlig hilsen [Dit navn]": "Mvh Kim Vase",
        "Med venlig hilsen [dit navn]": "Mvh Kim Vase",
        "[Dit navn]": "Kim Vase",
        "[dit navn]": "Kim Vase",
        "Hej Ulla,": f"Hej {first_name},",
        "Hej ulla,": f"Hej {first_name},",
        "Hej Mailbot,": f"Hej {first_name},",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"^Hej\s+[^,\n]+,", f"Hej {first_name},", text, count=1, flags=re.IGNORECASE)

    if text.startswith(f"Hej {first_name},") and "\n\n" not in text:
        text = text.replace(f"Hej {first_name},", f"Hej {first_name},\n\n", 1)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    content_lines = [line for line in lines if line.lower() not in {f"hej {first_name.lower()},", "mvh kim vase"}]

    if len(" ".join(content_lines).strip()) < 20:
        text = f"Hej {first_name},\n\nTak for din mail. Jeg vender tilbage med et mere præcist svar om det med det samme.\n\nMvh Kim Vase"

    if "Mvh Kim Vase" not in text:
        text = text.rstrip() + "\n\nMvh Kim Vase"

    return text


def send_via_resend(to_email, original_subject, draft_reply, sender):
    if not RESEND_API_KEY:
        raise ValueError("RESEND_API_KEY mangler i Railway Variables")

    if not AI_FROM_EMAIL:
        raise ValueError("AI_FROM_EMAIL mangler i Railway Variables")

    resend.api_key = RESEND_API_KEY

    if original_subject.lower().startswith("re:"):
        subject = original_subject
    else:
        subject = f"Re: {original_subject}"

    draft_reply = normalize_draft_reply(draft_reply, sender)

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
            full_body = get_plain_text_body(msg)
            cleaned_body = strip_quoted_text(full_body)

            print("========================================")
            print(f"NY MAIL (ID {mail_id_int})")
            print(f"Fra: {sender}")
            print(f"Emne: {subject}")
            print("Renset indhold preview:")
            print(cleaned_body[:600] if cleaned_body else "(intet indhold)")
            print("----------------------------------------")
            print("AI analyserer mailen...")

            try:
                ai_result = ai_analyze_email(sender, subject, cleaned_body)
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
                        original_preview=cleaned_body
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
    active_items = load_replies_by_status(["pending_approval", "approved_api", "send_failed"])
    history_items = load_replies_by_status(["sent", "rejected", "archived"])
    counts = get_counts()

    return render_template_string(
        HTML_TEMPLATE,
        active_items=active_items,
        history_items=history_items,
        pending_count=counts["pending_approval"],
        approved_count=counts["approved_api"],
        sent_count=counts["sent"],
        rejected_count=counts["rejected"],
        archived_count=counts["archived"]
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
    print("KALDER CHECK_MAIL / STARTER WEB")
    worker = threading.Thread(target=polling_loop, daemon=True)
    worker.start()
    app.run(host="0.0.0.0", port=PORT)
