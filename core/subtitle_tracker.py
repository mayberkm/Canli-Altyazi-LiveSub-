import re
import time
from difflib import SequenceMatcher


def normalize(text):
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines() if line.strip()).casefold()


def canonical(text):
    """Normalizes text to lowercase alphanumeric tokens for jitter-tolerant comparison."""
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").casefold()))


def is_same_or_subset(can_new, can_ref):
    """Returns True if can_new matches can_ref, has minor OCR jitter, or is a partial multi-word subset."""
    if not can_new or not can_ref:
        return can_new == can_ref
    if can_new == can_ref:
        return True
    new_words = can_new.split()
    ref_words = can_ref.split()
    # Protect single-word subtitles from being swallowed by substring or loose fuzzy matches
    if len(new_words) == 1 or len(ref_words) == 1:
        if len(new_words) == 1 and len(ref_words) == 1 and len(can_new) >= 6:
            return SequenceMatcher(None, can_new, can_ref).ratio() >= 0.93
        return False
    is_extension = can_new.startswith(can_ref + " ") and (len(can_new) - len(can_ref) >= 3)
    if not is_extension and SequenceMatcher(None, can_new, can_ref).ratio() >= 0.85:
        return True
    if len(can_new) >= 12 and len(can_new) < len(can_ref) and can_new in can_ref:
        return True
    return False


def is_numeric_only(text):
    """Ignore standalone counters while preserving numbers inside dialogue."""
    value = text.strip()
    return bool(value) and not re.search(r"[A-Za-z]", value) and bool(re.fullmatch(r"[\d\s.,:;!?%+\-–—/\\()\[\]#]+", value))


class SubtitleTracker:
    def __init__(self, stable_ms=60, clear_ms=1200):
        self.stable_s = stable_ms / 1000
        self.clear_s = clear_ms / 1000
        self.candidate = ""
        self.candidate_since = 0.0
        self.candidate_bounds = None
        self.current = ""
        self.missing_since = None
        self.bounds = None
        self.last_emitted = ""
        self.last_emitted_at = 0.0

    def observe(self, text, bounds=None):
        text = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines() if line.strip())
        now = time.monotonic()
        can_text = canonical(text)
        if not can_text or is_numeric_only(text):
            self.candidate = ""
            self.candidate_bounds = None
            if self.current and self.missing_since is None:
                self.missing_since = now
            if self.current and self.missing_since is not None and now - self.missing_since >= self.clear_s:
                self.current = ""
                self.bounds = None
                self.missing_since = None
                return "cleared", ""
            return None, ""

        self.missing_since = None
        can_curr = canonical(self.current)

        # Prevent punctuation jitter, minor OCR noise, or partial subset drops from re-emitting
        if can_curr and is_same_or_subset(can_text, can_curr):
            self.bounds = bounds or self.bounds
            self.last_emitted_at = now
            return "same", self.current

        # Prevent re-translating the same subtitle after a brief OCR dropout (< 6s, or < 1.2s for 1-word captions)
        dropout_window = 1.2 if len(can_text.split()) == 1 else 6.0
        if not can_curr and self.last_emitted and (now - self.last_emitted_at <= dropout_window):
            if is_same_or_subset(can_text, canonical(self.last_emitted)):
                self.current = self.last_emitted
                self.candidate = self.last_emitted
                self.bounds = bounds or self.bounds
                self.last_emitted_at = now
                return "same", self.current

        # Immediate emit if multi-word active subtitle grew incrementally (typewriter / multi-line reveal)
        if can_curr and len(can_curr.split()) >= 2 and can_text.startswith(can_curr + " ") and (len(can_text) - len(can_curr) >= 3):
            self.current = text
            self.candidate = text
            self.bounds = bounds or self.bounds
            self.candidate_bounds = self.bounds
            self.last_emitted = text
            self.last_emitted_at = now
            return "changed", self.current

        # Promote substantive new subtitles immediately on first observation so brief & 2-letter words aren't missed
        if len(can_text) >= 2:
            self.current = text
            self.candidate = text
            self.bounds = bounds
            self.candidate_bounds = bounds
            self.last_emitted = text
            self.last_emitted_at = now
            return "changed", self.current

        norm_text = normalize(text)
        norm_cand = normalize(self.candidate)
        if norm_cand and (
            (norm_text.startswith(norm_cand) and bool(norm_cand))
            or SequenceMatcher(None, norm_text, norm_cand).ratio() > .75
        ):
            if len(text) >= len(self.candidate):
                self.candidate = text
                if bounds:
                    self.candidate_bounds = bounds
        else:
            self.candidate = text
            self.candidate_bounds = bounds
            self.candidate_since = now
        if now - self.candidate_since >= self.stable_s:
            if canonical(self.candidate) != can_curr:
                self.current = self.candidate
                self.bounds = bounds or self.candidate_bounds
                self.last_emitted = self.current
                self.last_emitted_at = now
                return "changed", self.current
        return None, self.candidate
