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
        return ""

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

    return "".join(result)


def classify_mail(subject, sender):
    subject_l = subject.lower()
    sender_l = sender.lower()

    if "bekræft" in subject_l or "verify" in subject_l or "confirmation" in subject_l:
        return "vigtig"

    if "no-reply" in sender_l or "noreply" in sender_l:
        return "automatisk"

    if "newsletter" in subject_l or "nyhedsbrev" in subject_l:
        return "nyhedsbrev"

    if subject_l.startswith("fwd:") or subject_l.startswith("fw:"):
        return "videresendt"

    return "ukendt"


def check_mail():
    print("Tjekker mail...")

    if not EMAIL_USER or not EMAIL_PASS:
        raise ValueError("MAIL_USER eller MAIL_PASS mangler i Railway Variables")

    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select(MAILBOX)

    status, messages = mail.search(None, "UNSEEN")
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

                sender = decode_mime_text(msg.get("From"))
                subject = decode_mime_text(msg.get("Subject"))
                kategori = classify_mail(subject, sender)

                if kategori in ["vigtig", "ukendt"]:
                    print("========================================")
                    print(f"KATEGORI: {kategori.upper()}")
                    print(f"Fra: {sender}")
                    print(f"Emne: {subject}")
                    print("========================================")

    mail.logout()


print("Mail-bot starter...")

while True:
    try:
        check_mail()
    except Exception as e:
        print(f"Fejl: {e}")

    print(f"Venter {CHECK_INTERVAL_SECONDS} sekunder...")
    time.sleep(CHECK_INTERVAL_SECONDS)
