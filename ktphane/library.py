"""Kitap klasörünü tarar, sonuçları diske kaydeder, arka planda güncel tutar."""
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time

from . import metadata
from .categories import classify
from .textutil import detect_language, fold, group_key, tr_title

log = logging.getLogger("ktphane.library")

FORMATS = {"pdf", "epub", "mobi", "azw", "azw3", "djvu", "djv", "fb2", "txt", "cbz", "cbr", "doc",
           "docx", "rtf", "odt"}
SKIP_DIRS = {"@eaDir", "#recycle", "#snapshot", "$RECYCLE.BIN", "System Volume Information",
             "lost+found", ".Trash", ".Trashes", ".AppleDouble", "__MACOSX"}
CACHE_VERSION = 3


def _fid(rel):
    return hashlib.sha1(rel.encode("utf-8", "surrogateescape")).hexdigest()[:16]


def parse_filename(filename):
    """'Yazar - Başlık.pdf' -> (başlık, yazar). Yalnızca boşluklu tire ayraç sayılır;
    'Ibn-Sina' gibi isimler bölünmez."""
    stem = os.path.splitext(filename)[0]
    stem = stem.replace("_", " ")
    stem = metadata.JUNK_RE.sub(" ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" -–—.")
    parts = [p.strip() for p in re.split(r"\s+[-–—]\s+", stem) if p.strip()]
    if len(parts) >= 2:
        return tr_title(" - ".join(parts[1:])), tr_title(parts[0])
    return tr_title(stem), ""


def _looks_junk(title):
    """'scan0001', 'IMG_2020', '12345' gibi anlamsız dosya adları."""
    letters = sum(c.isalpha() for c in title)
    return letters < 3 or bool(re.fullmatch(r"(?i)(scan|img|dsc|doc|file|book|kitap|untitled)?[\W_\d]*", title))


def choose_title_author(ext, fn_title, fn_author, meta_title, meta_author, name):
    """EPUB'da dosyanın kendi bilgisi genelde doğrudur; PDF'lerde ise çoğu zaman
    'Microsoft Word - ...' gibi çöp olduğundan önce dosya adı kullanılır."""
    if meta_title and fn_author and not meta_author and fold(fn_author) in fold(meta_title):
        meta_title = ""  # PDF başlığı aslında 'Yazar - Başlık' dosya adının kopyası
    if ext == "epub":
        title = meta_title or fn_title
        author = meta_author or fn_author
    else:
        title = fn_title if fn_title and not _looks_junk(fn_title) else (meta_title or fn_title)
        author = fn_author or meta_author
    return tr_title(title or name), tr_title(author)


class Library:
    def __init__(self, book_dir, data_dir, scan_interval=1800):
        self.book_dir = book_dir
        self.data_dir = self._usable_data_dir(data_dir)
        self.cover_dir = os.path.join(self.data_dir, "covers")
        os.makedirs(self.cover_dir, exist_ok=True)
        self.cache_file = os.path.join(self.data_dir, "library.json")
        self.scan_interval = scan_interval
        self._lock = threading.RLock()
        self._scan_lock = threading.Lock()
        self.files = {}          # fid -> dosya kaydı
        self.status = {"scanning": False, "done": 0, "total": 0, "last_scan": None,
                       "error": None, "duration": None}
        self._version = 0
        self._books_cache = (None, None)
        self._load_cache()

    # ---------- kalıcılık ----------
    @staticmethod
    def _usable_data_dir(path):
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".write-test")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            return path
        except OSError as e:
            fallback = os.path.join(tempfile.gettempdir(), "ktphane-data")
            log.warning("%s yazılamıyor (%s); geçici klasör kullanılacak: %s", path, e, fallback)
            os.makedirs(fallback, exist_ok=True)
            return fallback

    def _load_cache(self):
        try:
            with open(self.cache_file, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") == CACHE_VERSION:
                self.files = data.get("files", {})
                self.status["last_scan"] = data.get("last_scan")
                log.info("Önbellekten %d dosya yüklendi", len(self.files))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("Önbellek okunamadı: %s", e)

    def _save_cache(self):
        tmp = self.cache_file + ".tmp"
        with self._lock:
            payload = {"version": CACHE_VERSION, "last_scan": self.status["last_scan"],
                       "files": self.files}
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, self.cache_file)

    # ---------- tarama ----------
    def start_background(self):
        def loop():
            self.scan()
            while self.scan_interval and self.scan_interval > 0:
                time.sleep(self.scan_interval)
                self.scan()
        threading.Thread(target=loop, name="scanner", daemon=True).start()

    def trigger_scan(self, full=False):
        if self._scan_lock.locked():
            return False
        threading.Thread(target=self.scan, kwargs={"full": full}, daemon=True).start()
        return True

    def _walk(self):
        for root, dirs, files in os.walk(self.book_dir, followlinks=True):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
            for name in files:
                if name.startswith(".") or name.startswith("~$"):
                    continue  # macOS ._ dosyaları, Office geçici dosyaları
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                if ext in FORMATS:
                    yield os.path.join(root, name), ext

    def scan(self, full=False):
        if not self._scan_lock.acquire(blocking=False):
            return
        t0 = time.time()
        try:
            if not os.path.isdir(self.book_dir):
                self.status.update(error=f"Kitap klasörü bulunamadı: {self.book_dir} "
                                         "(compose dosyasındaki volume yolunu kontrol edin)")
                log.error(self.status["error"])
                return
            found = list(self._walk())
            self.status.update(scanning=True, done=0, total=len(found), error=None)
            old = {} if full else dict(self.files)
            new = {}
            dirty = 0
            for i, (full_path, ext) in enumerate(found, 1):
                rel = os.path.relpath(full_path, self.book_dir)
                fid = _fid(rel)
                try:
                    st = os.stat(full_path)
                except OSError:
                    continue
                prev = old.get(fid)
                if prev and prev.get("mtime") == int(st.st_mtime) and prev.get("size") == st.st_size:
                    new[fid] = prev
                else:
                    self._drop_cover(fid)
                    new[fid] = self._analyse(fid, rel, full_path, ext, st)
                    dirty += 1
                    if dirty % 50 == 0:  # büyük arşivde kademeli görünür olsun
                        with self._lock:
                            self.files = {**self.files, **new}
                            self._version += 1
                self.status["done"] = i
            with self._lock:
                removed = set(self.files) - set(new)
                self.files = new
                self._version += 1
            for fid in removed:
                self._drop_cover(fid)
            self.status["last_scan"] = int(time.time())
            self._save_cache()
            log.info("Tarama bitti: %d dosya, %d yeni/değişmiş, %d silinmiş, %.1fs",
                     len(new), dirty, len(removed), time.time() - t0)
        except Exception as e:
            log.exception("Tarama hatası")
            self.status["error"] = str(e)
        finally:
            self.status["scanning"] = False
            self.status["duration"] = round(time.time() - t0, 1)
            self._scan_lock.release()

    def _analyse(self, fid, rel, full_path, ext, st):
        name = os.path.basename(rel)
        folders = [p for p in os.path.dirname(rel).split(os.sep) if p]
        title, author = parse_filename(name)
        rec = {"id": fid, "path": rel, "name": name, "ext": ext, "size": st.st_size,
               "mtime": int(st.st_mtime), "folders": folders, "subjects": [],
               "lang_code": "", "description": "", "publisher": "", "year": "",
               "pages": None, "needs_ocr": None, "cover_member": None, "broken": False,
               "meta_title": "", "meta_author": ""}
        try:
            if ext == "epub":
                m = metadata.read_epub(full_path)
                rec.update(meta_title=metadata.clean_meta_value(m.get("title")),
                           meta_author=metadata.clean_meta_value(m.get("author")),
                           lang_code=m.get("language", ""), subjects=m.get("subjects", []),
                           description=m.get("description", ""), publisher=m.get("publisher", ""),
                           year=m.get("year", ""), cover_member=m.get("cover"))
            elif ext == "pdf":
                m = metadata.read_pdf(full_path)
                rec.update(meta_title=metadata.clean_meta_value(m.get("title")),
                           meta_author=metadata.clean_meta_value(m.get("author")),
                           pages=m.get("pages"), needs_ocr=m.get("needs_ocr"))
        except Exception as e:
            log.info("Okunamadı %s: %s", rel, e)
            rec["broken"] = True
        rec["title"], rec["author"] = choose_title_author(ext, title, author,
                                                          rec["meta_title"], rec["meta_author"], name)
        return rec

    # ---------- sorgulama ----------
    def books(self):
        """Aynı kitabın farklı formatlarını tek kayıtta birleştirir."""
        with self._lock:
            ver = self._version
            if self._books_cache[0] == ver:
                return self._books_cache[1]
            files = list(self.files.values())
        groups = {}
        for f in files:
            key = group_key(f["title"], f["author"]) or f["id"]
            g = groups.get(key)
            if g is None:
                g = groups[key] = {"id": f["id"], "title": f["title"], "author": f["author"],
                                   "formats": [], "subjects": [], "folders": f["folders"],
                                   "description": "", "year": "", "publisher": "",
                                   "added": 0, "lang_code": ""}
            g["formats"].append({k: f[k] for k in ("id", "ext", "size", "pages", "needs_ocr",
                                                   "broken", "path", "name")})
            g["subjects"] = g["subjects"] or f.get("subjects", [])
            g["description"] = g["description"] or f.get("description", "")
            g["year"] = g["year"] or f.get("year", "")
            g["publisher"] = g["publisher"] or f.get("publisher", "")
            g["lang_code"] = g["lang_code"] or f.get("lang_code", "")
            g["added"] = max(g["added"], f["mtime"])
        order = {"epub": 0, "pdf": 1}
        out = []
        for g in groups.values():
            g["formats"].sort(key=lambda x: (order.get(x["ext"], 9), x["broken"]))
            # kapak için EPUB'u, yoksa ilk PDF'i tercih et
            g["cover_id"] = next((x["id"] for x in g["formats"]
                                  if x["ext"] in ("epub", "pdf") and not x["broken"]), None)
            main, sub, _ = classify(g["title"], g["author"], g["subjects"], g["folders"])
            g["category"], g["subcategory"] = main, sub
            g["language"] = detect_language(f"{g['title']} {g['author']}", g["lang_code"])
            out.append(g)
        out.sort(key=lambda b: b["title"].casefold())
        with self._lock:
            self._books_cache = (ver, out)
        return out

    def get_file(self, fid):
        with self._lock:
            return self.files.get(fid)

    def abs_path(self, rec):
        # Yalnızca taramada bulunmuş (kayıtlı) dosyalar sunulur; URL'den yol alınmaz.
        return os.path.join(self.book_dir, rec["path"])

    def _drop_cover(self, fid):
        for suffix in (".jpg", ".jpg.none"):
            try:
                os.remove(os.path.join(self.cover_dir, fid + suffix))
            except OSError:
                pass

    def cover_path(self, fid):
        """Kapağı ilk istendiğinde üretir ve diske kaydeder."""
        dest = os.path.join(self.cover_dir, fid + ".jpg")
        miss = dest + ".none"
        if os.path.exists(dest):
            return dest
        if os.path.exists(miss):
            return None
        rec = self.get_file(fid)
        if not rec:
            return None
        tmp = f"{dest}.{threading.get_ident()}.tmp"
        ok = metadata.make_cover(rec["ext"], self.abs_path(rec), rec.get("cover_member"), tmp)
        if ok:
            os.replace(tmp, dest)
            return dest
        open(miss, "w").close()
        return None
