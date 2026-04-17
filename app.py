import imaplib
import email
import os
import time
from email.header import decode_header
from openai import OpenAI

IMAP_SERVER = "imap.one.com"
MAILBOX = "INBOX"
CHECK_INTERVAL_SECONDS = 60
STATE_FILE = "last_mail_id.txt"

EMAIL_USER = os.getenv("MAIL_USER")
EMAIL_PASS = os.getenv("MAIL_PASS")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)


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


def get_plain_text_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition") or "")

            if content_type == "text/plain" and "attachment" not in content_disposition.lower():
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        return payload.decode(charset, errors="replace").strip()
                    except Exception:
                        return payload.decode("utf-8", errors="replace").strip()

        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition") or "")

            if content_type == "text/html" and "attachment" not in content_disposition.lower():
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        html_text = payload.decode(charset, errors="replace")
                    except Exception:
                        html_text = payload.decode("utf-8", errors="replace")
                    return html_text.strip()
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                return payload.decode(charset, errors="replace").strip()
            except Exception:
                return payload.decode("utf-8", errors="replace").strip()

    return ""


def get_last_mail_id():
    try:
        with open(STATE_FILE, "r") as f:
            return int(f.read().strip())
    except Exception:
        return 0


def save_last_mail_id(mail_id):
    with open(STATE_FILE, "w") as f:
        f.write(str(mail_id))


def ai_analyze_email(sender, subject, body):
    body_preview = body[:4000] if body else ""

    prompt = f"""
Du er en skarp mailassistent for en mindre dansk virksomhed.

Din opgave:
1. Forstå mailen
2. Klassificér den
3. Vurdér om den kræver svar
4. Lav et kort svarudkast på dansk i en naturlig menneskelig tone
5. Ingen AI-agtige formuleringer
6. Ingen punktopstilling i selve svarudkastet
7. Svar skal lyde kort, direkte og almindeligt

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
        model="gpt-5.4",
        input=prompt
    )

    return response.output_text.strip()


def check_mail():
    print("Mail-bot starter...")
    print("Tjekker mail...")

    if not EMAIL_USER or not EMAIL_PASS:
        raise ValueError("MAIL_USER eller MAIL_PASS mangler i Railway Variables")

    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY mangler i Railway Variables")

    last_seen = get_last_mail_id()
    print(f"Sidst behandlet ID: {last_seen}")

    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_USER, EMAIL_PASS)
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
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])

                sender = decode_mime_text(msg.get("From"))
                subject = decode_mime_text(msg.get("Subject"))
                body = get_plain_text_body(msg)

                print("========================================")
                print(f"NY MAIL (ID {mail_id_int})")
                print(f"Fra: {sender}")
                print(f"Emne: {subject}")
                print("Indhold preview:")
                print((body[:500] if body else "(intet indhold)") )
                print("----------------------------------------")
                print("AI analyserer mailen...")

                try:
                    ai_result = ai_analyze_email(sender, subject, body)
                    print(ai_result)
                except Exception as ai_error:
                    print(f"AI-fejl: {ai_error}")

                print("========================================")

    save_last_mail_id(newest_id)
    mail.logout()


while True:
    try:
        check_mail()
    except Exception as e:
        print(f"Fejl: {e}")

    print(f"Venter {CHECK_INTERVAL_SECONDS} sekunder...")
    time.sleep(CHECK_INTERVAL_SECONDS)
