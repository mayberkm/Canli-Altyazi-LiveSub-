import json
import time
from urllib.parse import urljoin

import requests

from .base import Translator
from .ollama_provider import clean_translation


SYSTEM = """You are a professional video game subtitle translator.
Translate the provided English subtitle into the requested target language.
Rules: Output only the translated subtitle. Preserve meaning, tone and emotion. Use natural conversational language. Preserve character names and proper nouns. Respect previous dialogue context. Do not explain, add quotation marks, summarize, censor or soften dialogue. Keep concise for real-time subtitles."""


class LlamaCppProvider(Translator):
    def __init__(self, base_url="http://127.0.0.1:8080", timeout=45):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def models(self):
        response = requests.get(urljoin(self.base_url + "/", "v1/models"), timeout=3)
        response.raise_for_status()
        return [x.get("id", "") for x in response.json().get("data", []) if x.get("id")]

    def translate(self, text, target, context=(), model=None, token_callback=None):
        user = f"Target language: {target}\nPrevious dialogue: {' | '.join(context[-4:])}\nSubtitle: {text}"
        body = {"model": model or "local-model", "temperature": 0,
                "max_tokens": 128, "stream": True,
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}
        started = time.perf_counter()
        first_token = None
        chunks = []
        url = urljoin(self.base_url + "/", "v1/chat/completions")
        with requests.post(url, json=body, stream=True, timeout=(5, self.timeout)) as response:
            response.raise_for_status()
            for raw in response.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data:"):
                    continue
                data = raw[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                    choice = (event.get("choices") or [{}])[0]
                    piece = choice.get("delta", {}).get("content", "")
                    if piece:
                        if first_token is None:
                            first_token = (time.perf_counter() - started) * 1000
                        chunks.append(piece)
                        if token_callback:
                            token_callback(piece)
                except (ValueError, IndexError):
                    continue
        return {"text": clean_translation("".join(chunks).strip()),
                "first_token_ms": first_token,
                "elapsed_ms": (time.perf_counter() - started) * 1000}
