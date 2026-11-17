import json
import re
import time

import requests

from .base import Translator


def clean_translation(text):
    if not text:
        return ""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    cleaned_lines = []
    seen = set()
    for line in lines:
        # Skip dialogue context lines if echoed
        if re.match(r"^(?:önceki\s+(?:konuşma|diyalog|metin)|previous\s+(?:dialogue|context)|context|note|açıklama)\s*[:：]", line, re.IGNORECASE):
            continue
        # Discard leaked instruction prefixes
        if re.search(r"(?:altyaz[ıi]\s+metni\s+kesinlikle|türkçeye\s+çevrilmelidir|translate\s+this\s+english)", line, re.IGNORECASE):
            stripped_after_colon = re.sub(
                r"^(?:bu\s+ingilizce\s+altyaz[ıi].*?[:：]|translate\s+this\s+.*?[:：]|.*?türkçeye\s+çevrilmelidir\s*[:：]?)\s*",
                "",
                line,
                flags=re.IGNORECASE,
            ).strip()
            if not stripped_after_colon or stripped_after_colon == line:
                continue
            line = stripped_after_colon
        line = re.sub(
            r"^(?:bu\s+ingilizce\s+altyaz[ıi].*?[:：]|translate\s+this\s+.*?[:：]|türkçeye\s+çevrilmelidir\s*[:：])\s*",
            "",
            line,
            flags=re.IGNORECASE,
        ).strip()
        # Strip prefixes like "Altyazı:", "Çeviri:", "Translation:", "Subtitle:", etc.
        line = re.sub(
            r"^(?:altyaz[ıi]|çeviri|translation|subtitle|turkish|türkçe|ingilizce|english|hedef|kaynak|metin|diyalog)\s*[:：]\s*",
            "",
            line,
            flags=re.IGNORECASE
        ).strip()
        # Strip surrounding quotation marks
        if (line.startswith('"') and line.endswith('"')) or (line.startswith("'") and line.endswith("'")):
            line = line[1:-1].strip()
        # Discard hallucinated repetition loops, chat artifacts, or garbled all-caps tokens
        if re.search(r"\b(?:döngüyü\s+(?:tekrar|yeniden)|aynı\s+döngü)\b", line, re.IGNORECASE):
            continue
        if re.search(r"(?:diğer\s+sorular|gouda\s+peynir|olama\s+listesi|^bash$)", line, re.IGNORECASE):
            continue
        if re.search(r"[A-Z]{15,}", line):
            continue
        c_line = line.casefold()
        if c_line not in seen and len(c_line) > 1:
            seen.add(c_line)
            cleaned_lines.append(line)
        # Subtitle translations are concise (at most 2 lines)
        if len(cleaned_lines) >= 2:
            break
    return "\n".join(cleaned_lines).strip()


def parse_batch_response(raw_text, count):
    lines = [l.strip() for l in raw_text.strip().splitlines() if l.strip()]
    results = [""] * count
    matched = 0
    for l in lines:
        m = re.match(r"^(?:\[(\d+)\]|(\d+)[.)])\s*(.+)", l)
        if m:
            num = int(m.group(1) or m.group(2)) - 1
            content = m.group(3).strip()
            if 0 <= num < count:
                results[num] = content
                matched += 1

    if matched == 0 and len(lines) == count:
        results = lines
    elif matched < count:
        unfilled = [i for i, r in enumerate(results) if not r]
        extra_lines = [l for l in lines if not re.match(r"^(?:\[(\d+)\]|(\d+)[.)])", l)]
        for idx, line in zip(unfilled, extra_lines):
            results[idx] = line

    return results


class OllamaProvider(Translator):
    """Ollama native API client. Outputs clean subtitles without metadata prefixes."""

    def __init__(self, base_url="http://127.0.0.1:11434", timeout=90):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def models(self):
        response = requests.get(self.base_url + "/api/tags", timeout=3)
        response.raise_for_status()
        return [item["name"] for item in response.json().get("models", []) if item.get("name")]

    def warm(self, model):
        # Ollama keeps a model loaded for the lifetime of the daemon until explicitly unloaded.
        response = requests.post(self.base_url + "/api/generate", json={
            "model": model, "prompt": "", "stream": False, "keep_alive": -1,
            "options": {"temperature": 0, "num_predict": 1},
        }, timeout=(3, self.timeout))
        response.raise_for_status()
        return response.json()

    def translate(self, text, target, context=(), model=None, token_callback=None, source="English", cancel_check=None):
        prompt = f"Translate the following segment into {target}, without additional explanation.\n\n{text}"
        messages = [{"role": "user", "content": prompt}]

        started = time.perf_counter()
        first_token = None
        chunks = []
        with requests.post(self.base_url + "/api/chat", json={
            "model": model or "ali6parmak/hy-mt1.5:1.8b", "messages": messages,
            "stream": True, "keep_alive": -1,
            "options": {"temperature": 0, "num_predict": 64},
        }, stream=True, timeout=(5, self.timeout)) as response:
            response.raise_for_status()
            for raw in response.iter_lines():
                if cancel_check and cancel_check():
                    break
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                piece = (event.get("message") or {}).get("content", "")
                if piece:
                    if first_token is None:
                        first_token = (time.perf_counter() - started) * 1000
                    chunks.append(piece)
                    if token_callback:
                        token_callback(piece)
                if event.get("done"):
                    break

        raw_text = "".join(chunks).strip()
        cleaned = clean_translation(raw_text)
        return {"text": cleaned, "first_token_ms": first_token,
                "elapsed_ms": (time.perf_counter() - started) * 1000}

    def translate_batch(self, items, target, context=(), model=None, source="English", cancel_check=None):
        if not items:
            return {"texts": [], "first_token_ms": None, "elapsed_ms": 0}
        if len(items) == 1:
            res = self.translate(items[0], target, context=context, model=model, source=source, cancel_check=cancel_check)
            return {"texts": [res["text"]], "first_token_ms": res["first_token_ms"], "elapsed_ms": res["elapsed_ms"]}

        numbered_subtitles = "\n".join(f"[{i+1}] {item}" for i, item in enumerate(items))
        messages = [
            {
                "role": "system",
                "content": (
                    f"You are a professional real-time subtitle translator. "
                    f"You MUST translate each numbered line from {source} into {target}. "
                    f"Every translation MUST strictly be in {target}. Never output in {source} or any other language. "
                    "Preserve numbering [1], [2], etc. "
                    "Output ONLY the numbered translations without notes, explanations, or quotes."
                )
            }
        ]
        user_content = ""
        if context:
            user_content += "Previous dialogue (context only, do not translate):\n" + " | ".join(context[-3:]) + "\n\n"
        user_content += f"Translate these {source} subtitles strictly into {target} (output ONLY {target}):\n{numbered_subtitles}"
        messages.append({"role": "user", "content": user_content})

        started = time.perf_counter()
        first_token = None
        chunks = []
        with requests.post(self.base_url + "/api/chat", json={
            "model": model or "ali6parmak/hy-mt1.5:1.8b", "messages": messages,
            "stream": True, "keep_alive": -1,
            "options": {"temperature": 0, "num_predict": 128},
        }, stream=True, timeout=(5, self.timeout)) as response:
            response.raise_for_status()
            for raw in response.iter_lines():
                if cancel_check and cancel_check():
                    break
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                piece = (event.get("message") or {}).get("content", "")
                if piece:
                    if first_token is None:
                        first_token = (time.perf_counter() - started) * 1000
                    chunks.append(piece)
                if event.get("done"):
                    break

        raw_text = "".join(chunks).strip()
        parsed = parse_batch_response(clean_translation(raw_text), len(items))
        cleaned_results = [clean_translation(p) for p in parsed]
        return {
            "texts": cleaned_results,
            "first_token_ms": first_token,
            "elapsed_ms": (time.perf_counter() - started) * 1000
        }
