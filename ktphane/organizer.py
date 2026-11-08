"""Kitapları klasör yapısına göre düzenler:

    <Dil>/<Konu>/EPUB/kitap.epub
    <Dil>/<Konu>/PDF/Metin/kitap.pdf
    <Dil>/<Konu>/PDF/OCR/kitap.pdf
    <Dil>/<Konu>/Diğer Formatlar/kitap.mobi
    Kontrol Edilecek/bozuk.epub

Her taşıma günlüğe yazılır; son düzenleme "Geri al" ile geri döndürülebilir.
Dosya asla silinmez; hedefte aynı adda başka dosya varsa "(2)" eklenir.
"""
import logging
import os
import re
import shutil
import time
import uuid

from .library import folder_name

log = logging.getLogger("ktphane.organizer")

CHECK_DIR = "Kontrol Edilecek"
JUNK_FILES = {".DS_Store", "Thumbs.db", "desktop.ini", ".localized"}
JUNK_DIRS = {"@eaDir", ".AppleDouble", "__MACOSX"}
MAX_JOURNAL = 20


def writable(lib):
    probe = os.path.join(lib.book_dir, ".ktphane-yazma-testi")
    try:
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def _format_dir(f):
    if f["broken"]:
        return None
    if f["ext"] == "epub":
        return "EPUB"
    if f["ext"] == "pdf":
        if f["needs_ocr"] is True:
            return os.path.join("PDF", "OCR")
        if f["needs_ocr"] is False:
            return os.path.join("PDF", "Metin")
        return os.path.join("PDF", "Diğer")  # şifreli vb.
    return "Diğer Formatlar"


SERIES_DIR = "Koleksiyonlar"


def safe_folder(name):
    """Mac/Windows paylaşımlarında sorun çıkaran karakterleri temizle."""
    name = re.sub(r'[\\/:*?"<>|]+', " - ", name or "").strip(" .-")
    return re.sub(r"\s+", " ", name)[:120] or "Adsız"


def target_for(book, f):
    sub = _format_dir(f)
    if sub is None:
        return os.path.join(CHECK_DIR, f["name"])
    base = os.path.join(folder_name(book["language"]), folder_name(book["category"]))
    if book.get("series"):
        # çok ciltli eserler: <Dil>/<Konu>/Koleksiyonlar/<Eser adı>/<dosya>
        return os.path.join(base, SERIES_DIR, safe_folder(book["series"]), f["name"])
    return os.path.join(base, sub, f["name"])


def _same_place(dst, cur):
    """'X.pdf' hedefi için zaten 'X (2).pdf' olarak yerleşmişse yeniden taşıma."""
    if dst == cur:
        return True
    if os.path.dirname(dst) != os.path.dirname(cur):
        return False
    stem, ext = os.path.splitext(os.path.basename(dst))
    return re.fullmatch(re.escape(stem) + r" \(\d+\)" + re.escape(ext), os.path.basename(cur)) is not None


def plan(lib):
    """[{id, from, to, book, override}] — yalnızca yeri değişecek dosyalar."""
    moves = []
    for b in lib.books():
        if b.get("pending"):
            continue
        for f in b["formats"]:
            dst = target_for(b, f)
            if not _same_place(dst, f["path"]):
                moves.append({"id": f["id"], "from": f["path"], "to": dst, "title": b["title"],
                              "override": b.get("folder_override") or {}})
    moves.sort(key=lambda m: m["to"].casefold())
    return moves


def _free_name(path):
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{stem} ({i}){ext}"):
        i += 1
    return f"{stem} ({i}){ext}"


def _cleanup_dirs(lib, start_dirs):
    """Taşıma sonrası boş kalan klasörleri (yalnızca sistem çöpü içerenler dahil) kaldırır."""
    root = os.path.realpath(lib.book_dir)
    for d in sorted(set(start_dirs), key=len, reverse=True):
        cur = d
        while True:
            real = os.path.realpath(cur)
            if real == root or not real.startswith(root + os.sep) or not os.path.isdir(cur):
                break
            try:
                entries = os.listdir(cur)
            except OSError:
                break
            if any(e not in JUNK_FILES and e not in JUNK_DIRS for e in entries):
                break
            try:
                for e in entries:
                    p = os.path.join(cur, e)
                    shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
                os.rmdir(cur)
            except OSError:
                break
            cur = os.path.dirname(cur)


def _move(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.rename(src, dst)
    except OSError:
        shutil.move(src, dst)  # farklı disk/bağlama noktası
    # macOS yan dosyası (._kitap.pdf) varsa birlikte taşı
    side = os.path.join(os.path.dirname(src), "._" + os.path.basename(src))
    if os.path.exists(side):
        try:
            os.rename(side, os.path.join(os.path.dirname(dst), "._" + os.path.basename(dst)))
        except OSError:
            pass


def apply_locked(lib, auto=False):
    """Tarama kilidi alınmışken çağrılır. Taşınan dosya sayısını döndürür."""
    moves = plan(lib)
    if not moves:
        return 0
    lib.org_status.update(running=True, done=0, total=len(moves))
    batch = {"id": uuid.uuid4().hex[:8], "time": int(time.time()), "auto": auto, "moves": []}
    errors, src_dirs = [], []
    placed = dict(lib.org.get("placed", {}))
    for i, m in enumerate(moves, 1):
        src = os.path.join(lib.book_dir, m["from"])
        dst = _free_name(os.path.join(lib.book_dir, m["to"]))
        try:
            if not os.path.exists(src):
                continue
            _move(src, dst)
            rel_dst = os.path.relpath(dst, lib.book_dir)
            batch["moves"].append([m["from"], rel_dst])
            placed.pop(m["from"], None)
            placed[rel_dst] = m["override"]
            src_dirs.append(os.path.dirname(src))
        except Exception as e:
            log.warning("Taşınamadı %s -> %s: %s", m["from"], m["to"], e)
            errors.append(f"{m['from']}: {e}")
        lib.org_status["done"] = i
    _cleanup_dirs(lib, src_dirs)
    with lib._lock:
        lib.org["placed"] = placed
        if batch["moves"]:
            lib.org["journal"] = (lib.org.get("journal", []) + [batch])[-MAX_JOURNAL:]
    lib.save_org()
    lib.org_status.update(running=False, last={"time": batch["time"], "moved": len(batch["moves"]),
                                              "errors": errors[:20], "auto": auto, "undo": False})
    log.info("Düzenleme: %d dosya taşındı, %d hata", len(batch["moves"]), len(errors))
    return len(batch["moves"])


def undo_locked(lib):
    journal = lib.org.get("journal", [])
    if not journal:
        return 0
    batch = journal[-1]
    lib.org_status.update(running=True, done=0, total=len(batch["moves"]))
    placed = dict(lib.org.get("placed", {}))
    back, errors, dst_dirs = 0, [], []
    for i, (src, dst) in enumerate(reversed(batch["moves"]), 1):
        a, b = os.path.join(lib.book_dir, dst), os.path.join(lib.book_dir, src)
        try:
            if os.path.exists(a) and not os.path.exists(b):
                _move(a, b)
                placed.pop(dst, None)
                dst_dirs.append(os.path.dirname(a))
                back += 1
        except Exception as e:
            errors.append(f"{dst}: {e}")
        lib.org_status["done"] = i
    _cleanup_dirs(lib, dst_dirs)
    with lib._lock:
        lib.org["placed"] = placed
        lib.org["journal"] = journal[:-1]
        lib.org["auto"] = False  # geri alınca hemen tekrar taşımasın
    lib.save_org()
    lib.org_status.update(running=False, last={"time": int(time.time()), "moved": back,
                                              "errors": errors[:20], "auto": False, "undo": True})
    return back


def run_in_background(lib, fn, **kw):
    """Tarama kilidiyle çalıştırır, ardından listeyi yeniler."""
    import threading
    if lib._scan_lock.locked():
        return False

    def job():
        if not lib._scan_lock.acquire(blocking=False):
            return
        try:
            lib.org_status["running"] = True
            if fn(lib, **kw):
                lib._scan_locked()
        except Exception as e:
            log.exception("Düzenleme hatası")
            lib.org_status["last"] = {"time": int(time.time()), "moved": 0, "errors": [str(e)]}
        finally:
            lib.org_status["running"] = False
            lib._scan_lock.release()
    threading.Thread(target=job, daemon=True).start()
    return True
