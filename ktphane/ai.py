"""Gemini (ücretsiz katman) ile kitap sınıflandırma.

- Yalnızca GEMINI_API_KEY tanımlıysa çalışır; yoksa uygulama kurallarla devam eder.
- Ücretsiz katman sınırlarına uyar: dakikalık hız (GEMINI_RPM) ve günlük üst sınır
  (GEMINI_DAILY_LIMIT). 429 gelirse bekler; günlük kota bitince ertesi güne kadar durur.
- Anahtar faturalandırma açık bir projeye aitse bile günlük üst sınır aşılmaz.
"""
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from .categories import CATEGORIES
from .library import LANG_LABELS

log = logging.getLogger("ktphane.ai")

API = os.environ.get("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta") + "/models/{model}:generateContent"
OTHER = "Diğer › Sınıflandırılmamış"
CHOICES = [f"{m} › {s}" for m, s, _ in CATEGORIES] + [OTHER]
LANGS = [l for l in LANG_LABELS if "/" not in l] + ["Diğer"]

PROMPT = """Sen bir kütüphanecisin. Aşağıdaki kitabı, verilen kategori listesinden EN UYGUN tek
kategoriye yerleştir. Karar verirken dosya adından çok kitabın İÇERİK örneğine ve içindekilere bak.
Dini, felsefi, tarihi ve Osmanlıca/Arapça eserlerde ince ayrımlara dikkat et (ör. tasavvuf ile kelam,
siyer ile İslam tarihi, sosyoloji ile siyaset). Dil, kitabın METNİNİN dilidir (Arap harfli Türkçe =
Osmanlıca). Başlık ve yazarı kitabın kendi dilinde, düzgün yazımla ver; emin değilsen boş bırak.
"guven" 0 ile 1 arasında, ne kadar emin olduğundur. "neden" en fazla 15 kelimelik Türkçe açıklama.

Dosya adı: {name}
Klasör: {folders}
Mevcut bilgi — başlık: {title} | yazar: {author} | konu etiketleri: {subjects}
İçindekiler: {toc}

İçerik örneği:
\"\"\"{text}\"\"\"
"""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "kategori": {"type": "STRING", "enum": CHOICES},
        "dil": {"type": "STRING", "enum": LANGS},
        "baslik": {"type": "STRING"},
        "yazar": {"type": "STRING"},
        "guven": {"type": "NUMBER"},
        "neden": {"type": "STRING"},
    },
    "required": ["kategori", "dil", "guven", "neden"],
}


class QuotaExhausted(Exception):
    pass


class Gemini:
    def __init__(self, data_dir):
        self.key = os.environ.get("GEMINI_API_KEY", "").strip()
        self.model = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest").strip()
        self.rpm = max(1, int(os.environ.get("GEMINI_RPM", "8")))
        self.daily_limit = int(os.environ.get("GEMINI_DAILY_LIMIT", "500"))
        self.usage_file = os.path.join(data_dir, "ai-usage.json")
        self._lock = threading.Lock()
        self._last = 0.0
        self.paused_until = 0
        self.last_error = None
        self.usage = self._load_usage()

    @property
    def enabled(self):
        return bool(self.key)

    # ---------- günlük sayaç ----------
    @staticmethod
    def _today():
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load_usage(self):
        try:
            with open(self.usage_file) as f:
                u = json.load(f)
            if u.get("day") == self._today():
                return u
        except Exception:
            pass
        return {"day": self._today(), "count": 0}

    def _count(self):
        if self.usage["day"] != self._today():
            self.usage = {"day": self._today(), "count": 0}
        self.usage["count"] += 1
        try:
            with open(self.usage_file, "w") as f:
                json.dump(self.usage, f)
        except OSError:
            pass

    def _pause_until_tomorrow(self, reason):
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=0, minute=5, second=0)
        self.paused_until = int(tomorrow.timestamp())
        self.last_error = reason
        log.info("Gemini duraklatıldı (%s), devam: %s", reason, tomorrow.isoformat())

    def status(self):
        if self.usage["day"] != self._today():
            self.usage = {"day": self._today(), "count": 0}
        return {"enabled": self.enabled, "model": self.model, "today": self.usage["count"],
                "daily_limit": self.daily_limit,
                "paused_until": self.paused_until if self.paused_until > time.time() else None,
                "error": self.last_error}

    def available(self):
        if not self.enabled or self.paused_until > time.time():
            return False
        if self.usage["day"] == self._today() and self.usage["count"] >= self.daily_limit:
            self._pause_until_tomorrow("günlük üst sınıra ulaşıldı")
            return False
        return True

    # ---------- istek ----------
    def classify(self, info):
        """info: name, folders, title, author, subjects, toc, text -> sonuç dict veya None."""
        if not self.available():
            raise QuotaExhausted()
        prompt = PROMPT.format(
            name=info.get("name", ""), folders=" / ".join(info.get("folders") or []) or "-",
            title=info.get("title") or "-", author=info.get("author") or "-",
            subjects=", ".join(info.get("subjects") or []) or "-",
            toc=" | ".join((info.get("toc") or [])[:40]) or "-",
            text=(info.get("text") or "")[:5000] or "(metin çıkarılamadı)")
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json",
                                     "responseSchema": SCHEMA, "maxOutputTokens": 400}}
        for attempt in range(4):
            with self._lock:  # dakikalık hız sınırı
                wait = self._last + 60.0 / self.rpm - time.time()
                if wait > 0:
                    time.sleep(wait)
                self._last = time.time()
            req = urllib.request.Request(API.format(model=self.model), method="POST",
                                         data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "x-goog-api-key": self.key})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.load(r)
                self._count()
                self.last_error = None
                return self._parse(data)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "ignore")
                if e.code == 429:
                    if re.search(r"PerDay|per day|daily", detail, re.I):
                        self._pause_until_tomorrow("Gemini ücretsiz günlük kotası doldu")
                        raise QuotaExhausted()
                    m = re.search(r'"retryDelay":\s*"(\d+)', detail)
                    time.sleep(int(m.group(1)) + 1 if m else 30 * (attempt + 1))
                    continue
                if e.code in (401, 403):
                    self.last_error = "API anahtarı geçersiz veya yetkisiz"
                    self.paused_until = int(time.time()) + 3600
                    raise QuotaExhausted()
                if e.code == 404:
                    self.last_error = f"Model bulunamadı: {self.model} (GEMINI_MODEL ayarını kontrol edin)"
                    self.paused_until = int(time.time()) + 3600
                    raise QuotaExhausted()
                if e.code >= 500:
                    time.sleep(10 * (attempt + 1))
                    continue
                self.last_error = f"Gemini hatası {e.code}"
                log.warning("Gemini %s: %s", e.code, detail[:300])
                return None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                self.last_error = f"Bağlantı hatası: {e}"
                time.sleep(15 * (attempt + 1))
        return None

    @staticmethod
    def _parse(data):
        try:
            txt = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
            out = json.loads(txt)
        except Exception:
            log.info("Gemini cevabı okunamadı: %s", str(data)[:300])
            return None
        cat = out.get("kategori", OTHER)
        main, _, sub = cat.partition(" › ")
        try:
            conf = max(0.0, min(1.0, float(out.get("guven", 0))))
        except (TypeError, ValueError):
            conf = 0.0
        return {"cat": main, "sub": sub or "Genel", "lang": out.get("dil") or None,
                "title": (out.get("baslik") or "").strip()[:200],
                "author": (out.get("yazar") or "").strip()[:120],
                "conf": round(conf, 2), "reason": (out.get("neden") or "").strip()[:200],
                "t": int(time.time())}
