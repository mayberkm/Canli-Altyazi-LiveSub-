import gzip
import re
import time
import traceback
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import numpy as np

from PySide6.QtCore import QThread, Signal

from .change_detector import ChangeDetector
from .ocr import PaddleTextReader
from .subtitle_tracker import SubtitleTracker
from translation.ollama_provider import OllamaProvider
from .cache import TranslationCache
from utils.system_info import gpu_memory_mb


# Regex pattern to catch non-English characters WITHOUT re.IGNORECASE
# (Avoiding Python re.IGNORECASE bug that folds English 'I' into Turkish 'ı')
NON_ENGLISH_CHARS = re.compile(
    r"[\u0400-\u04FF"  # Cyrillic
    r"\u4E00-\u9FFF"  # CJK Unified
    r"\u3040-\u30FF"  # Japanese Hiragana/Katakana
    r"\uAC00-\uD7AF"  # Korean Hangul
    r"\u0600-\u06FF"  # Arabic
    r"\u0590-\u05FF"  # Hebrew
    r"\u0370-\u03FF"  # Greek
    r"ğüşıöçĞÜŞİÖÇ"  # Turkish diacritics
    r"äöüßÄÖÜ"        # German umlauts / eszett
    r"éàèêëîïôùûÿœæÉÀÈÊËÎÏÔÙÛŸŒÆ"  # French accents
    r"áéíóúñÁÉÍÓÚÑ"  # Spanish accents
    r"ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"          # Polish diacritics
    r"]"
)


def apply_subtitle_filter(image):
    """Enhances white, yellow, and outline-framed bright subtitles while suppressing colorful scenery."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    val_f = v.astype(np.float32)
    sat_f = s.astype(np.float32)

    # 1. White text core: high brightness, low saturation (filters colorful grass, cars, flowers)
    white_score = np.clip(val_f - sat_f * 1.3, 0, 255)

    # 2. Yellow text core (Cyberpunk / Witcher yellow subtitles)
    yellow_mask = ((h >= 18) & (h <= 38) & (s >= 50) & (v >= 160)).astype(np.float32)
    yellow_score = yellow_mask * val_f

    # 3. Contour outline detection (dark border pixels around text: v <= 85)
    dark_mask = (v <= 85).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    dilated_dark = cv2.dilate(dark_mask, kernel, iterations=2)

    # Preserve cyan/light-colored bright subtitle cores framed by dark outlines
    bright_outlined = ((v >= 185) & (dilated_dark > 0)).astype(np.float32) * val_f
    text_lum = np.maximum(white_score, np.maximum(yellow_score, bright_outlined))

    # Boost pixels adjacent to dark outline strokes without darkening letter interiors
    outline_weight = 0.78 + 0.22 * dilated_dark.astype(np.float32)
    boosted = np.clip(text_lum * outline_weight, 0, 255).astype(np.uint8)

    # Blend with original grayscale to preserve smooth antialiasing for OCR
    blended = cv2.addWeighted(boosted, 0.75, gray, 0.25, 0)
    return cv2.cvtColor(blended, cv2.COLOR_GRAY2BGR)

COMMON_EN_WORDS = {
    # Pronouns & determiners
    "i", "me", "my", "myself", "we", "us", "our", "ours", "ourselves", "you", "your", "yours", "yourself", "yourselves",
    "he", "him", "his", "himself", "she", "her", "hers", "herself", "it", "its", "itself", "they", "them", "their",
    "theirs", "themselves", "this", "that", "these", "those", "what", "which", "who", "whom", "whose", "whatever",
    "the", "a", "an", "all", "another", "any", "both", "each", "either", "every", "neither", "no", "one", "other",
    "some", "such", "none",
    # Verbs & auxiliaries
    "am", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "having", "do", "does", "did",
    "doing", "can", "could", "will", "would", "shall", "should", "may", "might", "must",
    "go", "goes", "went", "gone", "going", "come", "comes", "came", "coming",
    "see", "sees", "saw", "seen", "seeing", "look", "looks", "looked", "looking",
    "know", "knows", "knew", "known", "knowing", "think", "thinks", "thought", "thinking",
    "get", "gets", "got", "gotten", "getting", "take", "takes", "took", "taken", "taking",
    "make", "makes", "made", "making", "say", "says", "said", "saying",
    "tell", "tells", "told", "telling", "give", "gives", "gave", "given", "giving",
    "find", "finds", "found", "finding", "call", "calls", "called", "calling",
    "talk", "talks", "talked", "talking", "hear", "hears", "heard", "hearing",
    "listen", "listens", "listened", "listening", "wait", "waits", "waited", "waiting",
    "stop", "stops", "stopped", "stopping", "help", "helps", "helped", "helping",
    "let", "lets", "run", "runs", "ran", "running", "move", "moves", "moved", "moving",
    "stay", "stays", "stayed", "staying", "leave", "leaves", "left", "leaving",
    "kill", "kills", "killed", "killing", "die", "dies", "died", "dying",
    "work", "works", "worked", "working", "need", "needs", "needed", "needing",
    "want", "wants", "wanted", "wanting", "like", "likes", "liked", "liking",
    "feel", "feels", "felt", "feeling", "try", "tries", "tried", "trying",
    "mean", "means", "meant", "meaning", "keep", "keeps", "kept", "keeping",
    "hold", "holds", "held", "holding", "bring", "brings", "brought", "bringing",
    "stand", "stands", "stood", "standing", "sit", "sits", "sat", "sitting",
    "put", "puts", "show", "shows", "showed", "shown", "showing",
    "ask", "asks", "asked", "asking", "answer", "answers", "answered",
    "break", "breaks", "broke", "broken", "breaking",
    # Contractions & colloquialisms
    "not", "dont", "cant", "wont", "isnt", "arent", "wasnt", "werent", "hasnt", "havent", "hadnt", "didnt",
    "youre", "theyre", "weve", "youve", "theyve", "ill", "youll", "hell", "shell", "theyll",
    "im", "ive", "id", "hes", "shes", "thats", "whats", "theres", "heres", "wheres", "whos", "lets",
    "couldnt", "wouldnt", "shouldnt", "mustnt", "ain", "aint", "yall", "gonna", "wanna", "gotta",
    "lemme", "gimme", "kinda", "sorta", "outta", "dunno", "cmon", "em", "cause", "cos", "bout",
    "and", "but", "or", "because", "as", "until", "while", "of", "at", "by", "for", "with", "about",
    "against", "between", "into", "through", "during", "before", "after", "above", "below", "to", "from",
    "up", "down", "in", "out", "on", "off", "over", "under", "again", "further", "then", "once",
    "here", "there", "when", "where", "why", "how", "so", "than", "too", "very", "just", "only", "now",
    # Conversational nouns, verbs, adverbs & common game dialogue vocabulary
    "yes", "yeah", "yep", "no", "nope", "okay", "ok", "hey", "hello", "hi", "bye", "goodbye",
    "please", "thanks", "thank", "sorry", "excuse", "pardon", "welcome", "alright",
    "man", "men", "woman", "women", "guy", "guys", "boy", "girl", "people", "friend", "brother", "sister",
    "sir", "mr", "mrs", "ms", "miss", "dr", "officer", "boss", "kid", "son",
    "car", "door", "room", "house", "home", "place", "street", "city", "town", "world",
    "day", "night", "morning", "evening", "today", "tomorrow", "tonight", "time", "year", "years",
    "thing", "things", "way", "life", "name", "hand", "hands", "head", "eyes", "eye",
    "shit", "fuck", "damn", "hell", "god", "jesus", "bitch", "ass", "crap",
    "right", "wrong", "true", "sure", "fine", "good", "bad", "great", "little", "big", "much", "many",
    "dinner", "beginnings", "beginning",
    "ready", "done", "dead", "alive", "nothing", "something", "everything", "anything",
    "someone", "anyone", "everyone", "nobody", "everybody", "somebody",
    "really", "never", "always", "maybe", "perhaps", "probably", "actually", "seriously",
    "definitely", "absolutely", "exactly", "obviously", "apparently", "honestly",
    "careful", "watch", "nice", "shot", "fire", "shoot", "open", "close", "shut",
    "behind", "back", "front", "ahead", "inside", "outside", "around", "away",
    "together", "alone", "almost", "already", "enough", "still", "soon", "later",
    "ever", "yet", "else", "mind", "care", "cares", "matter",
    "happened", "happening", "happen", "believe", "remember", "forget", "understand",
    "suppose", "guess", "wonder", "hope", "wish", "hurry", "quick", "fast", "slow",
    "easy", "hard", "safe", "clear", "quiet", "calm", "crazy", "insane", "stupid",
    "idiot", "moron", "bastard", "bullshit", "dammit", "goddammit", "heck", "christ", "lord",
    "buddy", "pal", "partner", "folks", "team", "squad", "captain", "commander", "general",
    "agent", "detective", "doctor", "prof", "king", "queen", "prince", "guard", "guards",
    "soldier", "enemy", "target", "mission", "job", "business", "money", "cash", "gold",
    "gun", "weapon", "weapons", "ammo", "bomb", "grenade", "sniper", "ambush", "attack",
    "fight", "cover", "reload", "reloading", "freeze", "surrender", "drop", "move",
    "follow", "lead", "check", "search", "hide", "escape", "save", "rescue", "protect",
    "defend", "destroy", "trust", "lie", "lying", "truth", "real", "fake", "free", "lost",
    "win", "won", "lose", "tired", "sick", "hurt", "pain", "blood", "deadly", "danger",
    "dangerous", "trouble", "problem", "idea", "plan", "chance", "choice", "fault", "deal",
    "point", "sense", "story", "news", "word", "words", "voice", "sound", "light", "dark",
    "water", "food", "drink", "sleep", "wake", "walk", "drive", "fly", "ride", "turn",
    "start", "begin", "finish", "end", "continue", "change", "touch", "hit", "beat",
    "catch", "caught", "throw", "pull", "push", "pick", "pay", "buy", "sell", "spend",
    "cost", "worth", "use", "used", "using",
    "oh", "ah", "uh", "um", "hm", "hmm", "huh", "whoa", "woah", "ouch", "ugh", "phew", "yikes", "oops", "shh", "aha", "yo",
    "anyway", "silence", "traitor", "traitors", "awesome", "brilliant", "perfect", "sweet", "cool",
    "jump", "duck", "crouch", "climb", "dodge", "block", "heal", "retreat", "advance", "charge",
    "coward", "cowards", "liar", "monster", "demon", "witch", "medic", "backup",
    "pathetic", "ridiculous", "absurd", "hilarious", "disgusting", "terrible", "horrible",
    "wonderful", "fantastic", "excellent", "exact", "correct", "weird", "strange", "odd",
    "funny", "sad", "mad", "angry", "happy", "glad", "proud", "lucky", "smart", "clever",
    "brave", "strong", "weak", "rich", "poor", "young", "old", "new", "late", "early",
    "busy", "full", "empty", "clean", "dirty", "cold", "hot", "warm", "bitter", "sharp",
    "heavy", "deep", "high", "low", "wide", "narrow", "short", "tall", "long",
    "louder", "faster", "slower", "gently", "agreed", "promise", "swear", "cheers",
    "bravo", "congrats", "congratulations",
    "cry", "eat", "sit", "cut", "hot", "low", "far", "few", "own", "top", "bro",
    "mom", "dad", "pop", "sis", "gross", "nah", "chill", "relax", "peace",
    "asshole", "motherfucker", "nigga", "nigger", "homie", "dawg", "chop",
    "fool", "fools", "punk", "psycho", "bingo", "boom", "cops", "police",
    "hookers", "therapy", "proctologist", "sup", "wassup", "howdy",
    # Common character names
    "michael", "trevor", "franklin", "arthur", "john", "dutch", "geralt", "sam", "tom", "jack", "bob", "alex", "rebecca", "david", "chris",
    "amanda", "jimmy", "tracey", "lamar", "lester", "simeon"
}

FOREIGN_ASCII_WORDS = {
    "evet", "hayir", "nasil", "neden", "tamam", "bir", "bu", "cok", "icin", "ile",
    "ben", "sen", "biz", "siz", "onlar", "gorusuruz", "merhaba", "gunaydin", "lutfen",
    "ne", "ve", "ki", "mi", "mu", "da", "de", "ya", "su", "al", "ol", "el",
    "gel", "git", "kal", "bak", "dur", "ver", "var", "yok", "ama", "hic", "hem",
    "her", "hep", "tek", "ilk", "son", "kac", "kim", "sey", "gibi", "daha", "bile",
    "sadece", "hemen", "simdi", "sonra", "once", "burada", "orada", "nerede",
    "oldu", "olur", "olmaz", "iyi", "kotu", "guzel", "dogru", "yanlis", "tabii", "peki", "belki",
    "und", "der", "die", "das", "nicht", "ich", "sie", "ist", "aber", "mit", "von", "guten", "morgen", "danke", "bitte",
    "dans", "avec", "pour", "cette", "nous", "vous", "ils", "elle", "bonjour", "merci", "oui",
    "hola", "gracias", "por", "que", "como", "pero", "para"
}

_PHANTOM_BLACKLIST = {"mk", "mki", "mkice", "mkiceandfire", "mim", "dbe", "soll", "sou", "reta", "ilil"}


_BUNDLED_EN_DICT = Path(__file__).resolve().parents[1] / "config/en_words.txt.gz"


def _load_system_en_words(dict_path=Path("/usr/share/dict/words"), bundled_path=_BUNDLED_EN_DICT):
    """Load lowercase words and capitalized proper nouns from the bundled or system dictionary."""
    dict_words = set()
    if bundled_path and Path(bundled_path).exists():
        try:
            with gzip.open(bundled_path, "rt", encoding="utf-8", errors="ignore") as f:
                for raw in f:
                    w = raw.strip().lower()
                    if w and w not in FOREIGN_ASCII_WORDS and w not in _PHANTOM_BLACKLIST:
                        dict_words.add(w)
        except OSError:
            pass
    if dict_path and Path(dict_path).exists():
        try:
            for raw in Path(dict_path).read_text(encoding="utf-8", errors="ignore").splitlines():
                w = raw.strip()
                if not w.isalpha() or not w.isascii():
                    continue
                low = w.lower()
                if low in FOREIGN_ASCII_WORDS or low in _PHANTOM_BLACKLIST:
                    continue
                if (w.islower() and len(w) >= 3) or (w[0].isupper() and w[1:].islower() and len(w) >= 4):
                    dict_words.add(low)
        except OSError:
            pass
    return dict_words


_SYSTEM_EN_WORDS = _load_system_en_words()

_EN_MORPH_RE = re.compile(
    r"^[a-z]{3,11}(?:ing|ed|ly|tion|sion|ment|ness|ful|less|able|ible|ous|ive|ist|ism|ize|ise|ate|ent|ant|ary|ory|ish|dom|hood|ship|ward|wards|wise|er|est|s)$"
)

_NON_STANDALONE_SINGLE_WORDS = {
    "in", "on", "at", "to", "by", "of", "or", "as", "if", "an", "am", "be",
    "the", "a", "it", "he", "we", "us", "my", "is", "are", "was", "were", "has", "had"
}


def _is_known_en_word(w: str) -> bool:
    if w in COMMON_EN_WORDS or w in _SYSTEM_EN_WORDS or w in {"st", "nd", "rd", "th", "am", "pm"}:
        return True
    if w.endswith("s") and len(w) >= 4 and (w[:-1] in COMMON_EN_WORDS or w[:-1] in _SYSTEM_EN_WORDS):
        return True
    return 4 <= len(w) <= 14 and bool(_EN_MORPH_RE.match(w))


def is_english_text(text: str) -> bool:
    """Strictly validates whether OCR-detected text is valid English dialogue."""
    if not text:
        return False
    stripped = text.strip()
    if len(stripped) < 2:
        return False

    # 1. Reject if any non-English/non-Latin extended characters exist
    if NON_ENGLISH_CHARS.search(stripped):
        return False

    # 2. Extract ASCII alphabetic letters
    letters = re.findall(r"[a-zA-Z]", stripped)
    if len(letters) < 2:
        return False

    # 3. Check ratio of alphabetic letters to total non-whitespace chars (filters timestamps/HUD/ASCII noise)
    non_space = [c for c in stripped if not c.isspace()]
    if not non_space:
        return False
    ratio = len(letters) / len(non_space)
    if ratio < 0.45:
        return False

    # 4. Extract word tokens (preserving apostrophe contractions while splitting glued punctuation)
    raw_alpha_tokens = re.findall(r"[a-zA-Z']+", stripped)
    joined_tokens = []
    for raw_tok in raw_alpha_tokens:
        raw_stem = re.sub(r"'s$", "", raw_tok, flags=re.IGNORECASE)
        w = re.sub(r"[^a-zA-Z]", "", raw_stem).lower()
        if not w:
            continue
        if len(w) >= 15 and not _is_known_en_word(w):
            return False
        if raw_stem.isalpha() and raw_stem.isupper() and len(w) >= 9 and not _is_known_en_word(w):
            return False
        joined_tokens.append(w)

    split_words = re.findall(r"[a-zA-Z]+", stripped.lower())
    words = [w for w in (joined_tokens + split_words) if len(w) >= 1]
    if not words:
        return False

    if len(joined_tokens) == 1 and joined_tokens[0] in _NON_STANDALONE_SINGLE_WORDS and not stripped.endswith(("!", "?", ".")):
        return False

    has_en_words = any(w in COMMON_EN_WORDS or w in _SYSTEM_EN_WORDS for w in words)
    has_foreign_words = any(w in FOREIGN_ASCII_WORDS for w in words)

    if has_foreign_words and not has_en_words:
        return False

    if len(joined_tokens) >= 2:
        substantive = [w for w in joined_tokens if len(w) >= 2]
        if substantive:
            known_cnt = sum(1 for w in substantive if _is_known_en_word(w))
            min_ratio = 0.50 if len(substantive) == 2 else 0.55
            if known_cnt / len(substantive) < min_ratio:
                return False

    if has_en_words:
        return True

    # 5. Morphological fallback for rare English words
    for w in words:
        if 4 <= len(w) <= 14 and _EN_MORPH_RE.match(w):
            vowels = len(re.findall(r"[aeiouy]", w))
            v_ratio = vowels / len(w)
            if 0.20 <= v_ratio <= 0.75:
                return True

    # 6. Punctuated proper-noun utterance fallback (e.g. "Simeon?", "Friedlander!", "Tanisha!")
    if (
        len(joined_tokens) == 1
        and re.match(r"^[A-Z][a-z]{3,}[.!?]+$", stripped)
        and joined_tokens[0] not in _PHANTOM_BLACKLIST
        and not has_foreign_words
    ):
        w = joined_tokens[0]
        vowels = len(re.findall(r"[aeiouy]", w))
        v_ratio = vowels / len(w)
        if 0.25 <= v_ratio <= 0.65:
            return True

    return False


def is_dialogue_subtitle(text, box=None, region_w=0, region_h=0):
    """Filters out channel watermarks (MKIceAndFire, Caida Libre), car plates (880HS955),
    and corner UI timeline elements so only actual dialogue subtitles are processed."""
    clean = text.strip()
    if len(clean) < 2:
        return False

    clean_alpha = re.sub(r"[^\w]", "", clean).lower()
    if clean_alpha in {"mk", "mki", "mkice", "mkiceandfire", "ign", "gamespot", "theradbrad", "radbrad"}:
        return False

    # 1. Reject vehicle license plates, speedometers, minimap meters with digits,
    # and mixed letter+digit OCR corruption (e.g. "Intac7") unless ordinal/time.
    for tok in clean.split():
        clean_tok = re.sub(r"[^\w]", "", tok)
        if re.search(r"[A-Za-z]", clean_tok) and re.search(r"\d", clean_tok):
            if not re.fullmatch(r"\d+(?:st|nd|rd|th|am|pm)", clean_tok, re.IGNORECASE):
                return False

    clean_no_ord = re.sub(r"\b\d+(?:st|nd|rd|th|am|pm)\b", "", clean, flags=re.IGNORECASE)
    digits = len(re.findall(r"\d", clean_no_ord))
    letters = len(re.findall(r"[a-zA-Z]", clean))
    if digits > 0:
        if digits >= 2 or letters < 4 or digits / max(1, digits + letters) > 0.20:
            return False

    # 2. Reject camelCase / mixedCase watermarks and channel names (e.g. 'MKIceAndFire', 'SanAndues')
    for clean_w in re.findall(r"[A-Za-z0-9]+", clean):
        if clean_w.startswith(("Mc", "Mac")):
            prefix_len = 2 if clean_w.startswith("Mc") else 3
            rest = clean_w[prefix_len:]
            if len(rest) >= 2 and rest[0].isupper() and rest[1:].islower():
                continue
        if re.search(r"[a-z][A-Z]", clean_w):
            return False

    # 3. Reject tiny watermarks stuck on extreme left/right borders, while keeping normal
    # left-aligned or off-center single-word subtitles inside the selected region.
    if box and region_w > 0:
        x0, y0, x1, y1 = box
        cx = (x0 + x1) / 2
        w = max(1, x1 - x0)
        rel_cx = cx / region_w
        rel_w = w / region_w
        if rel_w < 0.25 and (rel_cx > 0.88 or rel_cx < 0.05 or x1 >= region_w - 4):
            return False
        if region_w > 1400 and rel_w < 0.60 and (rel_cx < 0.05 or rel_cx > 0.95):
            return False

    return True


def pick_best_subtitle(blocks, region_w, region_h):
    """Picks the single primary dialogue subtitle block, discarding peripheral watermarks and UI."""
    if not blocks:
        return None
    if len(blocks) == 1:
        return blocks[0]

    def score(b):
        x0, y0, x1, y1 = b.get("bounds", [0, 0, 0, 0])
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        rw = max(1, region_w)
        rh = max(1, region_h)
        center_dist = abs(cx - rw / 2) / (rw / 2)
        vert_factor = cy / rh
        length = len(b.get("text", ""))
        return length * 2.5 + vert_factor * 60.0 - center_dist * 50.0

    return max(blocks, key=score)


def is_translation_echo(text, active_translations, source_lang="English"):
    clean = text.strip()
    if not clean:
        return False
    # If source is English, reject non-English or Turkish characters immediately
    if source_lang.lower() == "english":
        if NON_ENGLISH_CHARS.search(clean):
            return True
    c_text = re.sub(r"[^\w\s]", "", clean).strip().casefold()
    c_text = re.sub(r"\s+", " ", c_text)
    if not c_text:
        return False
    for active in active_translations:
        if not active:
            continue
        candidates = [active] + [ln for ln in active.splitlines() if ln.strip()]
        for cand in candidates:
            c_active = re.sub(r"[^\w\s]", "", cand).strip().casefold()
            c_active = re.sub(r"\s+", " ", c_active)
            if not c_active:
                continue
            if c_text == c_active:
                return True
            if SequenceMatcher(None, c_text, c_active).ratio() >= 0.82:
                return True
    return False


def is_inside_overlay(bounds, overlay_rects, reg_left=0, reg_top=0):
    """Checks whether detected text coordinates fall inside any visible CanlıAltyazı (LiveSub) overlay window."""
    if not bounds or not overlay_rects:
        return False
    bx0, by0, bx1, by1 = bounds
    bcx = (bx0 + bx1) / 2 + reg_left
    bcy = (by0 + by1) / 2 + reg_top
    for ox, oy, ow, oh in overlay_rects:
        if ox <= bcx <= ox + ow and oy <= bcy <= oy + oh:
            return True
    return False


class ModelWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, device):
        super().__init__()
        self.device = device

    def run(self):
        try:
            self.ready.emit(PaddleTextReader(self.device))
        except Exception as exc:
            self.failed.emit(f"PaddleOCR başlatılamadı: {exc}")


def _clamp_capture_region(cur_region, mon0):
    """Clamps capture coordinates inside the virtual desktop bounds (supporting negative monitor origins)."""
    mon0_left = int(mon0.get("left", 0))
    mon0_top = int(mon0.get("top", 0))
    mon0_right = mon0_left + int(mon0.get("width", 1920))
    mon0_bottom = mon0_top + int(mon0.get("height", 1080))
    reg_left = max(mon0_left, min(int(cur_region.get("left", 0)), mon0_right - 20))
    reg_top = max(mon0_top, min(int(cur_region.get("top", 0)), mon0_bottom - 20))
    reg_w = max(16, min(int(cur_region.get("width", 100)), mon0_right - reg_left))
    reg_h = max(16, min(int(cur_region.get("height", 50)), mon0_bottom - reg_top))
    return {"left": reg_left, "top": reg_top, "width": reg_w, "height": reg_h}


class CaptureWorker(QThread):
    recognized = Signal(str, float, float, float, float, object, bool, object)
    cleared = Signal()
    failed = Signal(str)

    def __init__(self, region, hz=7, stable_ms=60, source_lang="English", filter_mode="color_contour"):
        super().__init__()
        self.region = region
        self.hz = max(1, min(15, int(hz)))
        self.source_lang = source_lang
        self.filter_mode = filter_mode
        self.tracker = SubtitleTracker(stable_ms)
        self.detector = ChangeDetector()
        self.reader = None
        self._stop = False
        self.last_check = 0
        self.capture_source = None
        self.last_blocks = []
        self.active_translations = []
        self.overlay_rects = []

    def set_region(self, region):
        """Hot-swaps capture region when the user switches monitors or re-selects an area."""
        if region:
            self.region = dict(region)
            self.detector.previous = None
            self.tracker.current = ""
            self.tracker.candidate = ""
            self.tracker.candidate_bounds = None
            self.tracker.last_emitted = ""
            self.tracker.missing_since = None

    def set_active_translations(self, texts):
        self.active_translations = [t.strip() for t in texts if t and t.strip()]

    def set_overlay_rects(self, rects):
        self.overlay_rects = list(rects)

    def set_reader(self, reader):
        self.reader = reader

    def set_capture_source(self, source):
        """Content source (no longer unmaps windows to prevent flicker)."""
        self.capture_source = source

    def stop(self):
        self._stop = True

    def run(self):
        try:
            import mss
            with mss.mss() as capture:
                interval = 1 / self.hz
                last_ocr = 0
                while not self._stop:
                    tick = time.perf_counter()
                    cur_region = dict(self.region)
                    # Clamp capture coordinates safely inside the virtual desktop bounds
                    clean_region = _clamp_capture_region(cur_region, capture.monitors[0])

                    shot = capture.grab(clean_region)
                    image = shot if isinstance(shot, np.ndarray) else np.asarray(shot)[:, :, :3].copy()

                    if self.filter_mode == "color_contour":
                        ocr_image = apply_subtitle_filter(image)
                    else:
                        ocr_image = image

                    capture_ms = (time.perf_counter() - tick) * 1000
                    min_ocr_interval = max(0.06, 1.0 / self.hz)
                    time_since_ocr = tick - last_ocr
                    changed = False
                    change_ms = 0.0
                    should_run_ocr = False

                    # Only evaluate ChangeDetector when OCR is actually eligible to run
                    # so transient frame transitions are never overwritten on skipped ticks.
                    if self.reader and (time_since_ocr >= min_ocr_interval):
                        change_started = time.perf_counter()
                        changed = self.detector.changed(ocr_image)
                        change_ms = (time.perf_counter() - change_started) * 1000
                        should_run_ocr = changed or (time_since_ocr >= 0.20)

                    if should_run_ocr:
                        echo_filter = lambda txt: (
                            not is_translation_echo(txt, self.active_translations, self.source_lang)
                            and (self.source_lang.lower() != "english" or not NON_ENGLISH_CHARS.search(txt))
                        )
                        result = self.reader.read(ocr_image, line_filter=echo_filter)

                        # Filter out any blocks that are not valid English dialogue, are watermarks/codes, or match active translations
                        raw_blocks = result.get("blocks", [])
                        rw = cur_region.get("width", 0)
                        rh = cur_region.get("height", 0)
                        valid_blocks = [
                            b for b in raw_blocks
                            if is_english_text(b.get("text", ""))
                            and is_dialogue_subtitle(b.get("text", ""), b.get("bounds"), rw, rh)
                            and not is_translation_echo(b.get("text", ""), self.active_translations, self.source_lang)
                        ]
                        if valid_blocks:
                            best_block = pick_best_subtitle(valid_blocks, rw, rh)
                            result_text = best_block["text"]
                            bounds = best_block["bounds"]
                            selected_blocks = [best_block]
                        else:
                            result_text = ""
                            bounds = None
                            selected_blocks = []

                        self.last_blocks = selected_blocks
                        last_ocr = tick
                        action, text = self.tracker.observe(result_text, bounds)
                        if action in ("changed", "same") and text:
                            self.recognized.emit(text, capture_ms, change_ms, result["elapsed_ms"], gpu_memory_mb() or -1,
                                                  self.tracker.bounds, action == "changed", selected_blocks)
                        if action == "cleared":
                            self.cleared.emit()
                    # A candidate that survives the configured stability window should not
                    # wait for another pixel change before it is promoted.
                    elif self.reader and self.tracker.candidate and tick - self.last_check >= .04:
                        cand_bounds = getattr(self.tracker, "candidate_bounds", None)
                        rw = cur_region.get("width", 0)
                        rh = cur_region.get("height", 0)
                        if (
                            is_english_text(self.tracker.candidate)
                            and is_dialogue_subtitle(self.tracker.candidate, cand_bounds, rw, rh)
                            and not is_translation_echo(self.tracker.candidate, self.active_translations, self.source_lang)
                        ):
                            action, text = self.tracker.observe(self.tracker.candidate)
                            self.last_check = tick
                            if action in ("changed", "same") and text:
                                cand_block = [{"text": text, "bounds": self.tracker.bounds or cand_bounds}]
                                self.recognized.emit(text, capture_ms, change_ms, 0.0, gpu_memory_mb() or -1,
                                                      self.tracker.bounds, action == "changed", cand_block)
                        else:
                            self.tracker.candidate = ""
                            self.tracker.candidate_bounds = None
                    self.msleep(max(1, int((interval - (time.perf_counter() - tick)) * 1000)))
        except Exception as exc:
            self.failed.emit(f"Ekran yakalama hatası (X11/MSS): {exc}")


class TranslationWorker(QThread):
    completed = Signal(int, list, float, float, bool)
    failed = Signal(int, str)

    def __init__(self, generation, items, target, base_url, model, context, cache, source="English"):
        super().__init__()
        # items is a list of (text, anchor) tuples for this subtitle generation
        self.generation = generation
        self.items = items
        self.target = target
        self.source = source
        self.base_url, self.model, self.context, self.cache = base_url, model, context, cache
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def run(self):
        try:
            if not self.items:
                return

            results = [None] * len(self.items)
            miss_indices = []
            miss_texts = []

            # 1. Check SQLite cache for each text item
            for i, (text, anchor) in enumerate(self.items):
                key = self.cache.key(text, self.source, self.target, self.model, "|".join(self.context[-4:]))
                hit = self.cache.get(key)
                if hit is not None:
                    results[i] = (hit, anchor)
                else:
                    miss_indices.append(i)
                    miss_texts.append(text)

            # If every item was in the cache, complete immediately
            if not miss_indices:
                if not self.cancelled:
                    self.completed.emit(self.generation, results, 0.0, 0.0, True)
                return

            # 2. Call batch translation for all uncached items in a single request
            provider = OllamaProvider(self.base_url)
            batch_res = provider.translate_batch(
                miss_texts, self.target, context=self.context, model=self.model,
                source=self.source, cancel_check=lambda: self.cancelled
            )

            if self.cancelled:
                return

            translated_list = batch_res.get("texts", [])
            for idx, trans_text in zip(miss_indices, translated_list):
                orig_text, anchor = self.items[idx]
                cleaned_tr = (trans_text or "").strip()
                if not cleaned_tr:
                    continue
                if len(orig_text.strip()) >= 10 and cleaned_tr.casefold() == orig_text.strip().casefold():
                    continue
                key = self.cache.key(orig_text, self.source, self.target, self.model, "|".join(self.context[-4:]))
                self.cache.put(key, cleaned_tr)
                results[idx] = (cleaned_tr, anchor)

            final_results = [r for r in results if r is not None]

            if final_results and not self.cancelled:
                self.completed.emit(
                    self.generation,
                    final_results,
                    batch_res.get("first_token_ms") or -1,
                    batch_res.get("elapsed_ms") or 0.0,
                    False
                )
        except Exception as exc:
            if not self.cancelled:
                self.failed.emit(self.generation, str(exc))
