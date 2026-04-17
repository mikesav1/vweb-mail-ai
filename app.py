import imaplib
import email
import os
import time
from email.header import decode_header
from openai import OpenAI

print("APP STARTER NU!!!")

IMAP_SERVER = "imap.one.com"
MAILBOX = "INBOX"
CHECK_INTERVAL_SECONDS = 60
STATE_FILE = "last_mail_id.txt"


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
                        return payload.decode(charset, errors="replace").strip()
                    except Exception:
                        return payload.decode("utf-8", errors="replace").strip()
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                return payload.decode(charset, errors="replace").strip()
            except Exception:
                return payload.decode("utf-8", errors="replace").strip()

    return "(intet indhold)"


def get_last_mail_id():
    try:
        with open(STATE_FILE, "r") as f:
            return int(f.read().strip())
    except Exception:
        return 0


def save_last_mail_id(mail_id):
    with open(STATE_FILE, "w") as f:
        f.write(str(mail_id))


def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    print("OPENAI_API_KEY fundet:", bool(api_key))

    if not api_key:
        raise ValueError("OPENAI_API_KEY mangler i Railway Variables")

    return OpenAI(api_key=api_key)


def ai_analyze_email(sender, subject, body):
    client = get_openai_client()
    body_preview = body[:4000] if body else "(intet indhold)"

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
        model="gpt-4.1-mini",
        input=prompt
    )

    result = response.output_text.strip()
    return result


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
                print(body[:500] if body else "(intet indhold)")
                print("----------------------------------------")
                print("AI analyserer mailen...")

                try:
                    ai_result = ai_analyze_email(sender, subject, body)
                    print("AI RESULTAT:")
                    print("=================================")
                    print(ai_result)
                    print("=================================")
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
