import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'monitoring'))
from monitoring.api import _post_ai_request


class NSEReportGenerator:
    def __init__(self, scanner, llm_api_key: str, llm_api_url: str, llm_model: str):
        self.scanner = scanner
        self.llm_api_key = llm_api_key
        self.llm_api_url = llm_api_url
        self.llm_model = llm_model

    def generate_morning_note(self) -> str:
        candidates = self.scanner.scan()
        if not candidates:
            return "No NSE candidates found today."

        facts = "\n".join(
            f"- {c['ticker']}: {c['price']:.2f} KES, 5d {c['chg_5d']:+.2f}%, "
            f"vol ratio {c['vol_ratio']:.2f}"
            for c in candidates
        )
        prompt = (
            "Write a 150-word pre-market note for Nairobi Securities Exchange "
            "retail investors. Plain English. No hype. No guarantees. End with "
            "'Not financial advice.'\n\nFacts:\n" + facts
        )
        payload = {
            "model": self.llm_model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a financial analyst writing a daily market note for Kenyan retail investors. "
                        "Be factual, concise, and avoid hype or guaranteed returns. "
                        "Do not provide personalized investment advice."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        }
        try:
            result = _post_ai_request(self.llm_api_url, self.llm_api_key, payload)
            body = result["choices"][0]["message"]["content"].strip()
            return f"📈 *NSE Morning Brief*\n\n{body}\n\nTop candidates:\n{facts}"
        except Exception:  # noqa: BLE001
            # Fallback to a simple template if LLM fails
            return f"📈 *NSE Morning Brief*\n\nMarket data shows {len(candidates)} candidates.\n\nTop candidates:\n{facts}\n\nNot financial advice."