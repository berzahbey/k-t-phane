"""Kopya kitap bulma: hangisi daha iyi durumdaysa o kalır, diğerleri silinmeye önerilir.

Türler:
  kesin    — birebir aynı dosya (içerik özeti aynı) ya da aynı kitap + aynı format + PDF'te sayfa
             sayısı neredeyse aynı (±%2). Otomatik temizlik yalnızca bunlara dokunur.
  muhtemel — aynı başlık + yazar + format ama farklı baskı/çeviri olabilir; yalnızca listelenir,
             kullanıcı onaylamadan silinmez.
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

        # 2b) benzer başlık + aynı (ilk) yazar: "Cumhuriyet'in İlk Yüzyılı" ile
        #     "Cumhuriyet'in İlk Yüzyılı (1923-2023)" / "İlber Ortaylı, İsmail Küçükkaya" gibi
        fuzzy = {}
        for b in books:
            nt = norm_title(b["title"])
            if len(nt) < 6:
                continue
            vol = b.get("volume") if b.get("series") else None
            for f in b["formats"]:
                fuzzy.setdefault((nt, vol, f["ext"]), []).append((b, f))
        for (nt, vol, ext), items in fuzzy.items():
            if len({b["id"] for b, _ in items}) < 2:
                continue
            # yazarları uyuşanları kümele (biri boşsa başlığa güven)
            clusters = []
            for b, f in items:
                a = first_author(b["author"])
                for c in clusters:
                    if not a or not c["author"] or a == c["author"]:
                        c["files"].append(f)
                        c["author"] = c["author"] or a
                        break
                else:
                    clusters.append({"author": a, "files": [f]})
            for i, c in enumerate(clusters):
                if len(c["files"]) > 1:
                    groups[f"f:{ext}:{nt}:{vol}:{i}"] = {
                        "kind": "muhtemel", "files": c["files"],
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
        for key, g in groups.items():
            scored = []
            for f in g["files"]:
                sc, why = self.score(f, placed)
                scored.append({"id": f["id"], "path": f["path"], "name": f["name"], "ext": f["ext"],
                               "size": f["size"], "pages": f.get("pages"), "needs_ocr": f.get("needs_ocr"),
                               "broken": f.get("broken"), "score": sc, "why": why})
            scored.sort(key=lambda x: (-x["score"], -x["size"], x["path"]))
            scored[0]["keep"] = True
            # birebir aynı dosyalarda analiz beklemeye gerek yok; diğerlerinde kalite bilinmeli
            exact_only = g["reason"] == "Birebir aynı dosya"
            pending = not exact_only and any(x["ext"] == "pdf" and x["needs_ocr"] is None and not x["broken"] for x in scored)
            b = fmt_book.get(scored[0]["id"]) or {}
            out.append({"key": key, "kind": g["kind"], "reason": g["reason"], "title": b.get("title", ""),
                        "author": b.get("author", ""), "files": scored, "pending": pending,
                        "ignored": key in ignored})
        out.sort(key=lambda g: (g["ignored"], g["kind"] != "kesin", g["title"].casefold()))
        return out

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


def _size(b):
    if b >= 1 << 30:
        return f"{b / (1 << 30):.1f} GB"
    if b >= 1 << 20:
        return f"{b / (1 << 20):.1f} MB"
    return f"{max(1, b // 1024)} KB"
