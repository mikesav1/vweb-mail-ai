from openai import OpenAI
import os

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def analyze_mail(subject, sender, body):
    print("AI analyserer mailen...")

    prompt = f"""
Du er en assistent der analyserer emails.

Svar KUN sådan her:

KATEGORI: (kunde / spam / vigtig / ukendt)
KRÆVER_SVAR: (ja/nej)
RESUMÉ: (kort forklaring)
SVARUDKAST: (kort svar hvis relevant)

Email:
Fra: {sender}
Emne: {subject}
Indhold:
{body}
"""

    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=prompt
        )

        result = response.output_text.strip()

        print("AI RESULTAT:")
        print("=================================")
        print(result)
        print("=================================")

        return result

    except Exception as e:
        print("AI FEJL:", e)
        return "Fejl i AI"
