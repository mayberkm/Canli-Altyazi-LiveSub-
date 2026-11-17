import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path


def _legacy_cache_path():
    if sys.platform == "win32" or os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData/Local"))
        return base / "GameLingo/translations.sqlite3"
    return Path.home() / ".local/share/gamelingo/translations.sqlite3"


def default_cache_path():
    if sys.platform == "win32" or os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData/Local"))
        return base / "LiveSub/translations.sqlite3"
    return Path.home() / ".local/share/livesub/translations.sqlite3"


class TranslationCache:
    def __init__(self, path=None):
        explicit_path = path is not None
        self.path = Path(path or default_cache_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not explicit_path and not self.path.exists():
            legacy = _legacy_cache_path()
            if legacy.exists():
                try:
                    shutil.copy2(legacy, self.path)
                except OSError:
                    pass
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS translations (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    @staticmethod
    def key(text, source, target, model, context):
        value = json.dumps([" ".join(text.casefold().split()), source, target, model, context], ensure_ascii=False)
        return hashlib.sha256(value.encode()).hexdigest()

    def get(self, key):
        with self._connect() as db:
            row = db.execute("SELECT value FROM translations WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key, value):
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO translations VALUES (?, ?)", (key, value))
