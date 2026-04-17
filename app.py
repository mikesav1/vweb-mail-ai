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

STATE_FILE = "last_mail_id.txt"


def decode_mime_text(value):
    if not value:
        return "(intet emne)"

    parts = decode_header(value)
    result = []

    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)

    text = "".join(result).strip()
    return text if text else "(intet emne)"


def get_last_mail_id():
    try:
        with open(STATE_FILE, "r") as f:
            return int(f.read().strip())
    except:
        return 0


def save_last_mail_id(mail_id):
    with open(STATE_FILE, "w") as f:
        f.write(str(mail_id))


def check_mail():
    print("Tjekker mail...")

    if not EMAIL_USER or not EMAIL_PASS:
        raise ValueError("MAIL_USER eller MAIL_PASS mangler")

    last_seen = get_last_mail_id()
    print(f"Sidst behandlet ID: {last_seen}")

    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select(MAILBOX)

    status, messages = mail.search(None, "ALL")
    mail_ids = messages[0].split()

    if not mail_ids:
        print("Ingen mails fundet")
        mail.logout()
        return

    nyeste_id = int(mail_ids[-1])

    print(f"Nyeste mail ID: {nyeste_id}")

    for mail_id in mail_ids:
        mail_id_int = int(mail_id)

        if mail_id_int <= last_seen:
            continue

        status, msg_data = mail.fetch(mail_id, "(RFC822)")

        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])

                sender = decode_mime_text(msg.get("From"))
                subject = decode_mime_text(msg.get("Subject"))

                print("========================================")
                print(f"NY MAIL (ID {mail_id_int})")
                print(f"Fra: {sender}")
                print(f"Emne: {subject}")
                print("========================================")

    save_last_mail_id(nyeste_id)

    mail.logout()


print("Mail-bot starter...")

while True:
    try:
        check_mail()
    except Exception as e:
        print("Fejl:", e)

    print(f"Venter {CHECK_INTERVAL_SECONDS} sekunder...")
    time.sleep(CHECK_INTERVAL_SECONDS)
