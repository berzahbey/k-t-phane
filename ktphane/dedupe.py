"""Kopya kitap bulma: hangisi daha iyi durumdaysa o kalır, diğerleri silinmeye önerilir.

Karar kitabın İÇERİĞİNE göre verilir (yalnızca isim yetmez):
  kesin    — birebir aynı dosya ya da baş ve orta kısımlarındaki metin en az %80 aynı.
             Otomatik temizlik yalnızca bunlara dokunur.
  muhtemel — metin kısmen benziyor (%15–80) ya da metin okunamadığı için karşılaştırılamadı;
             yalnızca listelenir, kullanıcı onaylamadan silinmez.
  (listelenmez) — adı benzese de metni farklı (<%15): başka çeviri/baskı ya da başka kitap.
Farklı formatlar (EPUB + PDF) kopya sayılmaz; ikisi de kalır.
"""
import hashlib
import json
import re
import logging
import os
import threading

log = logging.getLogger("ktphane.dedupe")


class Dedupe:
    def __init__(self, lib):
        self.lib = lib
        self.hash_file = os.path.join(lib.data_dir, "hashes.json")
        self.state_file = os.path.join(lib.data_dir, "dedupe.json")
        self._lock = threading.Lock()
        self.hashes = self._load(self.hash_file, {})
        self.state = self._load(self.state_file, {"auto": False, "ignored": []})
        self.mid_file = os.path.join(lib.data_dir, "orta-metin.json")
        self.mids = self._load(self.mid_file, {})  # ckey -> kitabın ortasından metin örneği

    @staticmethod
    def _load(path, default):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    def _save(self, path, data):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)

    def save_state(self):
        self._save(self.state_file, self.state)

    # ---------- içerik özeti ----------
    def _hash(self, f):
        k = f["ckey"]
        if k in self.hashes:
            return self.hashes[k]
        h = hashlib.sha1()
        try:
            with open(self.lib.abs_path(f), "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
        except OSError:
            return None
        self.hashes[k] = h.hexdigest()
        return self.hashes[k]

    # ---------- kalite puanı ----------
    def score(self, f, placed):
        """(puan, açıklama). Yüksek = daha iyi."""
        s, why = 0.0, []
        if f.get("broken"):
            return -1000, "bozuk / açılamıyor"
        if f.get("error"):
            s -= 50
            why.append("okuma hatası")
        if f["ext"] == "pdf":
            if f.get("needs_ocr") is False:
                s += 200
                why.append("metin tabanlı")
            elif f.get("needs_ocr") is True:
                why.append("taranmış (OCR)")
            if f.get("pages"):
                s += min(f["pages"], 3000) / 10
                why.append(f"{f['pages']} sayfa")
        elif f["ext"] == "epub":
            a = (self.lib.enricher.get(f["ckey"]) or {}) if getattr(self.lib, "enricher", None) else {}
            if a.get("toc"):
                s += min(len(a["toc"]), 40)
                why.append("içindekiler var")
            rec = self.lib.get_file(f["id"]) or {}
            if rec.get("cover_member"):
                s += 30
                why.append("kapak var")
        s += min(f["size"] / (1 << 20), 40)  # büyük dosya genelde daha eksiksiz (resimler, yüksek çözünürlük)
        if f["path"] in placed:
            s += 5  # zaten düzenlenmiş yerde duran kalsın
        why.append(_size(f["size"]))
        return round(s, 1), ", ".join(why)

    # ---------- bulma ----------
    def find(self):
        books = self.lib.books()
        placed = self.lib.org.get("placed", {})
        fmt_book = {}
        all_files = []
        for b in books:
            for f in b["formats"]:
                fmt_book[f["id"]] = b
                all_files.append(f)

        groups = {}
        # 1) birebir aynı dosyalar: önce boyuta göre, sonra içerik özetine göre
        by_size = {}
        for f in all_files:
            by_size.setdefault((f["ext"], f["size"]), []).append(f)
        with self._lock:
            for (_, size), fs in by_size.items():
                if len(fs) < 2 or size == 0:
                    continue
                by_hash = {}
                for f in fs:
                    h = self._hash(f)
                    if h:
                        by_hash.setdefault(h, []).append(f)
                for h, same in by_hash.items():
                    if len(same) > 1:
                        groups["h:" + h] = {"kind": "kesin", "reason": "Birebir aynı dosya", "files": same}
            try:
                self._save(self.hash_file, self.hashes)
            except OSError:
                pass
        # 2) aynı kitap (başlık+yazar) içinde aynı formattan birden fazla dosya
        for b in books:
            by_ext = {}
            for f in b["formats"]:
                by_ext.setdefault(f["ext"], []).append(f)
            for ext, fs in by_ext.items():
                if len(fs) < 2:
                    continue
                kind, reason = "muhtemel", "Aynı başlık ve yazar — farklı baskı/çeviri olabilir"
                pages = [f.get("pages") for f in fs]
                if ext == "pdf" and all(pages) and max(pages) <= min(pages) * 1.02:
                    kind, reason = "kesin", "Aynı kitap, sayfa sayısı aynı"
                if any(f.get("broken") for f in fs):
                    kind, reason = "kesin", "Aynı kitap, kopyalardan biri bozuk"
                groups[f"b:{b['id']}:{ext}"] = {"kind": kind, "reason": reason, "files": fs}

        # 2b) benzer kitaplar: yazım hatası ("Muvazeneleri"/"Muvazeleleri"), başlığa eklenmiş yazar adı
        #     ("El Muvafakat Şatıbi"/"El-Muvâfakât"), parantez/alt başlık/yıl farkı, ortak yazar
        n = 0
        for a, b2, fa, fb in similar_pairs(books):
            n += 1
            groups[f"f:{n}"] = {"kind": "muhtemel", "files": [fa, fb],
                                "reason": "Başlık ve yazar çok benzer — farklı baskı olabilir"}

        # 3) çakışan grupları birleştir (aynı dosya hem birebir kopya hem aynı kitap grubunda olabilir)
        parent = {}

        def find(x):
            while parent.setdefault(x, x) != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for key, g in groups.items():
            for f in g["files"]:
                parent[find(f["id"])] = find(key)
        merged = {}
        for key, g in groups.items():
            root = find(key)
            m = merged.setdefault(root, {"keys": [], "kinds": set(), "reasons": [], "files": {}})
            m["keys"].append(key)
            m["kinds"].add(g["kind"])
            if g["reason"] not in m["reasons"]:
                m["reasons"].append(g["reason"])
            for f in g["files"]:
                m["files"][f["id"]] = f
        groups = {sorted(m["keys"])[0]: {"kind": "kesin" if m["kinds"] == {"kesin"} else "muhtemel",
                                         "reason": " · ".join(m["reasons"]), "files": list(m["files"].values())}
                  for m in merged.values()}

        ignored = set(self.state.get("ignored", []))
        out = []
        self.different = []  # adı benzeyip içeriği farklı çıkanlar (kullanıcıya bilgi için)
        mids_changed = False
        for key, g in groups.items():
            scored = []
            for f in g["files"]:
                sc, why = self.score(f, placed)
                scored.append({"id": f["id"], "path": f["path"], "name": f["name"], "ext": f["ext"],
                               "size": f["size"], "pages": f.get("pages"), "needs_ocr": f.get("needs_ocr"),
                               "broken": f.get("broken"), "score": sc, "why": why, "ckey": f["ckey"]})
            scored.sort(key=lambda x: (-x["score"], -x["size"], x["path"]))
            exact_only = g["reason"] == "Birebir aynı dosya"
            kind, reason = g["kind"], g["reason"]
            if not exact_only:
                # İÇERİK KARŞILAŞTIRMASI: en iyi dosyanın metniyle diğerlerininki
                ref = next((x for x in scored if not x["broken"]), scored[0])
                ref_sh, mids_changed = self._shingles(ref, mids_changed)
                kept = [ref]
                sims = []
                for x in scored:
                    if x is ref:
                        continue
                    if x["broken"]:
                        x["sim"] = None
                        kept.append(x)
                        continue
                    sh, mids_changed = self._shingles(x, mids_changed)
                    sim = _containment(ref_sh, sh)
                    x["sim"] = sim
                    if sim is not None and sim < 0.15:
                        # metin farklı: başka çeviri/baskı ya da başka kitap -> kopya değil
                        bx, br = fmt_book.get(x["id"]) or {}, fmt_book.get(ref["id"]) or {}
                        self.different.append({"a": br.get("title", ""), "a_author": br.get("author", ""),
                                               "a_path": ref["path"], "b": bx.get("title", ""),
                                               "b_author": bx.get("author", ""), "b_path": x["path"], "sim": sim})
                        continue
                    kept.append(x)
                    sims.append(sim)
                if len(kept) < 2:
                    continue
                scored = kept
                known = [v for v in sims if v is not None]
                if known and min(known) >= 0.8 and len(known) == len(sims):
                    kind = "kesin"
                    reason = f"İçerik aynı (%{int(min(known) * 100)} eşleşme)"
                elif known:
                    kind = "muhtemel"
                    reason = f"İçerik kısmen benzer (%{int(min(known) * 100)}) — farklı baskı olabilir"
                else:
                    kind = "muhtemel"
                    reason = "İçerik karşılaştırılamadı (metin okunamadı) — " + g["reason"]
            scored[0]["keep"] = True
            pending = not exact_only and any(x["ext"] == "pdf" and x["needs_ocr"] is None and not x["broken"] for x in scored)
            b = fmt_book.get(scored[0]["id"]) or {}
            out.append({"key": key, "kind": kind, "reason": reason, "title": b.get("title", ""),
                        "author": b.get("author", ""), "files": scored, "pending": pending,
                        "ignored": key in ignored})
        if mids_changed:
            try:
                self._save(self.mid_file, self.mids)
            except OSError:
                pass
        out.sort(key=lambda g: (g["ignored"], g["kind"] != "kesin", g["title"].casefold()))
        return out

    # ---------- içerik parmak izi ----------
    def _mid(self, f):
        """Kitabın ortasından metin (ayrı süreçte, zaman aşımıyla); önbelleğe alınır."""
        import subprocess
        import sys
        k = f["ckey"]
        if k in self.mids:
            return self.mids[k], False
        text = ""
        try:
            r = subprocess.run([sys.executable, "-m", "ktphane.worker", self.lib.abs_path(f), f["ext"], "mid"],
                               capture_output=True, text=True, timeout=60,
                               cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            text = (json.loads(r.stdout or "{}") or {}).get("mid", "")
        except Exception as e:
            log.info("Orta metin okunamadı %s: %s", f["path"], e)
        self.mids[k] = text[:4000]
        return self.mids[k], True

    def _shingles(self, f, changed):
        a = (self.lib.enricher.get(f["ckey"]) or {}) if getattr(self.lib, "enricher", None) else {}
        mid, new = self._mid(f) if f["ext"] in ("epub", "pdf") else ("", False)
        text = (a.get("sample") or "") + " " + mid
        return _shingle_set(text), changed or new

    def auto_clean(self):
        """Otomatik mod: yalnızca 'kesin' kopyalar, analizi bitmiş ve yok sayılmamış olanlar."""
        if not self.state.get("auto"):
            return 0
        ids = []
        for g in self.find():
            if g["kind"] != "kesin" or g["pending"] or g["ignored"]:
                continue
            ids += [f["id"] for f in g["files"] if not f.get("keep")]
        if not ids:
            return 0
        done, _ = self.lib.trash.delete(ids, title="Otomatik kopya temizliği")
        log.info("Otomatik kopya temizliği: %d dosya silindi", len(done))
        return len(done)


def _shingle_set(text):
    """Metnin 4 kelimelik parçalarının özetleri. Az metin varsa None (karşılaştırılamaz)."""
    from .textutil import words
    w = words(text)
    if len(w) < 120:
        return None
    return {hash(" ".join(w[i:i + 4])) for i in range(len(w) - 3)}


def _containment(a, b):
    """İki metnin ortak parça oranı (küçük olana göre). Karşılaştırılamıyorsa None."""
    if not a or not b:
        return None
    return round(len(a & b) / min(len(a), len(b)), 2)


_EDITION = re.compile(r"\b(tam metin|orijinal|orjinal|yeni baski|genisletilmis|gozden gecirilmis|"
                      r"\d+\.? ?baski|baski|edition|ed|cilt|kitap|epub|pdf|e-?kitap|ebook)\b")


def norm_title(t):
    """Kopya karşılaştırması için sade başlık: parantez/köşeli ayraç içi, alt başlık (: ve – sonrası),
    yıllar ve baskı ibareleri atılır. Cilt numarası gibi diğer sayılar korunur."""
    from .textutil import fold
    t = fold(t or "")
    t = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", t)
    t = re.split(r"\s[:–—]\s|:\s", t)[0]
    t = re.sub(r"\b1[0-9]{3}\b|\b20[0-9]{2}\b", " ", t)
    t = t.replace("'", "").replace("’", "")
    t = _EDITION.sub(" ", t)
    return "".join(re.findall(r"[^\W_]+", t))


def first_author(a):
    """İlk yazarın soyadı (veya tek adı); 'Prof. Dr.' gibi ünvanlar atılır."""
    from .textutil import fold
    a = fold(a or "")
    a = re.split(r",|;|&|\sve\s|\sand\s|\s-\s", a)[0]
    toks = [t for t in re.findall(r"[^\W_]+", a) if t not in {"prof", "dr", "doc", "yrd", "hz", "imam", "seyh", "mr", "ed"}]
    return toks[-1] if toks else ""


_STOP = {"ve", "ile", "bir", "the", "of", "and", "a", "an", "el", "al", "fi", "de", "da", "ın", "in", "un", "un"}


def _title_tokens(t):
    from .textutil import fold
    t = fold(t or "")
    t = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", t)
    t = re.sub(r"\b1[0-9]{3}\b|\b20[0-9]{2}\b", " ", t.replace("'", "").replace("’", ""))
    t = _EDITION.sub(" ", t)
    return [w for w in re.findall(r"[^\W_]+", t)]


def _all_author_tokens(a):
    from .textutil import fold
    return {w for w in re.findall(r"[^\W_]+", fold(a or "")) if len(w) > 2}


def similar_pairs(books):
    """Aynı formatta, büyük olasılıkla aynı kitap olan dosya çiftleri: (kitapA, kitapB, dosyaA, dosyaB)."""
    from difflib import SequenceMatcher
    items = []
    for b in books:
        toks = _title_tokens(b["title"])
        nt = "".join(toks)
        if len(nt) < 6:
            continue
        items.append({"b": b, "nt": nt, "toks": set(toks), "key": {w for w in toks if w not in _STOP and len(w) >= 4},
                      "fa": first_author(b["author"]), "atoks": _all_author_tokens(b["author"]),
                      "digits": re.findall(r"\d+", nt), "vol": b.get("volume") if b.get("series") else None})
    # başlık ile yazar yer değiştirmiş olabilir ("Atakan Altınörs" / yazar: "Dil Felsefesine Giriş"):
    # başlık+yazar kelimelerinin tamamı aynıysa aday say
    combo = {}
    for i, it in enumerate(items):
        it["all"] = frozenset(w for w in it["toks"] | it["atoks"] if w not in _STOP)
        if len(it["all"]) >= 3:
            combo.setdefault(it["all"], []).append(i)
    index = {}
    for i, it in enumerate(items):
        for w in it["key"]:
            index.setdefault(w[:5], set()).add(i)  # ilk 5 harf: küçük yazım farklarına dayanıklı
    # çok yaygın kelimeler ("tarih", "islam") aday üretmez; her kitap en nadir 2 kelimesiyle aranır
    for it in items:
        ks = sorted({w[:5] for w in it["key"]}, key=lambda k: len(index[k]))
        it["probe"] = [k for k in ks if len(index[k]) <= 60] or ks[:2]
    seen, out = set(), []
    for i, a in enumerate(items):
        cands = set()
        for k in a["probe"]:
            cands |= index.get(k, set())
        swapped = {j for j in combo.get(a["all"], []) if j != i}
        cands |= swapped
        for j in cands:
            if j <= i or (i, j) in seen:
                continue
            seen.add((i, j))
            b = items[j]
            if a["b"]["id"] == b["b"]["id"]:
                continue
            if a["digits"] != b["digits"] or (a["vol"] or b["vol"]) and a["vol"] != b["vol"]:
                continue  # farklı cilt / sayı
            # yazar uyumu: ilk yazar aynı/benzer, biri boş, ya da yazar adı diğerinin başlığında geçiyor
            fa, fb = a["fa"], b["fa"]
            author_ok = (not fa or not fb or fa == fb or SequenceMatcher(None, fa, fb).ratio() >= 0.85
                         or fa in b["toks"] or fb in a["toks"])
            if j in swapped:  # başlık/yazar yer değiştirmiş: kelimeler aynı, içerik kontrolü karar verecek
                author_ok, forced = True, True
            else:
                forced = False
            if not author_ok:
                continue
            sm = SequenceMatcher(None, a["nt"], b["nt"])
            ratio = sm.ratio() if sm.real_quick_ratio() >= 0.9 and sm.quick_ratio() >= 0.9 else 0
            small, big = (a, b) if len(a["toks"]) <= len(b["toks"]) else (b, a)
            contained = (len(small["key"]) >= 1 and len(small["nt"]) >= 8
                         and small["toks"] <= (big["toks"] | big["atoks"] | small["atoks"])
                         and (big["toks"] - small["toks"]) <= (small["atoks"] | big["atoks"] | _STOP))
            if ratio < 0.9 and not contained and not forced:
                continue
            fa_by_ext = {}
            for f in a["b"]["formats"]:
                fa_by_ext.setdefault(f["ext"], f)
            for f in b["b"]["formats"]:
                if f["ext"] in fa_by_ext:
                    out.append((a["b"], b["b"], fa_by_ext[f["ext"]], f))
    return out


def _size(b):
    if b >= 1 << 30:
        return f"{b / (1 << 30):.1f} GB"
    if b >= 1 << 20:
        return f"{b / (1 << 20):.1f} MB"
    return f"{max(1, b // 1024)} KB"
