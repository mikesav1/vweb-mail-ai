import imaplib
import email
import time
from email.header import decode_header

IMAP_SERVER = "imap.one.com"
EMAIL = "ulla@vweb.info"
PASSWORD = "DIT_KODEORD"


def decode_text(text):
    if text is None:
        return ""
    decoded, charset = decode_header(text)[0]
    if isinstance(decoded, bytes):
        return decoded.decode(charset or "utf-8", errors="ignore")
    return decoded


def classify_mail(subject, sender):
    subject = subject.lower()
    sender = sender.lower()

    # Simpel klassificering
    if "verify" in subject or "bekræft" in subject:
        return "vigtig"
    if "fwd" in subject:
        return "videresendt"
    if "no-reply" in sender or "noreply" in sender:
        return "automatisk"
    if "newsletter" in subject or "nyhedsbrev" in subject:
        return "nyhedsbrev"
    
    return "ukendt"


def check_mail():
    print("Tjekker mail...")

    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL, PASSWORD)
    mail.select("inbox")

    # KUN nye mails
    status, messages = mail.search(None, "UNSEEN")
    mail_ids = messages[0].split()

    print(f"Nye mails fundet: {len(mail_ids)}")

    for mail_id in mail_ids:
        status, msg_data = mail.fetch(mail_id, "(RFC822)")

        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])

                subject = decode_text(msg["subject"])
                sender = decode_text(msg["from"])

                kategori = classify_mail(subject, sender)

                # KUN vis relevante mails
                if kategori in ["vigtig", "ukendt"]:
                    print("====================================")
                    print(f"KATEGORI: {kategori.upper()}")
                    print(f"Fra: {sender}")
                    print(f"Emne: {subject}")
                    print("====================================\n")

    mail.logout()


print("Mail-bot starter...")

while True:
    try:
        check_mail()
    except Exception as e:
        print("Fejl:", e)

    print("Venter 60 sekunder...\n")
    time.sleep(60)
