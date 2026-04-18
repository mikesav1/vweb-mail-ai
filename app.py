MAILBOT V4.5 – PATCH DEL 1
Formål:
- stoppe botten med at svare til Kim som om Kim var kunde
- tilføje selvtest-logik
- fjerne signatur ved selvtest
- undgå afslutning med “Mvh Kim Vase” ved selvtest

========================
1) ØVERST I app.py
========================

Find området med disse linjer:

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
AI_FROM_EMAIL = os.getenv("AI_FROM_EMAIL")

Tilføj lige under:

MAIL_USER = os.getenv("MAIL_USER", "")

Så det bliver:

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
AI_FROM_EMAIL = os.getenv("AI_FROM_EMAIL")
MAIL_USER = os.getenv("MAIL_USER", "")

========================
2) I init_db()
========================

Find CREATE TABLE replies og tilføj feltet:

is_self_test TEXT,

mellem:
product_context TEXT,
og
subject TEXT,

Det skal ende sådan her i den del:

CREATE TABLE IF NOT EXISTS replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mail_id TEXT UNIQUE,
    saved_at TEXT,
    sender TEXT,
    recipient TEXT,
    product_context TEXT,
    is_self_test TEXT,
    subject TEXT,
    category TEXT,
    requires_reply TEXT,
    summary TEXT,
    draft_reply TEXT,
    original_preview TEXT,
    status TEXT,
    sent_at TEXT,
    send_error TEXT
)

========================
3) I ensure_replies_columns()
========================

Tilføj dette:

        if "is_self_test" not in columns:
            cur.execute("ALTER TABLE replies ADD COLUMN is_self_test TEXT")

Hele funktionen skal ende sådan her:

def ensure_replies_columns():
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(replies)")
        columns = [row["name"] for row in cur.fetchall()]

        if "recipient" not in columns:
            cur.execute("ALTER TABLE replies ADD COLUMN recipient TEXT")
        if "product_context" not in columns:
            cur.execute("ALTER TABLE replies ADD COLUMN product_context TEXT")
        if "is_self_test" not in columns:
            cur.execute("ALTER TABLE replies ADD COLUMN is_self_test TEXT")

        conn.commit()
        conn.close()

========================
4) TILFØJ NY FUNKTION
========================

Læg denne funktion ind et passende sted under extract_reply_email() eller tæt på de andre hjælpefunktioner:

def is_self_test_sender(sender):
    if not MAIL_USER:
        return False
    _, addr = email.utils.parseaddr(sender or "")
    return addr.lower() == MAIL_USER.lower()

========================
5) ERSTAT ai_analyze_email()
========================

Erstat hele funktionen med denne:

def ai_analyze_email(sender, recipient, subject, body, is_self_test=False):
    client = get_openai_client()
    company_context = get_company_context()
    product_key, product_context = get_product_context(recipient, subject, body)
    date_hint = build_date_hint(body)
    body_preview = body[:5000] if body else "(intet indhold)"

    self_rule = ""
    if is_self_test:
        self_rule = '''
VIGTIGT:
Denne mail er sendt af Kim Vase selv som test eller intern prøve.
Du må IKKE skrive et normalt kundesvar som om Kim er en kunde.
Du må IKKE starte med "Hej Kim,".
Du må IKKE afslutte med "Mvh Kim Vase".
Når det er en selvtest, skal du i stedet skrive et kort internt testnotat eller skrive "intet" hvis der ikke giver mening at svare.
Format ved selvtest:
SVARUDKAST: intet
'''.strip()

    prompt = f'''
Du er en skarp mailassistent for virksomheden Vweb.

Overordnet virksomhedskontekst:
{company_context if company_context else "Ingen company_context.txt fundet."}

Aktiv produktkontekst:
Produktnøgle: {product_key}
{product_context if product_context else "Ingen specifik produktkontekst fundet. Brug kun virksomhedskontekst."}

Baggrund:
- Du svarer som Kim Vase, medmindre dette er en selvtest.
- Svar skal være konkrete, hjælpsomme, menneskelige og direkte.
- Du skal bruge det faktiske spørgsmål i mailen aktivt og svare på det, ikke bare skrive et standardsvar.

Vigtige regler:
- Hvis dette IKKE er en selvtest og afsenderen hedder Kim, skal svaret begynde med "Hej Kim,"
- Du må ikke forveksle afsender med egne navne fra gamle mails eller signaturer.
- Hvis mailen indeholder et konkret spørgsmål, skal du forsøge at give et konkret svar.
- Brug kun fallback-svar som "jeg vender tilbage" hvis du reelt mangler oplysninger.
- Hvis der spørges om datoer eller dage, og du har et hjælpespor, så brug det.
- Hvis spørgsmålet handler om et produkt, så svar ud fra den relevante produktkontekst.
- Svarudkast må ikke lyde som AI.
- Svarudkast skal være kort, naturligt og direkte.
- Ingen punktopstilling i selve svarudkastet.
- Svarudkast må ALDRIG indeholde pladsholdere som [Dit navn], [Navn], [Firmanavn] eller lignende.
- Hvis dette IKKE er en selvtest, skal Svarudkast afsluttes med: "Mvh Kim Vase"
- Hvis der ikke skal svares, skriv "intet".

Hjælpespor:
{date_hint if date_hint else "Ingen særlige dato-hints."}

{self_rule}

Returnér KUN i dette format:

KATEGORI: <spam|nyhedsbrev|automatisk|kunde|vigtig|ukendt>
KRÆVER_SVAR: <ja|nej>
RESUMÉ: <kort opsummering>
SVARUDKAST: <kort svar på dansk, eller skriv "intet">

Afsender:
{sender}

Sendt til:
{recipient}

Emne:
{subject}

Renset mailindhold:
{body_preview}
'''.strip()

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt
    )

    return response.output_text.strip(), product_key

========================
6) ERSTAT normalize_draft_reply()
========================

Erstat hele funktionen med denne:

def normalize_draft_reply(draft_reply, sender, is_self_test=False):
    if not draft_reply:
        return ""

    if is_self_test:
        return draft_reply.strip()

    text = draft_reply.strip()
    first_name = extract_first_name(sender)

    replacements = {
        "Mvh [Dit navn]": "Mvh Kim Vase",
        "Mvh [dit navn]": "Mvh Kim Vase",
        "Med venlig hilsen [Dit navn]": "Mvh Kim Vase",
        "Med venlig hilsen [dit navn]": "Mvh Kim Vase",
        "[Dit navn]": "Kim Vase",
        "[dit navn]": "Kim Vase",
        "Hej Ulla,": f"Hej {first_name},",
        "Hej ulla,": f"Hej {first_name},",
        "Hej Mailbot,": f"Hej {first_name},",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"^Hej\s+[^,\n]+,", f"Hej {first_name},", text, count=1, flags=re.IGNORECASE)

    if text.startswith(f"Hej {first_name},") and "\n\n" not in text:
        text = text.replace(f"Hej {first_name},", f"Hej {first_name},\n\n", 1)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    content_lines = [line for line in lines if line.lower() not in {f"hej {first_name.lower()},", "mvh kim vase"}]

    if len(" ".join(content_lines).strip()) < 20:
        text = f"Hej {first_name},\n\nTak for din mail. Jeg vender tilbage med et mere præcist svar om det med det samme.\n\nMvh Kim Vase"

    if "Mvh Kim Vase" not in text:
        text = text.rstrip() + "\n\nMvh Kim Vase"

    return text

========================
7) ERSTAT send_via_resend()
========================

Erstat hele funktionen med denne:

def send_via_resend(to_email, original_subject, draft_reply, sender, is_self_test=False):
    if not RESEND_API_KEY:
        raise ValueError("RESEND_API_KEY mangler i Railway Variables")
    if not AI_FROM_EMAIL:
        raise ValueError("AI_FROM_EMAIL mangler i Railway Variables")

    resend.api_key = RESEND_API_KEY

    if original_subject.lower().startswith("re:"):
        subject = original_subject
    else:
        subject = f"Re: {original_subject}"

    draft_reply = normalize_draft_reply(draft_reply, sender, is_self_test=is_self_test)

    signature_html = ""
    if not is_self_test:
        signature_html = '''
        <br><br>
        <hr style="border:none;border-top:1px solid #ddd;">
        <table style="font-family: Arial, sans-serif; font-size:14px; color:#222;">
          <tr>
            <td style="padding-right:15px; vertical-align:top;">
              <a href="https://vweb.info" target="_blank">
                <img src="https://vweb.info/images/vweb-logo.svg" alt="Vweb logo" width="150">
              </a>
            </td>
            <td style="vertical-align:top;">
              <b>Ulla Vase</b><br>
              Syrenvej 5<br>
              7200 Grindsted<br>
              Tlf.: 91 83 07 25<br>
              E-mail: <a href="mailto:ulla@vweb.info">ulla@vweb.info</a>
            </td>
          </tr>
        </table>
        '''

    html = f"<p>{draft_reply.replace(chr(10), '<br>')}</p>{signature_html}"

    params = {
        "from": AI_FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "html": html
    }

    return resend.Emails.send(params)
MAILBOT V4.5 – PATCH DEL 2
Formål:
- gemme selvtest i databasen
- vise selvtest i oversigten
- bruge selvtest-logik når mails læses og sendes

========================
1) ERSTAT load_replies_by_status()
========================

def load_replies_by_status(statuses):
    placeholders = ",".join("?" for _ in statuses)
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(f'''
            SELECT mail_id, saved_at, sender, recipient, product_context, is_self_test, subject, category, requires_reply,
                   summary, draft_reply, original_preview, status, sent_at, send_error
            FROM replies
            WHERE status IN ({placeholders})
            ORDER BY datetime(COALESCE(sent_at, saved_at)) DESC
        ''', tuple(statuses))
        rows = cur.fetchall()
        conn.close()
        return [dict(row) for row in rows]

========================
2) ERSTAT save_pending_reply()
========================

def save_pending_reply(mail_id, sender, recipient, product_context, is_self_test, subject, category, summary, reply_needed, draft_reply, original_preview):
    if already_saved_reply(mail_id):
        return

    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO replies (
                mail_id, saved_at, sender, recipient, product_context, is_self_test, subject, category, requires_reply,
                summary, draft_reply, original_preview, status, sent_at, send_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            str(mail_id),
            datetime.utcnow().isoformat() + "Z",
            sender,
            recipient,
            product_context,
            "ja" if is_self_test else "nej",
            subject,
            category,
            reply_needed,
            summary,
            draft_reply,
            original_preview[:2000],
            "pending_approval",
            None,
            None
        ))
        conn.commit()
        conn.close()

========================
3) ERSTAT get_reply_by_id()
========================

def get_reply_by_id(mail_id):
    with file_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('''
            SELECT mail_id, saved_at, sender, recipient, product_context, is_self_test, subject, category, requires_reply,
                   summary, draft_reply, original_preview, status, sent_at, send_error
            FROM replies
            WHERE mail_id = ?
        ''', (str(mail_id),))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

========================
4) TEMPLATE – VIS SELVTEST
========================

I HTML_TEMPLATE:
Find linjen:

<div class="row"><span class="label">Produktkontekst:</span> {{ item.product_context }}</div>

Tilføj lige under:

<div class="row"><span class="label">Selvtest:</span> {{ item.is_self_test }}</div>

Gør det både under Aktive mails og Historik.

========================
5) ERSTAT check_mail()
========================

def check_mail():
    print("Mail-bot starter...")
    print("Tjekker mail...")

    email_user = os.getenv("MAIL_USER")
    email_pass = os.getenv("MAIL_PASS")

    if not email_user or not email_pass:
        raise ValueError("MAIL_USER eller MAIL_PASS mangler i Railway Variables")

    last_seen = get_last_mail_id()

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

    if last_seen is None:
        print("Ingen tidligere state fundet. Springer gamle mails over første gang.")
        save_last_mail_id(newest_id)
        mail.logout()
        return

    print(f"Sidst behandlet ID: {last_seen}")

    for mail_id in mail_ids:
        mail_id_int = int(mail_id)
        if mail_id_int <= last_seen:
            continue

        status, msg_data = mail.fetch(mail_id, "(RFC822)")
        if status != "OK":
            print(f"Kunne ikke hente mail {mail_id_int}")
            continue

        for response_part in msg_data:
            if not isinstance(response_part, tuple):
                continue

            msg = email.message_from_bytes(response_part[1])

            sender = decode_mime_text(msg.get("From"))
            recipient = extract_recipient(msg) or os.getenv("MAIL_USER", "")
            subject = decode_mime_text(msg.get("Subject"))
            full_body = get_plain_text_body(msg)
            cleaned_body = strip_quoted_text(full_body)
            self_test = is_self_test_sender(sender)

            try:
                ai_result, product_key = ai_analyze_email(
                    sender=sender,
                    recipient=recipient,
                    subject=subject,
                    body=cleaned_body,
                    is_self_test=self_test
                )
                parsed = parse_ai_result(ai_result)

                category = parsed["category"]
                requires_reply = parsed["requires_reply"]
                summary = parsed["summary"]
                draft_reply = parsed["draft_reply"]

                if category in REPLY_CATEGORIES and requires_reply == "ja" and draft_reply.lower() != "intet":
                    save_pending_reply(
                        mail_id=mail_id_int,
                        sender=sender,
                        recipient=recipient,
                        product_context=product_key,
                        is_self_test=self_test,
                        subject=subject,
                        category=category,
                        summary=summary,
                        reply_needed=requires_reply,
                        draft_reply=draft_reply,
                        original_preview=cleaned_body
                    )
                    print("SVARUDKAST GEMT TIL GODKENDELSE")
                else:
                    print("INGEN SVAR GEMT")

            except Exception as ai_error:
                print(f"AI-fejl: {ai_error}")

    save_last_mail_id(newest_id)
    mail.logout()

========================
6) ERSTAT send_reply()
========================

@app.route("/send/<mail_id>", methods=["POST"])
def send_reply(mail_id):
    item = get_reply_by_id(mail_id)
    if not item:
        return redirect(url_for("dashboard"))

    if item.get("status") not in {"approved_api", "send_failed"}:
        return redirect(url_for("dashboard"))

    try:
        to_email = extract_reply_email(item["sender"])
        if not to_email:
            raise ValueError("Kunne ikke udlede modtagerens mailadresse")

        result = send_via_resend(
            to_email=to_email,
            original_subject=item["subject"],
            draft_reply=item["draft_reply"],
            sender=item["sender"],
            is_self_test=(item.get("is_self_test") == "ja")
        )

        update_reply_status(
            mail_id=mail_id,
            new_status="sent",
            send_error=None,
            sent_at=datetime.utcnow().isoformat() + "Z"
        )
        print("RESEND RESULT:", result)

    except Exception as e:
        update_reply_status(
            mail_id=mail_id,
            new_status="send_failed",
            send_error=str(e),
            sent_at=None
        )

    return redirect(url_for("dashboard"))

========================
7) NEDERST I FILEN
========================

Sørg for at den nederste del ser sådan ud:

if __name__ == "__main__":
    init_db()
    ensure_replies_columns()
    print("KALDER CHECK_MAIL / STARTER WEB")
    worker = threading.Thread(target=polling_loop, daemon=True)
    worker.start()
    app.run(host="0.0.0.0", port=PORT)
