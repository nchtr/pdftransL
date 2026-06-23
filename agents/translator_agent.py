from openai import OpenAI
from dotenv import load_dotenv
import os
import logging
load_dotenv()
logging.basicConfig(level=logging.INFO)
class TranslatorAgent:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1"
        )
    
    def translate(self, text):
        try:

            logging.info("Translation started")

            response = self.client.chat.completions.create(
                model="nex-agi/nex-n2-pro:free",

                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Translate the following scientific markdown text "
                            "from English to Russian. "
                            "Preserve ALL markdown formatting, "
                            "LaTeX formulas, tables, headers and structure. "
                            "Return ONLY translated markdown text "
                            "without explanations or comments."
                        )
                    },
                    {
                        "role": "user",
                        "content": text
                    }
                ],

                temperature=0.1
            )

            result = response.choices[0].message.content

            logging.info("Translation finished")

            if result is None:
                return ""

            return result

        except Exception as e:

            logging.error(f"Translation error: {e}")

            return ""