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


def _ckey(rec):
    """Kapak anahtarı dosya içeriğine bağlı: dosya taşınsa da kapak yeniden üretilmez."""
    raw = f"{rec['name']}|{rec['size']}|{rec['mtime']}"
    return hashlib.sha1(raw.encode("utf-8", "surrogateescape")).hexdigest()[:16]


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


LANG_LABELS = ["Türkçe", "Osmanlıca", "Osmanlıca / Farsça", "Arapça", "Farsça", "English",
               "Nederlands", "Deutsch", "Français"]


def folder_name(label):
    """Klasör adına uygun hale getir ('Osmanlıca / Farsça' -> 'Osmanlıca - Farsça')."""
    return re.sub(r"\s*/\s*", " - ", label).strip()


_LANG_BY_FOLDER = {fold(folder_name(l)): l for l in LANG_LABELS}
_LANG_BY_FOLDER.update({"turkish": "Türkçe", "arabic": "Arapça", "ingilizce": "English",
                        "hollandaca": "Nederlands", "almanca": "Deutsch", "farsi": "Farsça",
                        "ottoman": "Osmanlıca", "dutch": "Nederlands", "german": "Deutsch"})


def folder_override(folders):
    """Kullanıcının elle oluşturduğu 'Dil' ve 'Ana kategori' klasörlerini tanır."""
    from .categories import category_tree
    cats = {fold(c): c for c in category_tree()}
    out = {}
    for f in folders:
        k = fold(f).strip()
        if "lang" not in out and k in _LANG_BY_FOLDER:
            out["lang"] = _LANG_BY_FOLDER[k]
        elif "cat" not in out and k in cats:
            out["cat"] = cats[k]
    return out


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
        self.org_file = os.path.join(self.data_dir, "organizer.json")
        self.org = {"auto": False, "placed": {}, "journal": []}
        self.org_status = {"running": False, "last": None}
        self._load_cache()
        self._load_org()

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

    def _load_org(self):
        try:
            with open(self.org_file, encoding="utf-8") as f:
                self.org.update(json.load(f))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("organizer.json okunamadı: %s", e)

    def save_org(self):
        tmp = self.org_file + ".tmp"
        with self._lock:
            payload = json.dumps(self.org, ensure_ascii=False)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, self.org_file)

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
        try:
            self._scan_locked(full=full)
            if self.org.get("auto") and not self.status.get("error"):
                from . import organizer
                if organizer.writable(self):
                    moved = organizer.apply_locked(self, auto=True)
                    if moved:
                        self._scan_locked()
        finally:
            self._scan_lock.release()

    def _scan_locked(self, full=False):
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
            # taşınmış dosyalar yeniden okunmasın: ad+boyut+tarih eşleşmesi
            by_content = {(r["name"], r["size"], r["mtime"]): r for r in old.values()}
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
                moved = by_content.get((os.path.basename(rel), st.st_size, int(st.st_mtime)))
                if prev and prev.get("mtime") == int(st.st_mtime) and prev.get("size") == st.st_size:
                    new[fid] = prev
                elif moved:
                    new[fid] = {**moved, "id": fid, "path": rel,
                                "folders": [p for p in os.path.dirname(rel).split(os.sep) if p]}
                else:
                    new[fid] = self._analyse(fid, rel, full_path, ext, st)
                    dirty += 1
                    if dirty % 50 == 0:  # büyük arşivde kademeli görünür olsun
                        with self._lock:
                            self.files = {**self.files, **new}
                            self._version += 1
                self.status["done"] = i
                self.status["current"] = rel
            with self._lock:
                removed = set(self.files) - set(new)
                self.files = new
                self._version += 1
            alive = {_ckey(r) for r in new.values()}
            for fid in removed:
                old_rec = old.get(fid) or {}
                if old_rec and _ckey(old_rec) not in alive:
                    self._drop_cover(_ckey(old_rec))
            # artık var olmayan yerleşim kayıtlarını temizle (elle taşınan dosyalar)
            paths = {r["path"] for r in new.values()}
            placed = self.org.get("placed", {})
            if any(p not in paths for p in placed):
                self.org["placed"] = {p: v for p, v in placed.items() if p in paths}
                try:
                    self.save_org()
                except OSError:
                    pass
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
            # PDF'ler taramada AÇILMAZ (bazıları dakikalarca sürer); sayfa sayısı, OCR durumu ve
            # içerik arka plandaki analizde ayrı süreçte, zaman aşımıyla okunur.
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
        from .series import split_volume
        for f in files:
            # EPUB içindeki başlıkta cilt numarası yoksa ama dosya adında varsa ("Mesnevi Cilt 2.epub"
            # içinde başlık sadece "Mesnevi") ciltler tek kitapta birleşmesin
            if split_volume(f["title"])[0] is None:
                fn_title, _ = parse_filename(f["name"])
                _, n = split_volume(fn_title)
                if n:
                    f = {**f, "title": f"{f['title']} {n}"}
            key = group_key(f["title"], f["author"]) or f["id"]
            g = groups.get(key)
            if g is None:
                g = groups[key] = {"id": f["id"], "title": f["title"], "author": f["author"],
                                   "formats": [], "subjects": [], "folders": f["folders"],
                                   "description": "", "year": "", "publisher": "",
                                   "added": 0, "lang_code": ""}
            fmt = {k: f[k] for k in ("id", "ext", "size", "pages", "needs_ocr", "broken", "path", "name")}
            fmt["ckey"] = _ckey(f)
            a = self.enricher.get(fmt["ckey"]) if getattr(self, "enricher", None) else None
            if a:
                if fmt["needs_ocr"] is None and "needs_ocr" in a:
                    fmt["needs_ocr"] = a["needs_ocr"]
                fmt["pages"] = fmt["pages"] or a.get("pages")
                if a.get("error") and f["ext"] == "pdf":
                    fmt["broken"] = fmt["broken"] or "zaman aşımı" not in a["error"]
                if f["ext"] == "pdf" and a.get("meta_author") and not g["author"]:
                    g["author"] = tr_title(a["meta_author"])
                if f["ext"] == "pdf" and a.get("meta_title") and _looks_junk(g["title"]):
                    g["title"] = tr_title(a["meta_title"])
            g["formats"].append(fmt)
            g["subjects"] = g["subjects"] or f.get("subjects", [])
            g["description"] = g["description"] or f.get("description", "")
            g["year"] = g["year"] or f.get("year", "")
            g["publisher"] = g["publisher"] or f.get("publisher", "")
            g["lang_code"] = g["lang_code"] or f.get("lang_code", "")
            g["added"] = max(g["added"], f["mtime"])
        order = {"epub": 0, "pdf": 1}
        placed = self.org.get("placed", {})
        out = []
        for g in groups.values():
            g["formats"].sort(key=lambda x: (order.get(x["ext"], 9), x["broken"]))
            # kapak için EPUB'u, yoksa ilk PDF'i tercih et
            g["cover_id"] = next((x["id"] for x in g["formats"]
                                  if x["ext"] in ("epub", "pdf") and not x["broken"]), None)
            # Elle konulmuş klasör (Türkçe/Din gibi) her zaman kazanır. Düzenleyicinin kendi
            # oluşturduğu klasörler ise sınıflandırmayı etkilemez (kendini besleme olmasın).
            override, user_folders = {}, []
            for x in g["formats"]:
                if x["path"] in placed:
                    o = placed[x["path"]] or {}
                else:
                    folders = [p for p in os.path.dirname(x["path"]).split(os.sep) if p]
                    user_folders += folders
                    o = folder_override(folders)
                for k, v in o.items():
                    override.setdefault(k, v)
            self._decide(g, override, user_folders)
            out.append(g)
        from .series import assign as assign_series
        assign_series(out)
        out.sort(key=lambda b: b["title"].casefold())
        with self._lock:
            self._books_cache = (ver, out)
        return out

    AI_MIN_CONF = 0.55

    def _decide(self, g, override, user_folders):
        """Kategori ve dil kararı. Öncelik: kullanıcının klasörü > Gemini > içerik > ad/etiket."""
        enr = getattr(self, "enricher", None)
        analyses = [enr.get(x["ckey"]) for x in g["formats"]] if enr else []
        analyses = [a for a in analyses if a]
        ai = next((a["ai"] for a in analyses if a.get("ai") and not a["ai"].get("failed")), None)
        body = max(analyses, key=lambda a: a.get("chars", 0), default=None)

        main, sub, rscore = classify(g["title"], g["author"], g["subjects"], user_folders)
        why = {"source": "rules", "conf": min(1.0, rscore / 9),
               "text": "Dosya adı, etiket ve klasör adlarından" if rscore else "Belirgin bir ipucu bulunamadı"}
        kw = (body or {}).get("kw")
        if kw and kw["score"] >= 6 and kw["ratio"] >= 0.3 and (kw["score"] / 3 >= rscore or main == "Diğer"):
            main, sub = kw["cat"], kw["sub"]
            why = {"source": "content", "conf": min(0.9, kw["ratio"]),
                   "text": f"Kitabın içeriğindeki terimlerden (%{int(kw['ratio'] * 100)} {kw['cat']})"}
        if ai and ai["conf"] >= self.AI_MIN_CONF and ai["cat"] != "Diğer":
            main, sub = ai["cat"], ai["sub"]
            why = {"source": "ai", "conf": ai["conf"], "text": ai.get("reason") or "Gemini"}
        if override.get("cat"):
            if override["cat"] != main:
                main, sub = override["cat"], "Genel"
            why = {"source": "folder", "conf": 1.0, "text": "Senin klasör seçimin"}
        g["category"], g["subcategory"] = main, sub

        lang = override.get("lang")
        if not lang and ai and ai.get("lang") and ai["lang"] != "Diğer" and ai["conf"] >= self.AI_MIN_CONF:
            lang = ai["lang"]
        if not lang and body and body.get("lang") and body.get("lang_conf", 0) >= 0.45:
            lang = body["lang"]
        g["language"] = lang or detect_language(f"{g['title']} {g['author']}", g["lang_code"])

        if ai:  # yapay zeka başlık/yazarı yalnızca eksik veya anlamsız olanı düzeltir
            if not g["author"] and ai.get("author"):
                g["author"] = ai["author"]
            if _looks_junk(g["title"]) and ai.get("title"):
                g["title"] = ai["title"]
        g["override"] = override
        g["why"] = why
        g["ocr_text"] = any(a.get("ocr") for a in analyses)
        # Düzenleyici, analizi bitmemiş kitabı taşımaz (iki kez taşınmasın)
        pending = False
        if enr:
            readable = [x for x in g["formats"] if not x["broken"] and x["ext"] in ("epub", "pdf", "txt")]
            if any(not enr.get(x["ckey"]) for x in readable):
                pending = True
            elif readable and not override.get("cat") and not ai and enr.ai.available():
                tried = any((a.get("ai") or {}).get("failed") for a in analyses)
                pending = not tried
        g["pending"] = pending

    def get_file(self, fid):
        with self._lock:
            return self.files.get(fid)

    def abs_path(self, rec):
        # Yalnızca taramada bulunmuş (kayıtlı) dosyalar sunulur; URL'den yol alınmaz.
        return os.path.join(self.book_dir, rec["path"])

    def _drop_cover(self, key):
        for suffix in (".jpg", ".jpg.none"):
            try:
                os.remove(os.path.join(self.cover_dir, key + suffix))
            except OSError:
                pass

    def cover_path(self, fid):
        """Kapağı ilk istendiğinde üretir ve diske kaydeder."""
        rec = self.get_file(fid)
        if not rec:
            return None
        dest = os.path.join(self.cover_dir, _ckey(rec) + ".jpg")
        miss = dest + ".none"
        if os.path.exists(dest):
            return dest
        if os.path.exists(miss):
            return None
        tmp = f"{dest}.{threading.get_ident()}.tmp"
        ok = metadata.make_cover(rec["ext"], self.abs_path(rec), rec.get("cover_member"), tmp)
        if ok:
            os.replace(tmp, dest)
            return dest
        open(miss, "w").close()
        return None
