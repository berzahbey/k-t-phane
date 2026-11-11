"""Büyük ve taranmış PDF'ler için sunucu tarafında sayfa görüntüsü.

Taranmış (OCR'sız) kitaplar çoğu zaman yüzlerce MB olur; bazılarının iç dizini (xref) bozuktur
ve tarayıcıdaki pdf.js açabilmek için dosyanın büyük kısmını indirmek zorunda kalır (telefonda
dakikalar sürer). Bunun yerine sunucu sayfayı PDFium ile çizer ve yalnızca o sayfanın küçük
WebP görüntüsünü gönderir. Görüntüler /data/sayfalar altında önbelleğe alınır.
"""
import collections
import logging
import os
import re
import threading
import time
import unicodedata

log = logging.getLogger("ktphane.pdfpages")

WIDTHS = (480, 720, 960, 1280, 1600, 2000, 2400)
CACHE_LIMIT = int(os.environ.get("PAGE_CACHE_MB", "3000")) * 1024 * 1024
OPEN_DOCS = 4

_lock = threading.RLock()          # PDFium iş parçacığı güvenli değil: tek kilit
_docs = collections.OrderedDict()  # path -> (mtime, PdfDocument)
_info = {}                         # ckey -> bilgi
_texts = {}                        # ckey -> [sayfa metinleri]
_writes = 0


def _doc(path):
    import pypdfium2 as pdfium
    mt = os.path.getmtime(path)
    hit = _docs.get(path)
    if hit and hit[0] == mt:
        _docs.move_to_end(path)
        return hit[1]
    if hit:
        _close(path)
    doc = pdfium.PdfDocument(path)
    _docs[path] = (mt, doc)
    while len(_docs) > OPEN_DOCS:
        _close(next(iter(_docs)))
    return doc


def _close(path):
    _, doc = _docs.pop(path)
    try:
        doc.close()
    except Exception:
        pass


def forget(path):
    with _lock:
        if path in _docs:
            _close(path)


def info(path, ckey):
    if ckey in _info:
        return _info[ckey]
    with _lock:
        doc = _doc(path)
        n = len(doc)
        sizes = []
        for i in range(n):
            try:
                w, h = doc.get_page_size(i)
            except Exception:
                w, h = sizes[-1] if sizes else (595, 842)
            sizes.append([round(w, 1), round(h, 1)])
        toc = []
        try:
            for t in doc.get_toc(max_depth=6):
                if t.page_index is not None and t.title:
                    toc.append({"title": t.title.strip()[:300], "page": t.page_index + 1, "level": t.level + 1})
                if len(toc) >= 3000:
                    break
        except Exception:
            pass
    out = {"pages": n, "sizes": sizes, "outline": toc}
    _info[ckey] = out
    return out


def snap_width(w):
    for x in WIDTHS:
        if w <= x:
            return x
    return WIDTHS[-1]


def page_image(path, ckey, data_dir, n, width):
    """Sayfa n (1'den başlar) için WebP dosya yolu döndürür; yoksa çizip kaydeder."""
    global _writes
    width = snap_width(width)
    d = os.path.join(data_dir, "sayfalar", ckey)
    out = os.path.join(d, f"{n}-{width}.webp")
    if os.path.isfile(out):
        try:
            os.utime(out)  # son kullanım (önbellek temizliği için)
        except OSError:
            pass
        return out
    with _lock:
        doc = _doc(path)
        if not 1 <= n <= len(doc):
            return None
        page = doc[n - 1]
        try:
            pw = page.get_width() or 595
            scale = max(0.2, min(6.0, width / pw))
            img = page.render(scale=scale, may_draw_forms=False).to_pil()
        finally:
            page.close()
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    os.makedirs(d, exist_ok=True)
    tmp = out + ".tmp"
    img.save(tmp, "WEBP", quality=80, method=3)
    os.replace(tmp, out)
    _writes += 1
    if _writes % 200 == 1:
        threading.Thread(target=_trim, args=(os.path.join(data_dir, "sayfalar"),), daemon=True).start()
    return out


def _trim(root):
    """Önbellek sınırı aşılırsa en uzun süredir kullanılmayan sayfaları sil."""
    try:
        files = []
        for dp, _, fs in os.walk(root):
            for f in fs:
                p = os.path.join(dp, f)
                try:
                    st = os.stat(p)
                    files.append((st.st_mtime, st.st_size, p))
                except OSError:
                    pass
        total = sum(f[1] for f in files)
        if total <= CACHE_LIMIT:
            return
        files.sort()
        for _, size, p in files:
            if total <= CACHE_LIMIT * 0.8:
                break
            try:
                os.remove(p)
                total -= size
            except OSError:
                pass
        for dp, ds, fs in os.walk(root, topdown=False):
            if dp != root and not os.listdir(dp):
                try:
                    os.rmdir(dp)
                except OSError:
                    pass
    except Exception as e:
        log.info("sayfa önbelleği temizlenemedi: %s", e)


# ---------- arama ----------
_HARAKA = re.compile(r"[ً-ٰٟـ­‌‍]")
_ARABIC_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ی": "ي", "ک": "ك", "ۀ": "ه"})


def _norm_char(c):
    if _HARAKA.match(c):
        return ""
    c = c.translate(_ARABIC_MAP)
    c = "i" if c == "İ" else "ı" if c == "I" else c.lower()
    c = "".join(x for x in unicodedata.normalize("NFD", c) if not unicodedata.combining(x))
    return " " if c.isspace() else c


def norm_map(s):
    out, idx, prev_space = [], [], False
    for i, ch in enumerate(s):
        c = _norm_char(ch)
        if not c:
            continue
        if c == " ":
            if prev_space:
                continue
            prev_space = True
        else:
            prev_space = False
        for x in c:
            out.append(x)
            idx.append(i)
    return "".join(out), idx


def page_texts(path, ckey):
    if ckey in _texts:
        return _texts[ckey]
    texts = []
    with _lock:
        doc = _doc(path)
        for i in range(len(doc)):
            try:
                page = doc[i]
                tp = page.get_textpage()
                texts.append(tp.get_text_bounded() or "")
                tp.close()
                page.close()
            except Exception:
                texts.append("")
    if len(_texts) > 6:
        _texts.pop(next(iter(_texts)))
    _texts[ckey] = texts
    return texts


def search(path, ckey, q, limit=300):
    nq, _ = norm_map(q.strip())
    if len(nq) < 2:
        return {"results": [], "has_text": True}
    texts = page_texts(path, ckey)
    res, chars = [], 0
    for n, raw in enumerate(texts, 1):
        chars += len(raw.strip())
        t, idx = norm_map(raw)
        start = 0
        while len(res) < limit:
            k = t.find(nq, start)
            if k < 0:
                break
            s0, s1 = idx[k], idx[k + len(nq) - 1] + 1
            clean = lambda x: re.sub(r"\s+", " ", x)
            res.append({"page": n, "pre": clean(raw[max(0, s0 - 50):s0]), "hit": clean(raw[s0:s1]),
                        "post": clean(raw[s1:s1 + 60])})
            start = k + len(nq)
        if len(res) >= limit:
            break
    return {"results": res, "has_text": chars >= len(texts) * 20}
