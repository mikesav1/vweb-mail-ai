import imaplib
import email
import os

IMAP_SERVER = "imap.one.com"
EMAIL = os.getenv("MAIL_USER")
PASSWORD = os.getenv("MAIL_PASS")

def check_mail():
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL, PASSWORD)
    mail.select("inbox")

    status, messages = mail.search(None, "ALL")
    mail_ids = messages[0].split()

    for mail_id in mail_ids[-5:]:
        status, msg_data = mail.fetch(mail_id, "(RFC822)")
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                print("Mail:", msg["subject"])

    mail.logout()

print("Starter mail check...")
check_mail()
