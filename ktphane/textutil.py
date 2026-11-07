"""Metin yardımcıları: Türkçe uyumlu normalizasyon, dil tespiti, başlık düzeltme."""
import re
import unicodedata

_TR_LOWER = str.maketrans({"I": "ı", "İ": "i"})
_TR_UPPER = str.maketrans({"i": "İ", "ı": "I"})
_FOLD = str.maketrans({"ı": "i", "ş": "s", "ğ": "g", "ç": "c", "ö": "o", "ü": "u",
                       "â": "a", "î": "i", "û": "u", "’": "'", "‘": "'", "`": "'"})

ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
PERSIAN_OTTOMAN_RE = re.compile(r"[پچژگڭ]")
TR_CHARS_RE = re.compile(r"[ıİşŞğĞçÇöÖüÜ]")
WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def tr_lower(s: str) -> str:
    return s.translate(_TR_LOWER).lower()


def tr_upper(s: str) -> str:
    return s.translate(_TR_UPPER).upper()


def fold(s: str) -> str:
    """Arama/eşleştirme için: küçük harf, Türkçe karakter ve aksan sadeleştirme."""
    s = tr_lower(s or "").translate(_FOLD)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s


def words(s: str) -> list:
    return WORD_RE.findall(fold(s).replace("'", " "))


def group_key(*parts) -> str:
    return "|".join("".join(words(p)) for p in parts)


_SMALL_WORDS = {"ve", "ile", "de", "da", "ki", "mi", "bir", "of", "the", "and", "in", "on",
                "a", "an", "to", "for", "van", "de", "het", "een", "el", "al", "ibn", "bin"}


def tr_title(s: str) -> str:
    """Tamamı büyük/küçük yazılmış başlıkları Türkçe kurallarıyla düzeltir.
    Zaten karışık yazılmışsa dokunmaz (yazarın tercihi korunur)."""
    s = re.sub(r"\s+", " ", s or "").strip()
    letters = [c for c in s if c.isalpha() and not ARABIC_RE.match(c)]
    if not letters:
        return s
    if not (all(c.islower() for c in letters) or all(c.isupper() for c in letters)):
        return s
    out = []
    for i, w in enumerate(s.split(" ")):
        lw = tr_lower(w)
        if i > 0 and lw in _SMALL_WORDS:
            out.append(lw)
            continue
        # kesme işaretinden sonrası küçük kalır: Kur'an, Muhammed'in
        head, sep, tail = lw.partition("'")
        head = tr_upper(head[:1]) + head[1:] if head else head
        out.append(head + sep + tail)
    return " ".join(out)


_STOP = {
    "Türkçe": {"ve", "bir", "ile", "icin", "uzerine", "tarihi", "hayati", "kitabi", "nedir",
               "ve", "bu", "olan", "gore", "dair", "hakkinda", "seckiler", "eserleri"},
    "English": {"the", "of", "and", "to", "in", "a", "an", "introduction", "history", "on",
                "for", "with", "from", "philosophy", "guide", "how", "what", "why"},
    "Nederlands": {"de", "het", "een", "van", "voor", "geschiedenis", "en", "naar", "over",
                   "met", "economie", "wereld", "nederland"},
    "Deutsch": {"der", "die", "das", "und", "zur", "geschichte", "einfuhrung", "uber", "von"},
}

_LANG_CODES = {"tr": "Türkçe", "tur": "Türkçe", "en": "English", "eng": "English",
               "nl": "Nederlands", "nld": "Nederlands", "dut": "Nederlands",
               "de": "Deutsch", "ger": "Deutsch", "deu": "Deutsch",
               "ar": "Arapça", "ara": "Arapça", "fa": "Farsça", "per": "Farsça",
               "fas": "Farsça", "ota": "Osmanlıca", "fr": "Français", "fre": "Français",
               "fra": "Français"}

_OTTOMAN_HINTS = {"osmanlica", "osmanli", "elifba", "lugat", "lugati", "risale", "risalesi",
                  "risale-i", "matbaa", "matbaasi", "kitabhane"}


def language_from_code(code: str):
    if not code:
        return None
    code = code.strip().lower().split("-")[0].split("_")[0]
    return _LANG_CODES.get(code)


def detect_language(text: str, meta_code: str = None) -> str:
    """Önce dosyanın kendi dil bilgisi, sonra yazı sistemi, sonra kelime istatistiği."""
    from_meta = language_from_code(meta_code)
    w = set(words(text))
    if ARABIC_RE.search(text or ""):
        if w & _OTTOMAN_HINTS or "ڭ" in text:
            return "Osmanlıca"
        if PERSIAN_OTTOMAN_RE.search(text):
            return "Osmanlıca / Farsça"
        return from_meta or "Arapça"
    if from_meta:
        return from_meta
    if "osmanlica" in w:
        return "Osmanlıca"
    scores = {lang: len(w & stop) for lang, stop in _STOP.items()}
    if TR_CHARS_RE.search(text or ""):
        scores["Türkçe"] += 2
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "Türkçe"
