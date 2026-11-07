"""Arka planda kitapların içeriğini okur (1. aşama) ve varsa Gemini ile sınıflandırır (2. aşama).

Sonuçlar dosya içeriğine bağlı anahtarla (_ckey) /data/analysis.json'a yazılır; dosya taşınsa
veya tam tarama yapılsa bile tekrar hesaplanmaz.
"""
import json
import logging
import os
import threading
import time

from . import content
from .ai import Gemini, QuotaExhausted
from .categories import classify_text

log = logging.getLogger("ktphane.enricher")
ANALYSIS_VERSION = 1


class Enricher:
    def __init__(self, lib):
        self.lib = lib
        self.file = os.path.join(lib.data_dir, "analysis.json")
        self.data = self._load()
        self.ai = Gemini(lib.data_dir)
        self.status = {"running": False, "done": 0, "pending": 0, "ai_pending": 0, "current": None}
        self._wake = threading.Event()
        self._dirty = 0

    def _load(self):
        try:
            with open(self.file, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as e:
            log.warning("analysis.json okunamadı: %s", e)
            return {}

    def save(self):
        tmp = self.file + ".tmp"
        with self.lib._lock:
            payload = json.dumps(self.data, ensure_ascii=False)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, self.file)

    def get(self, ckey):
        return self.data.get(ckey)

    def wake(self):
        self._wake.set()

    def start(self):
        threading.Thread(target=self._loop, name="enricher", daemon=True).start()

    # ---------- iş listesi ----------
    def _todo(self):
        """(aşama1 bekleyen dosyalar, yapay zeka bekleyen kitaplar)"""
        from .library import _ckey
        stage1, stage2 = [], []
        with self.lib._lock:
            files = list(self.lib.files.values())
        for f in files:
            a = self.data.get(_ckey(f))
            if f["broken"] or f["ext"] not in ("epub", "pdf", "txt"):
                continue
            if not a or a.get("v") != ANALYSIS_VERSION:
                stage1.append(f)
        stage1.sort(key=lambda f: (f.get("needs_ocr") is True, f["size"]))  # OCR'lılar en sona
        if self.ai.enabled:
            for b in self.lib.books():
                if b.get("override", {}).get("cat"):
                    continue  # kullanıcı klasörü belirlemiş, kota harcama
                keys = [x["ckey"] for x in b["formats"] if x.get("ckey")]
                ais = [(self.data.get(k) or {}).get("ai") for k in keys]
                if any(x and not x.get("failed") for x in ais):
                    continue
                if any(x and x.get("failed") and x["t"] > time.time() - 3 * 86400 for x in ais):
                    continue  # yakın zamanda denendi, sonra tekrar denenecek
                primary = next((x for x in b["formats"] if not x["broken"] and x["ext"] in ("epub", "pdf", "txt")), None)
                if primary and self.data.get(primary["ckey"]):
                    stage2.append((b, primary))
            # en belirsiz kitaplar önce
            stage2.sort(key=lambda bp: bp[0].get("why", {}).get("conf", 0))
        return stage1, stage2

    def _loop(self):
        time.sleep(5)
        while True:
            try:
                worked = self._step()
            except Exception:
                log.exception("Analiz hatası")
                worked = False
            if not worked:
                self.status.update(running=False, current=None)
                self._flush(force=True)
                self._wake.wait(timeout=600)
                self._wake.clear()

    def _step(self):
        if self.lib.status.get("scanning"):
            time.sleep(3)
            return True
        stage1, stage2 = self._todo()
        self.status.update(pending=len(stage1), ai_pending=len(stage2))
        if stage1:
            self._stage1(stage1[0])
            return True
        if stage2 and self.ai.available():
            b, primary = stage2[0]
            self._stage2(b, primary)
            return True
        return False

    def _stage1(self, f):
        from .library import _ckey
        self.status.update(running=True, current=f["name"])
        path = self.lib.abs_path(f)
        entry = {"v": ANALYSIS_VERSION, "t": int(time.time())}
        try:
            ex = self._run_worker(path, f["ext"])
            if ex.get("error"):
                raise RuntimeError(ex["error"])
            text = ex.get("text") or ""
            lang, lconf = content.detect_text_language(text)
            main, sub, score, ratio = classify_text(text, ex.get("toc") or [])
            entry.update(lang=lang, lang_conf=lconf, ocr=ex.get("ocr"), chars=len(text),
                         kw={"cat": main, "sub": sub, "score": score, "ratio": ratio},
                         toc=(ex.get("toc") or [])[:30], sample=text[:3500])
            for k in ("pages", "needs_ocr", "encrypted"):
                if k in ex:
                    entry[k] = ex[k]
            from .metadata import clean_meta_value
            for k in ("meta_title", "meta_author"):
                if ex.get(k):
                    entry[k] = clean_meta_value(ex[k])
        except Exception as e:
            log.info("İçerik okunamadı %s: %s", f["path"], e)
            entry.update(error=str(e)[:200])
        key = _ckey(f)
        with self.lib._lock:
            old = self.data.get(key) or {}
            if old.get("ai"):
                entry["ai"] = old["ai"]
            self.data[key] = entry
        self.status["done"] += 1
        self._flush()

    WORKER_TIMEOUT = int(os.environ.get("ANALYSIS_TIMEOUT", "300"))

    def _run_worker(self, path, ext):
        """İçeriği ayrı süreçte oku; takılırsa öldür."""
        import subprocess
        import sys
        try:
            r = subprocess.run(["nice", "-n", "10", sys.executable, "-m", "ktphane.worker", path, ext],
                               capture_output=True, text=True, timeout=self.WORKER_TIMEOUT,
                               cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        except subprocess.TimeoutExpired:
            return {"error": f"zaman aşımı ({self.WORKER_TIMEOUT} sn) — dosya çok büyük veya bozuk olabilir"}
        try:
            return json.loads(r.stdout or "{}") or {"error": (r.stderr or "boş çıktı")[-200:]}
        except ValueError:
            return {"error": (r.stderr or r.stdout or "okunamadı")[-200:]}

    def _stage2(self, b, primary):
        self.status.update(running=True, current="Gemini: " + b["title"])
        a = self.data.get(primary["ckey"]) or {}
        info = {"name": primary["name"], "folders": b.get("folders"), "title": b["title"],
                "author": b["author"], "subjects": b.get("subjects"), "toc": a.get("toc"),
                "text": a.get("sample")}
        try:
            res = self.ai.classify(info)
        except QuotaExhausted:
            # kota bitti: bekleyen kitaplar şimdilik içerik/kurallara göre yerleşebilsin
            self._dirty = max(self._dirty, 1)
            self._flush(force=True)
            return
        with self.lib._lock:
            for x in b["formats"]:
                k = x.get("ckey")
                if k and k in self.data:
                    self.data[k]["ai"] = res or {"failed": True, "t": int(time.time())}
        self.status["done"] += 1
        self._flush()

    def _flush(self, force=False):
        if force and not self._dirty:
            return
        if not force:
            self._dirty += 1
        if force or self._dirty >= 5:
            if self._dirty:
                with self.lib._lock:
                    self.lib._version += 1
                try:
                    self.save()
                except OSError as e:
                    log.warning("analysis.json yazılamadı: %s", e)
                self._dirty = 0
                if self.lib.org.get("auto"):
                    from . import organizer
                    if organizer.writable(self.lib):
                        organizer.run_in_background(self.lib, organizer.apply_locked, auto=True)
