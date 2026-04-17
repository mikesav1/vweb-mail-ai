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
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                return payload.decode(charset, errors="replace").strip()
            except Exception:
                return payload.decode("utf-8", errors="replace").strip()

    return ""


def classify_mail(subject, sender, body):
    subject_l = subject.lower()
    sender_l = sender.lower()
    body_l = body.lower()

    if "bekræft" in subject_l or "verify" in subject_l or "confirmation" in subject_l:
        return "vigtig"

    if "no-reply" in sender_l or "noreply" in sender_l:
        return "automatisk"

    if "newsletter" in subject_l or "nyhedsbrev" in subject_l:
        return "nyhedsbrev"

    if subject_l.startswith("fwd:") or subject_l.startswith("fw:"):
        return "videresendt"

    if "faktura" in subject_l or "betaling" in subject_l or "invoice" in subject_l:
        return "vigtig"

    if "ordre" in subject_l or "order" in subject_l:
        return "vigtig"

    if "hej" in body_l or "hello" in body_l or "kontakt" in body_l:
        return "ukendt"

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
                body = get_plain_text_body(msg)

                kategori = classify_mail(subject, sender, body)

                if kategori in ["vigtig", "ukendt"]:
                    print("========================================")
                    print(f"KATEGORI: {kategori.upper()}")
                    print(f"Fra: {sender}")
                    print(f"Emne: {subject}")
                    print("Indhold preview:")
                    print(body[:300] if body else "(intet indhold fundet)")
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
