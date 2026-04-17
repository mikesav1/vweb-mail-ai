import imaplib
import email
import os
import time
import json
from datetime import datetime
from email.header import decode_header
from openai import OpenAI
from bs4 import BeautifulSoup

print("APP STARTER NU!!!")

IMAP_SERVER = "imap.one.com"
MAILBOX = "INBOX"
CHECK_INTERVAL_SECONDS = 60
STATE_FILE = "last_mail_id.txt"
PENDING_REPLIES_FILE = "pending_replies.json"

AUTO_IGNORE_CATEGORIES = {"spam", "nyhedsbrev", "automatisk"}
REPLY_CATEGORIES = {"kunde", "vigtig", "ukendt"}


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
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return 0


def save_last_mail_id(mail_id):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(str(mail_id))


def load_pending_replies():
    try:
        with open(PENDING_REPLIES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_pending_replies(data):
    with open(PENDING_REPLIES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def already_saved_reply(mail_id):
    pending = load_pending_replies()
    return any(str(item.get("mail_id")) == str(mail_id) for item in pending)


def save_pending_reply(mail_id, sender, subject, category, summary, reply_needed, draft_reply):
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
            "draft_reply": draft_reply,
            "status": "pending_approval"
        }
    )

    save_pending_replies(pending)


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
    print(f"Sidst behandlet ID: {last_seen}")

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
                        draft_reply=draft_reply
                    )
                    print("SVARUDKAST GEMT TIL GODKENDELSE")
                else:
                    print("INGEN SVAR GEMT")

            except Exception as ai_error:
                print(f"AI-fejl: {ai_error}")

            print("========================================")

    save_last_mail_id(newest_id)
    mail.logout()


if __name__ == "__main__":
    print("KALDER CHECK_MAIL")
    while True:
        try:
            check_mail()
        except Exception as e:
            print(f"Fejl: {e}")

        print(f"Venter {CHECK_INTERVAL_SECONDS} sekunder...")
        time.sleep(CHECK_INTERVAL_SECONDS)
