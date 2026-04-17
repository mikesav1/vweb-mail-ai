import imaplib
import email
import os
import time
from email.header import decode_header

IMAP_SERVER = "imap.one.com"
MAILBOX = "INBOX"
CHECK_INTERVAL_SECONDS = 60

EMAIL_USER = os.getenv("MAIL_USER")
EMAIL_PASS = os.getenv("MAIL_PASS")


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


def check_mail():
    print("Mail-bot starter...")
    print("Tjekker mail...")

    if not EMAIL_USER or not EMAIL_PASS:
        raise ValueError("MAIL_USER eller MAIL_PASS mangler i Railway Variables")

    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_USER, EMAIL_PASS)

    status, mailbox_info = mail.select(MAILBOX)
    print(f"Valgt mappe: {MAILBOX}")
    print(f"Select status: {status}")
    print(f"Mailbox info: {mailbox_info}")

    if status != "OK":
        print("Kunne ikke åbne INBOX")
        mail.logout()
        return

    # Hent ALLE mails til test
    status, messages = mail.search(None, "ALL")
    if status != "OK":
        print("Kunne ikke søge efter mails")
        mail.logout()
        return

    mail_ids = messages[0].split()
    print(f"Samlet antal mails i INBOX: {len(mail_ids)}")

    sidste_10 = mail_ids[-10:]
    print(f"Viser de sidste {len(sidste_10)} mails")

    for mail_id in sidste_10:
        status, msg_data = mail.fetch(mail_id, "(RFC822)")
        if status != "OK":
            print(f"Kunne ikke hente mail {mail_id}")
            continue

        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])

                sender = decode_mime_text(msg.get("From"))
                subject = decode_mime_text(msg.get("Subject"))
                date = decode_mime_text(msg.get("Date"))

                print("========================================")
                print(f"Mail ID: {mail_id.decode()}")
                print(f"Fra: {sender}")
                print(f"Emne: {subject}")
                print(f"Dato: {date}")
                print("========================================")

    mail.logout()


while True:
    try:
        check_mail()
    except Exception as e:
        print(f"Fejl: {e}")

    print(f"Venter {CHECK_INTERVAL_SECONDS} sekunder...")
    time.sleep(CHECK_INTERVAL_SECONDS)
