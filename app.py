import imaplib
import email
import os
import time

IMAP_SERVER = "imap.one.com"
EMAIL_USER = os.getenv("MAIL_USER")
EMAIL_PASS = os.getenv("MAIL_PASS")

def check_mail():
    print("Tjekker mail...")

    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select("inbox")

    status, messages = mail.search(None, "UNSEEN")
    mail_ids = messages[0].split()

    print("Nye mails:", len(mail_ids))

    for mail_id in mail_ids:
        status, msg_data = mail.fetch(mail_id, "(RFC822)")
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                print("Mail:", msg.get("subject"))

    mail.logout()

while True:
    check_mail()
    time.sleep(60)
