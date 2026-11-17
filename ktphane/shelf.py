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

EPUB_DIR, M4B_DIR, MP3_DIR = "EPUB", "M4B", "MP3"
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

        ed = os.path.join(self.root, EPUB_DIR)
        if os.path.isdir(ed):
            for lang in sorted(os.listdir(ed)):
                ld = os.path.join(ed, lang)
                if lang.startswith(".") or not os.path.isdir(ld):
                    continue
                for f in os.listdir(ld):
                    stem, ext = os.path.splitext(f)
                    if f.startswith(".") or ext.lower() != ".epub":
                        continue
                    rec = add_file(f"{EPUB_DIR}/{lang}/{f}", "epub", lang)
                    b = rec and book(stem)
                    if b:
                        b["versions"].append({"id": rec["id"], "lang": lang, "label": LANG_LABEL.get(lang, lang),
                                              "short": LANG_SHORT.get(lang, lang[:3].upper()), "size": rec["size"],
                                              "rtl": lang in RTL})
                        b["added"] = max(b["added"], rec["mtime"])
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
        out = []
        for b in books.values():
            if not b["versions"] and not b["audio"]:
                continue
            b["versions"].sort(key=lambda v: LANG_ORDER.index(v["lang"]) if v["lang"] in LANG_ORDER else 99)
            b["id"] = "s" + hashlib.sha1(b["key"].encode()).hexdigest()[:15]
            b["cover_id"] = b["versions"][0]["id"] if b["versions"] else None
            b["audio_parts"] = len(b["audio"])
            out.append(b)
        return out, files

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

    # ---------- kapak ----------
    def cover_path(self, fid):
        rec = self.get_file(fid)
        if not rec or rec["ext"] != "epub":
            return None
        key = f"{fid}-{rec['size']}-{rec['mtime']}"
        dest = os.path.join(self.lib.cover_dir, key + ".jpg")
        miss = dest + ".none"
        if os.path.exists(dest):
            return dest
        if os.path.exists(miss):
            return None
        try:
            member = metadata.read_epub(rec["path"]).get("cover")
        except Exception:
            member = None
        tmp = f"{dest}.{threading.get_ident()}.tmp"
        if member and metadata.make_cover("epub", rec["path"], member, tmp):
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
