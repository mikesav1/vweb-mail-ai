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


def decode_mime_header(value):
    if not value:
        return "(intet emne)"

    parts = decode_header(value)
    decoded_text = []

    for part, charset in parts:
        if isinstance(part, bytes):
            try:
                decoded_text.append(part.decode(charset or "utf-8", errors="replace"))
            except Exception:
                decoded_text.append(part.decode("utf-8", errors="replace"))
        else:
            decoded_text.append(part)

    return "".join(decoded_text)


def check_mail():
    print("Tjekker mail...")

    if not EMAIL_USER or not EMAIL_PASS:
        raise ValueError("MAIL_USER eller MAIL_PASS mangler i Railway Variables")

    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select(MAILBOX)

    status, messages = mail.search(None, "ALL")
    if status != "OK":
        print("Kunne ikke hente ulæste mails.")
        mail.logout()
        return

    mail_ids = messages[0].split()
    print(f"Nye mails fundet: {len(mail_ids)}")

    for mail_id in mail_ids:
        status, msg_data = mail.fetch(mail_id, "(RFC822)")
        if status != "OK":
            print(f"Kunne ikke hente mail {mail_id}")
            continue

        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])

                subject = decode_mime_header(msg.get("Subject"))
                sender = decode_mime_header(msg.get("From"))

                print(f"Fra: {sender}")
                print(f"Emne: {subject}")
                print("-" * 60)

    mail.logout()


print("Mail-bot starter...")

while True:
    try:
        check_mail()
    except Exception as e:
        print(f"Fejl: {e}")

    print(f"Venter {CHECK_INTERVAL_SECONDS} sekunder...")
    time.sleep(CHECK_INTERVAL_SECONDS)
