"""Çok ciltli eserleri (İhya 1..8, Mesnevi Cilt 3, Tarih Vol. II ...) koleksiyon olarak gruplar."""
import re
from collections import Counter

from .textutil import fold, words

_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
          "xi": 11, "xii": 12, "xiii": 13, "xiv": 14, "xv": 15, "xvi": 16, "xvii": 17, "xviii": 18,
          "xix": 19, "xx": 20}
_MARK = r"(?:cilt|cİlt|c\.|vol\.?|volume|band|bd\.?|tome|deel|part|kısım|kisim|kitap|bölüm|bolum|sayı|sayi|no\.?|#)"
# "Başlık 3", "Başlık - 3", "Başlık Cilt 3", "Başlık (3)", "Başlık Vol. II", "Başlık 3. Cilt", "Başlık 2-3"
_VOL_END = re.compile(
    rf"^(?P<base>.*?\S)[\s\-–—_.,:;]*[\(\[]?\s*(?:{_MARK}\s*)?(?<![\dA-Za-z])(?P<n>\d{{1,3}}|[IVX]{{1,5}})"
    rf"(?:\s*[-–]\s*\d{{1,3}})?\s*\.?\s*(?:{_MARK})?\s*[\)\]]?\s*$", re.I)
# "3. Cilt - Başlık", "Cilt 3: Başlık"
_VOL_START = re.compile(rf"^\s*(?:(?P<n1>\d{{1,3}})\s*\.?\s*{_MARK}|{_MARK}\s*(?P<n2>\d{{1,3}}))\s*[\-–—:.]\s*(?P<base>.+)$", re.I)


def split_volume(title):
    """('İhya u Ulumid Din', 3) ya da (None, None)."""
    t = (title or "").strip().rstrip("?!. ")
    m = _VOL_START.match(t)
    if m:
        return m.group("base").strip(" -–—:."), int(m.group("n1") or m.group("n2"))
    m = _VOL_END.match(t)
    if not m:
        return None, None
    n = m.group("n")
    if n.isdigit():
        num = int(n)
        if num == 0 or (len(n) == 4):  # yıl gibi
            return None, None
    else:
        if n != n.upper():  # roma rakamı büyük harf olmalı
            return None, None
        num = _ROMAN.get(n.lower())
        if not num:
            return None, None
    base = m.group("base").strip(" -–—_.,:;([")
    if len("".join(words(base))) < 4:
        return None, None
    return base, num


def assign(books):
    """books listesine 'series', 'volume' alanlarını ekler; serinin kategori/dilini birleştirir."""
    groups = {}
    for b in books:
        base, num = split_volume(b["title"])
        if base is None:
            continue
        key = "".join(words(base))
        groups.setdefault(key, []).append((b, base, num))
    # numarasız ilk cilt: "Kitap" + "Kitap 2" + "Kitap 3"
    for b in books:
        if b.get("_vol_checked"):
            continue
        key = "".join(words(b["title"]))
        if key in groups and split_volume(b["title"])[0] is None:
            nums = {n for _, _, n in groups[key]}
            if 1 not in nums:
                groups[key].append((b, b["title"], 1))
    for key, items in groups.items():
        if len({n for _, _, n in items}) < 2:
            continue
        # farklı yazarlara ait aynı adlı eserler karışmasın
        authors = Counter(fold(b["author"]) for b, _, _ in items if b["author"])
        main_author = authors.most_common(1)[0][0] if authors else ""
        members = [(b, base, n) for b, base, n in items if not b["author"] or fold(b["author"]) == main_author]
        if len({n for _, _, n in members}) < 2:
            continue
        name = Counter(base for _, base, _ in members).most_common(1)[0][0]
        author = next((b["author"] for b, _, _ in members if b["author"]), "")
        # serinin tamamı aynı yerde dursun: çoğunluk kategorisi ve dili
        user_cat = next((b["override"].get("cat") for b, _, _ in members if b.get("override", {}).get("cat")), None)
        cats = Counter((b["category"], b["subcategory"]) for b, _, _ in members if b["category"] != "Diğer")
        cat = (user_cat, "Genel") if user_cat else (cats.most_common(1)[0][0] if cats else ("Diğer", "Sınıflandırılmamış"))
        if user_cat and cats:
            same = [c for c in cats if c[0] == user_cat]
            if same:
                cat = max(same, key=lambda c: cats[c])
        user_lang = next((b["override"].get("lang") for b, _, _ in members if b.get("override", {}).get("lang")), None)
        lang = user_lang or Counter(b["language"] for b, _, _ in members).most_common(1)[0][0]
        sid = "s" + key[:40]
        for b, _, n in members:
            b["series"], b["series_id"], b["volume"] = name, sid, n
            b["category"], b["subcategory"], b["language"] = cat[0], cat[1], lang
            if not b["author"] and author:
                b["author"] = author
