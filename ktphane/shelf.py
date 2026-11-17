"""Stüdyo rafı: Dedplay Studio'da işlenip bitmiş kitaplar (okunur ve dinlenir).

Studio'nun çıktı klasörü (sunucuda /media/ZimaOS-HD/dedplay) Kitaplık'a salt okunur /studyo olarak bağlanır.
Düzen (Studio yazar):
    EPUB/<Dil>/<Eser - Yazar>.epub      Dil: Türkçe, Osmanlıca, Türkçe-Osmanlıca (yabancı asıllılarda Arapça… da)
    M4B/<Eser - Yazar>.m4b              büyük kitapta "<Eser - Yazar> - 1.m4b", "- 2.m4b" …
    MP3/<Eser - Yazar>/*.mp3            (M4B yoksa bunlar çalınır)
Aynı adı taşıyan dosyalar tek kitapta toplanır. Bu klasöre hiçbir şey yazılmaz; düzenleme, kopya bulma, silme
gibi işlemler bu kitaplara dokunmaz (yalnız kitap klasörü /books ile çalışırlar).

Kategori: kitabın Kitaplık'taki aslından gelir. Eşleştirme sırası: Studio'nun kitap kaydı (Kitaplık'ın gönderdiği
kitabın Studio kimliği) > başlık + yazar > yalnız başlık. Bulunamazsa "Stüdyo" yazar.

Dinleme konumu sunucuda tutulur (/data/dinleme.json): iPhone'da bıraktığın yerden Mac'te devam edilir.
"""
import hashlib
import json
import logging
import os
import re
import threading
import time
import unicodedata

from . import metadata
from .textutil import fold

log = logging.getLogger("ktphane.shelf")

EPUB_DIR, M4B_DIR, MP3_DIR, HTML_DIR, PDF_DIR = "EPUB", "M4B", "MP3", "HTML", "PDF"
LANG_ORDER = ["Türkçe", "Osmanlıca", "Türkçe-Osmanlıca", "Arapça", "Arapça-Türkçe", "İngilizce", "İngilizce-Türkçe",
              "Fransızca", "Fransızca-Türkçe"]
LANG_LABEL = {"Türkçe-Osmanlıca": "Türkçe + Osmanlıca", "Arapça-Türkçe": "Arapça + Türkçe",
              "İngilizce-Türkçe": "İngilizce + Türkçe", "Fransızca-Türkçe": "Fransızca + Türkçe"}
LANG_SHORT = {"Türkçe": "TR", "Osmanlıca": "OSM", "Türkçe-Osmanlıca": "TR+OSM", "Arapça": "AR",
              "Arapça-Türkçe": "AR+TR", "İngilizce": "EN", "İngilizce-Türkçe": "EN+TR", "Fransızca": "FR",
              "Fransızca-Türkçe": "FR+TR"}
RTL = {"Osmanlıca", "Arapça"}
TTL = 20            # klasör en fazla bu kadar saniyede bir yeniden okunur
STUDIO_TTL = 300    # Studio'nun kitap kaydı (kategori eşleştirmesi için) bu sıklıkta sorulur


def fid_of(rel):
    """Raf dosyasının kimliği: 's' + yolun özeti. Kitaplık dosya kimlikleri onaltılık olduğundan karışmaz."""
    return "s" + hashlib.sha1(("studyo/" + rel).encode("utf-8", "surrogateescape")).hexdigest()[:15]


def is_shelf_id(fid):
    return isinstance(fid, str) and len(fid) == 16 and fid[0] == "s"


def _natural(s):
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", s)]


def key_of(name):
    """Aynı kitabın dosyalarını eşleştiren sade ad (büyük/küçük harf, şapka, Türkçe harf ve noktalama farkı yok)."""
    s = unicodedata.normalize("NFC", name or "")
    return re.sub(r"[^0-9a-z]+", " ", fold(s)).strip()


def split_name(name):
    if " - " in name:
        t, a = name.split(" - ", 1)
        return t.strip(), a.strip()
    return name.strip(), ""


class Shelf:
    def __init__(self, lib, root=None):
        self.lib = lib
        self.root = root or os.environ.get("STUDYO_DIR", "/studyo")
        self._lock = threading.RLock()
        self._cache = (0.0, None)          # (zaman, kitaplar)
        self._files = {}                   # fid -> {path(abs), rel, ext, name, kind, lang}
        self._match = (None, {})           # (anahtar, eşleşmeler)
        self._studio = (0.0, [])           # Studio /api/kitaplar
        self._studio_busy = False
        self.listen_file = os.path.join(lib.data_dir, "dinleme.json")
        self.listen = lib._read_json(self.listen_file, {})
        self._llock = threading.Lock()
        self._conv_lock = threading.Lock()

    # ---------- klasör ----------
    def available(self):
        return os.path.isdir(self.root)

    def has_content(self):
        return any(os.path.isdir(os.path.join(self.root, d)) for d in (EPUB_DIR, M4B_DIR, MP3_DIR))

    def _scan(self):
        books, files = {}, {}

        def book(name):
            k = key_of(name)
            if not k:
                return None
            b = books.get(k)
            if b is None:
                title, author = split_name(name)
                b = books[k] = {"key": k, "name": name, "title": title, "author": author, "versions": [],
                                "audio": [], "added": 0}
            return b

        def add_file(rel, kind, lang=None):
            p = os.path.join(self.root, rel)
            try:
                st = os.stat(p)
            except OSError:
                return None
            fid = fid_of(rel)
            files[fid] = {"id": fid, "path": p, "rel": rel, "name": os.path.basename(rel),
                          "ext": os.path.splitext(rel)[1].lower().lstrip("."), "kind": kind, "lang": lang,
                          "size": st.st_size, "mtime": int(st.st_mtime)}
            return files[fid]

        # EPUB (Kütüphane'den geçenler), HTML ve PDF (Studio her kitap için üç dilde yazar). EPUB'u olmayan dil
        # sürümü HTML'den EPUB'a çevrilerek okunur (htmlepub), böylece okuyucunun bütün özellikleri çalışır.
        texts = {}   # (kitap anahtarı, dil) -> {"epub": rec, "html": rec, "pdf": rec}
        for sub, ext in ((EPUB_DIR, ".epub"), (HTML_DIR, ".html"), (PDF_DIR, ".pdf")):
            ed = os.path.join(self.root, sub)
            if not os.path.isdir(ed):
                continue
            for lang in sorted(os.listdir(ed)):
                ld = os.path.join(ed, lang)
                if lang.startswith(".") or not os.path.isdir(ld):
                    continue
                for f in os.listdir(ld):
                    stem, e = os.path.splitext(f)
                    if f.startswith(".") or e.lower() != ext:
                        continue
                    rec = add_file(f"{sub}/{lang}/{f}", ext[1:], lang)
                    b = rec and book(stem)
                    if b:
                        texts.setdefault((b["key"], lang), {})[ext[1:]] = rec
                        b["added"] = max(b["added"], rec["mtime"])
        for (k, lang), fm in texts.items():
            b = books[k]
            main = fm.get("epub") or fm.get("html")
            if fm.get("pdf"):
                b.setdefault("pdfs", []).append(fm["pdf"]["id"])
            if not main:
                continue
            b["versions"].append({"id": main["id"], "lang": lang, "label": LANG_LABEL.get(lang, lang),
                                  "short": LANG_SHORT.get(lang, lang[:3].upper()), "size": main["size"],
                                  "rtl": lang in RTL, "from_html": main["ext"] == "html",
                                  "pdf": fm["pdf"]["id"] if fm.get("pdf") else None})
        md = os.path.join(self.root, M4B_DIR)
        if os.path.isdir(md):
            for f in sorted(os.listdir(md), key=_natural):
                if f.startswith(".") or not f.lower().endswith(".m4b"):
                    continue
                rec = add_file(f"{M4B_DIR}/{f}", "audio")
                b = rec and book(re.sub(r" - \d+$", "", f[:-4]))
                if b:
                    b["audio"].append({"id": rec["id"], "name": f, "size": rec["size"], "type": "audio/mp4"})
                    b["added"] = max(b["added"], rec["mtime"])
        pd = os.path.join(self.root, MP3_DIR)
        if os.path.isdir(pd):
            for d in os.listdir(pd):
                dd = os.path.join(pd, d)
                if d.startswith(".") or not os.path.isdir(dd):
                    continue
                b = books.get(key_of(d))
                if b is None or b["audio"]:   # M4B varsa o çalınır; MP3'ler yalnız M4B yoksa
                    continue
                for f in sorted(os.listdir(dd), key=_natural):
                    if f.lower().endswith(".mp3") and not f.startswith("."):
                        rec = add_file(f"{MP3_DIR}/{d}/{f}", "audio")
                        if rec:
                            b["audio"].append({"id": rec["id"], "name": f, "size": rec["size"], "type": "audio/mpeg"})
        self._merge(books)
        out = []
        for b in books.values():
            if not b["versions"] and not b["audio"]:
                continue
            b["versions"].sort(key=lambda v: LANG_ORDER.index(v["lang"]) if v["lang"] in LANG_ORDER else 99)
            b["id"] = "s" + hashlib.sha1(b["key"].encode()).hexdigest()[:15]
            epubs = [v["id"] for v in b["versions"] if not v.get("from_html")]
            b["cover_id"] = (epubs or b.get("pdfs") or [None])[0]
            b.pop("pdfs", None)
            b["audio_parts"] = len(b["audio"])
            out.append(b)
        return out, files

    @staticmethod
    def _words(s):
        return {w for w in key_of(s).split() if len(w) > 1}

    def _merge(self, books):
        """Aynı kitabın farklı adla yazılmış dosyalarını birleştirir. Studio sesi ve PDF/HTML'yi işin adıyla
        ("Yazar - Başlık" ya da kısaltılmış ad), EPUB'u künyedeki "Başlık - Yazar" ile yazabiliyor."""
        items = list(books.values())
        info = {}
        for b in items:
            t, a = split_name(b["name"])
            info[b["key"]] = (self._words(b["name"]), self._words(t), self._words(a))

        def same(x, y):
            wx, tx, ax = info[x["key"]]
            wy, ty, ay = info[y["key"]]
            if not tx or not ty:
                return False
            if {w for w in wx if w.isdigit()} != {w for w in wy if w.isdigit()}:
                return False                   # Cilt 1 ile Cilt 2 birleşmez
            if wx == wy:                       # aynı kelimeler, farklı sıra ("Yazar - Başlık" / "Başlık - Yazar")
                return True
            if ty <= wx or tx <= wy:           # birinin başlığı ötekinde tümüyle var
                if ax and ay:
                    return bool(ax & ay)       # iki yazar da biliniyorsa ortak kelime olmalı
                return min(len(tx), len(ty)) >= 2   # yazar yoksa tek kelimelik başlık ("Ahlak") yetmez
            return False

        langs = lambda b: {v["lang"] for v in b["versions"]}
        for a in sorted(items, key=lambda b: -len(b["versions"])):
            if a["key"] not in books:
                continue
            for b in items:
                if b is a or b["key"] not in books or a["key"] not in books:
                    continue
                # yalnız birbirini tamamlayanlar birleşir (birinde ses, ötekinde metin ya da farklı diller)
                complement = bool(a["audio"]) != bool(b["audio"]) or not (langs(a) & langs(b))
                if complement and same(a, b):
                    real = lambda x: any(not v.get("from_html") for v in x["versions"])
                    if real(b) and not real(a):    # künyeden gelen EPUB adı ("Başlık - Yazar") daha doğru
                        a["name"], a["title"], a["author"] = b["name"], b["title"], b["author"]
                    have = langs(a)
                    a["versions"] += [v for v in b["versions"] if v["lang"] not in have]
                    a["audio"] = a["audio"] or b["audio"]
                    a.setdefault("pdfs", []).extend(b.get("pdfs") or [])
                    a["added"] = max(a["added"], b["added"])
                    if not a["author"] and b["author"]:
                        a["author"] = b["author"]
                    a.setdefault("aliases", []).append(b["key"])
                    del books[b["key"]]

    def books(self):
        """Raftaki kitaplar (kategorileri Kitaplık'taki asıllarından)."""
        now = time.time()
        with self._lock:
            t, cached = self._cache
            if cached is None or now - t > TTL:
                if self.available():
                    try:
                        cached, files = self._scan()
                    except OSError as e:
                        log.warning("Stüdyo klasörü okunamadı: %s", e)
                        cached, files = [], {}
                else:
                    cached, files = [], {}
                self._files = files
                self._cache = (now, cached)
                self._match = (None, {})
            items = cached
        self._refresh_studio()
        matches = self._matches(items)
        out = []
        for b in items:
            m = matches.get(b["id"]) or {}
            if m.get("swap"):
                b = {**b, "title": b["author"], "author": b["title"]}
            out.append({**b, "category": m.get("category") or "Stüdyo", "subcategory": m.get("subcategory") or "",
                        "language": m.get("language") or "", "source_id": m.get("book_id"),
                        "source_title": m.get("title"), "matched_by": m.get("by")})
        out.sort(key=lambda b: b["title"].casefold())
        return out

    def get_file(self, fid):
        if not is_shelf_id(fid):
            return None
        with self._lock:
            rec = self._files.get(fid)
        if rec is None and self._cache[1] is None:
            self.books()
            with self._lock:
                rec = self._files.get(fid)
        return rec

    def book_of_file(self, fid):
        for b in self.books():
            if any(v["id"] == fid for v in b["versions"]) or any(a["id"] == fid for a in b["audio"]):
                return b
        return None

    def refresh(self):
        with self._lock:
            self._cache = (0.0, self._cache[1])

    # ---------- okunacak EPUB ----------
    def epub_path(self, rec):
        """Okuyucuya verilecek EPUB: kendisi ya da (HTML ise) önbellekteki çevirisi."""
        if rec["ext"] == "epub":
            return rec["path"]
        if rec["ext"] != "html":
            return None
        d = os.path.join(self.lib.data_dir, "studyo-epub")
        os.makedirs(d, exist_ok=True)
        dest = os.path.join(d, f"{rec['id']}-{rec['size']}-{rec['mtime']}.epub")
        if os.path.exists(dest):
            return dest
        with self._conv_lock:
            if not os.path.exists(dest):
                for old in os.listdir(d):
                    if old.startswith(rec["id"] + "-"):
                        try:
                            os.remove(os.path.join(d, old))
                        except OSError:
                            pass
                b = self.book_of_file(rec["id"]) or {}
                html_to_epub(rec["path"], dest, b.get("title") or os.path.splitext(rec["name"])[0],
                             b.get("author") or "", rec.get("lang") or "")
        return dest

    # ---------- kapak ----------
    def cover_path(self, fid):
        rec = self.get_file(fid)
        if not rec or rec["ext"] not in ("epub", "pdf"):
            return None
        key = f"{fid}-{rec['size']}-{rec['mtime']}"
        dest = os.path.join(self.lib.cover_dir, key + ".jpg")
        miss = dest + ".none"
        if os.path.exists(dest):
            return dest
        if os.path.exists(miss):
            return None
        member = None
        if rec["ext"] == "epub":
            try:
                member = metadata.read_epub(rec["path"]).get("cover")
            except Exception:
                member = None
        tmp = f"{dest}.{threading.get_ident()}.tmp"
        if (member or rec["ext"] == "pdf") and metadata.make_cover(rec["ext"], rec["path"], member, tmp):
            os.replace(tmp, dest)
            return dest
        open(miss, "w").close()
        return None

    # ---------- kategori eşleştirme ----------
    def _refresh_studio(self):
        """Studio'nun kitap kaydını arka planda tazeler (sayfa açılışını bekletmez)."""
        st = getattr(self.lib, "studio", None)
        if not st or self._studio_busy or time.time() - self._studio[0] < STUDIO_TTL:
            return
        self._studio_busy = True

        def run():
            data = []
            try:
                code, d = st._req(st.base(), "GET", "/api/kitaplar", timeout=8)
                if code == 200 and isinstance(d, list):
                    data = d
            except Exception as e:
                log.info("Studio kitap kaydı alınamadı: %s", e)
            with self._lock:
                old = self._studio[1]
                self._studio = (time.time(), data or old)
                if data:
                    self._match = (None, {})
            self._studio_busy = False
        threading.Thread(target=run, daemon=True, name="studyo-kayit").start()

    def _matches(self, items):
        lib = self.lib
        stamp = (id(items), getattr(lib, "_version", 0), self._studio[0],
                 len(getattr(getattr(lib, "studio", None), "sent", {}) or {}))
        with self._lock:
            if self._match[0] == stamp:
                return self._match[1]
            studio_rows = self._studio[1]
        books = lib.books()
        by_ckey, by_title, by_full = {}, {}, {}
        for b in books:
            for f in b["formats"]:
                by_ckey[f["ckey"]] = b
            by_title.setdefault(key_of(b["title"]), []).append(b)
            by_full.setdefault(key_of(f"{b['title']} {b['author']}"), b)
        # Studio kimliği -> Kitaplık kitabı (Kitaplık'tan gönderilenler)
        by_kid = {}
        st = getattr(lib, "studio", None)
        for ck, s in list(((st.sent if st else {}) or {}).items()):
            if s.get("kimlik") and ck in by_ckey:
                by_kid[s["kimlik"]] = by_ckey[ck]
        # raf adı -> Studio kimliği
        name_kid = {}
        for r in studio_rows:
            kid = r.get("kimlik")
            if not kid:
                continue
            for c in r.get("cikti") or []:
                name_kid.setdefault(key_of(os.path.splitext(os.path.basename(c))[0]), kid)
            if r.get("baslik"):
                y = r.get("yazar") or ""
                name_kid.setdefault(key_of(f"{r['baslik']} - {y}" if y and y != r["baslik"] else r["baslik"]), kid)

        def pack(b, by):
            return {"category": b.get("category"), "subcategory": b.get("subcategory"), "language": b.get("language"),
                    "book_id": b["id"], "title": b["title"], "by": by}

        out = {}
        for it in items:
            src = by_kid.get(name_kid.get(it["key"]))
            if src:
                out[it["id"]] = pack(src, "studio")
                continue
            src = by_full.get(key_of(f"{it['title']} {it['author']}"))
            if src:
                out[it["id"]] = pack(src, "title+author")
                continue
            src = it["author"] and by_full.get(key_of(f"{it['author']} {it['title']}"))
            if src:   # ad "Yazar - Başlık" sırasında yazılmış
                out[it["id"]] = {**pack(src, "swapped"), "swap": True}
                continue
            cands = by_title.get(key_of(it["title"])) or []
            if it["author"]:
                a = key_of(it["author"])
                cands = [b for b in cands if not b["author"] or a in key_of(b["author"]) or key_of(b["author"]) in a] or \
                    ([] if len(cands) > 1 else cands)
            if len({(b["category"], b["subcategory"]) for b in cands}) == 1:
                out[it["id"]] = pack(cands[0], "title")
        with self._lock:
            self._match = (stamp, out)
        return out

    # ---------- dinleme konumu ----------
    def get_listen(self, bid):
        with self._llock:
            return dict(self.listen.get(bid) or {})

    def set_listen(self, bid, d):
        clean = {"updated": int(time.time() * 1000)}
        if isinstance(d.get("part"), int) and 0 <= d["part"] < 10000:
            clean["part"] = d["part"]
        for k in ("time", "duration", "rate"):
            if isinstance(d.get(k), (int, float)) and d[k] >= 0:
                clean[k] = round(float(d[k]), 2)
        if isinstance(d.get("percent"), (int, float)):
            clean["percent"] = max(0.0, min(1.0, float(d["percent"])))
        if isinstance(d.get("device"), str):
            clean["device"] = d["device"][:60]
        if d.get("finished"):
            clean["finished"] = True
        with self._llock:
            self.listen[bid] = clean
            tmp = self.listen_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.listen, f, ensure_ascii=False)
            os.replace(tmp, self.listen_file)
        return clean

    def all_listen(self):
        with self._llock:
            return {k: dict(v) for k, v in self.listen.items()}


# ---------------- HTML -> EPUB (EPUB'u olmayan Studio kitapları için) ----------------
import html as _html
import zipfile
from html.parser import HTMLParser

CHAPTER_CHARS = 60000   # bir bölüm (xhtml) en fazla bu kadar harf; başlık bulunamazsa böyle bölünür
EPUB_CSS = """
body { line-height: 1.7; }
p { margin: 0 0 .9em; text-align: justify; }
p.osm { font-family: "Amiri", "Noto Naskh Arabic", serif; font-size: 1.25em; line-height: 1.9; direction: rtl; }
.iki p.tr { margin-bottom: .2em; opacity: .8; }
.iki p.osm { margin-bottom: 1.2em; }
h1, h2 { text-align: center; line-height: 1.35; margin: 1.2em 0 1em; }
h2 { font-size: 1.25em; }
.kunye { text-align: center; margin-top: 30%; }
.kunye p { text-align: center; opacity: .7; }
"""


class _Paras(HTMLParser):
    """Studio HTML'sindeki başlık (h1) ve paragrafları (p.tr / p.osm) sırasıyla toplar."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.label, self.paras = "", "", []
        self._cur, self._kind, self._in_title = None, None, False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div" and "title" in (a.get("class") or "").split():
            self._in_title = True
        if tag in ("p", "h1"):
            self._cur, self._kind = [], ("h1" if tag == "h1" else ("osm" if "osm" in (a.get("class") or "") else "tr"))

    def handle_endtag(self, tag):
        if tag in ("p", "h1") and self._cur is not None:
            t = " ".join("".join(self._cur).split())
            if self._in_title:
                if tag == "h1":
                    self.title = t
                else:
                    self.label = t
            elif t:
                self.paras.append((self._kind, t))
            self._cur = None
        if tag == "div" and self._in_title and self._cur is None:
            self._in_title = False

    def handle_data(self, d):
        if self._cur is not None:
            self._cur.append(d)


def _is_heading(text):
    """Kısa, noktayla bitmeyen satır (BİRİNCİ BÖLÜM, Önsöz, 1. Mektup…) bölüm başı sayılır."""
    t = text.strip()
    if not (2 <= len(t) <= 70) or t[-1] in ".,;:…!?»\"'’)" and not t.isupper():
        return False
    letters = [c for c in t if c.isalpha()]
    if not letters or not (t[0].isupper() or t[0].isdigit() or not t[0].isascii() and not t[0].islower()):
        return False
    up = sum(c.isupper() for c in letters) / len(letters)
    return up > 0.7 or (len(t.split()) <= 6 and t[-1] not in ".,;:…")


def html_to_epub(src, dest, title, author, lang_dir):
    """Studio'nun HTML çıktısını bölümlü bir EPUB 3'e çevirir (içindekiler dahil)."""
    with open(src, encoding="utf-8", errors="replace") as f:
        p = _Paras()
        p.feed(f.read())
    variant = {"Osmanlıca": "osm", "Türkçe-Osmanlıca": "iki"}.get(lang_dir, "tr")
    rtl = variant == "osm"
    lang = "ota" if rtl else "tr"
    book_title = p.title or title
    # bölümler: başlık satırında (iki dilli sürümde Türkçe satırda) yeni bölüm; uzun bölümler ayrıca bölünür
    chapters, cur, size = [], {"title": None, "paras": []}, 0
    for kind, text in p.paras:
        head = kind != "osm" and _is_heading(text) or (variant == "osm" and _is_heading(text) and len(text) <= 40)
        if head and (cur["paras"] or cur["title"]):
            if cur["paras"]:
                chapters.append(cur)
            cur, size = {"title": text, "paras": []}, 0
            continue
        if head:
            cur["title"] = text
            continue
        if size > CHAPTER_CHARS and kind != "osm":
            chapters.append(cur)
            cur, size = {"title": None, "paras": []}, 0
        cur["paras"].append((kind, text))
        size += len(text)
    if cur["paras"] or cur["title"]:
        chapters.append(cur)
    esc = _html.escape
    n_untitled = 0
    for c in chapters:
        if not c["title"]:
            n_untitled += 1
            c["toc"] = f"Kısım {n_untitled}"
        else:
            c["toc"] = c["title"]
    dir_attr = ' dir="rtl"' if rtl else ""

    def page(body, cls=""):
        return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
                f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}" xml:lang="{lang}"{dir_attr}>'
                f'<head><meta charset="utf-8"/><title>{esc(book_title)}</title><link rel="stylesheet" href="kitap.css"/></head>'
                f'<body class="{variant} {cls}">{body}</body></html>')

    def para(kind, text):
        if kind == "osm":
            return f'<p class="osm" dir="rtl" lang="ota" xml:lang="ota">{esc(text)}</p>'
        return f'<p class="tr">{esc(text)}</p>'

    files = [("kunye.xhtml", page(f'<div class="kunye"><h1{dir_attr}>{esc(book_title)}</h1>'
                                   + (f"<p>{esc(author)}</p>" if author else "") + f"<p>{esc(p.label or lang_dir)}</p></div>"))]
    for i, c in enumerate(chapters, 1):
        h = f"<h2>{esc(c['title'])}</h2>" if c["title"] else ""
        files.append((f"bolum_{i:04d}.xhtml", page(h + "".join(para(k, t) for k, t in c["paras"]))))
    nav = "".join(f'<li><a href="bolum_{i:04d}.xhtml">{esc(c["toc"])}</a></li>' for i, c in enumerate(chapters, 1))
    nav_x = ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
             f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}"><head><title>İçindekiler</title></head>'
             f'<body><nav epub:type="toc" id="toc"><h1>İçindekiler</h1><ol>{nav}</ol></nav></body></html>')
    ncx_pts = "".join(f'<navPoint id="n{i}" playOrder="{i}"><navLabel><text>{esc(c["toc"])}</text></navLabel>'
                      f'<content src="bolum_{i:04d}.xhtml"/></navPoint>' for i, c in enumerate(chapters, 1))
    uid = "urn:uuid:" + hashlib.sha1(src.encode("utf-8", "surrogateescape")).hexdigest()
    ncx = (f'<?xml version="1.0" encoding="utf-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
           f'<head><meta name="dtb:uid" content="{uid}"/></head><docTitle><text>{esc(book_title)}</text></docTitle>'
           f'<navMap>{ncx_pts}</navMap></ncx>')
    ppd = ' page-progression-direction="rtl"' if rtl else ""
    manifest = "".join(f'<item id="x{i}" href="{n}" media-type="application/xhtml+xml"/>' for i, (n, _) in enumerate(files))
    spine = "".join(f'<itemref idref="x{i}"/>' for i in range(len(files)))
    opf = (f'<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang="{lang}">'
           f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="uid">{uid}</dc:identifier>'
           f'<dc:title>{esc(book_title)}</dc:title><dc:language>{lang}</dc:language>'
           + (f"<dc:creator>{esc(author)}</dc:creator>" if author else "")
           + f'<meta property="dcterms:modified">{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}</meta></metadata>'
           f'<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
           f'<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
           f'<item id="css" href="kitap.css" media-type="text/css"/>{manifest}</manifest>'
           f'<spine toc="ncx"{ppd}>{spine}</spine></package>')
    tmp = f"{dest}.{threading.get_ident()}.tmp"
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
                   compress_type=zipfile.ZIP_DEFLATED)
        for name, data in [("content.opf", opf), ("nav.xhtml", nav_x), ("toc.ncx", ncx), ("kitap.css", EPUB_CSS)] + files:
            z.writestr("OEBPS/" + name, data, compress_type=zipfile.ZIP_DEFLATED)
    os.replace(tmp, dest)
    return dest
