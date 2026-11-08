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

try:
    from zoneinfo import ZoneInfo
    _PACIFIC = ZoneInfo("America/Los_Angeles")
except Exception:  # saat dilimi verisi yoksa yaklaşık değer (PST)
    _PACIFIC = timezone(timedelta(hours=-8))

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
        self._daily_hits = 0
        self.usage = self._load_usage()
        self.settings_file = os.path.join(data_dir, "settings.json")
        self.key_source = "env" if self.key else None
        self._apply_settings(self._load_settings())

    # ---------- uygulama içinden girilen ayarlar (/data/settings.json) ----------
    def _load_settings(self):
        try:
            with open(self.settings_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _apply_settings(self, st):
        if st.get("gemini_api_key"):
            self.key, self.key_source = st["gemini_api_key"].strip(), "app"
        if st.get("gemini_model"):
            self.model = st["gemini_model"].strip()
        if st.get("gemini_daily_limit"):
            self.daily_limit = int(st["gemini_daily_limit"])

    def update_settings(self, key=None, model=None, daily_limit=None):
        st = self._load_settings()
        if key is not None:
            key = key.strip()
            if key:
                st["gemini_api_key"] = key
            else:  # silindi: ortam değişkenine geri dön
                st.pop("gemini_api_key", None)
                self.key = os.environ.get("GEMINI_API_KEY", "").strip()
                self.key_source = "env" if self.key else None
        if model:
            st["gemini_model"] = model.strip()
        if daily_limit:
            st["gemini_daily_limit"] = max(1, min(100000, int(daily_limit)))
        tmp = self.settings_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.settings_file)
        self._apply_settings(st)
        self.paused_until, self.last_error = 0, None

    def public_settings(self):
        k = self.key or ""
        hint = (k[:4] + "…" + k[-4:]) if len(k) > 10 else ("•••" if k else "")
        return {"has_key": bool(k), "key_hint": hint, "key_source": self.key_source,
                "model": self.model, "daily_limit": self.daily_limit, "rpm": self.rpm}

    def test(self):
        """Anahtarı küçük bir istekle dener. (ok, mesaj)"""
        if not self.key:
            return False, "Anahtar girilmemiş"
        body = {"contents": [{"role": "user", "parts": [{"text": "Sadece 'tamam' yaz."}]}],
                "generationConfig": {"maxOutputTokens": 10}}
        req = urllib.request.Request(API.format(model=self.model), method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "x-goog-api-key": self.key})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                json.load(r)
            self._count()
            self.paused_until, self.last_error = 0, None
            return True, f"Bağlantı başarılı ({self.model})"
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            m = re.search(r'"message":\s*"([^"]+)', detail)
            msg = m.group(1) if m else detail[:200]
            if e.code in (400, 401, 403) and re.search(r"API key|API_KEY", detail):
                return False, "API anahtarı geçersiz: " + msg
            if e.code == 404:
                return False, f"Model bulunamadı ({self.model}): " + msg
            if e.code == 429:
                return True, "Anahtar geçerli ama şu an kota dolu; uygulama bekleyip devam edecek"
            return False, f"Hata {e.code}: {msg}"
        except Exception as e:
            return False, f"Bağlantı kurulamadı: {e}"

    @property
    def enabled(self):
        return bool(self.key)

    # ---------- günlük sayaç ----------
    @staticmethod
    def _today():
        # Google günlük kotayı Pasifik saatiyle gece yarısı sıfırlar; sayaç da o güne göre tutulur
        return datetime.now(_PACIFIC).strftime("%Y-%m-%d")

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
        # Bir sonraki Pasifik gece yarısı + 5 dk (Avrupa saatiyle sabah 09:05 civarı)
        now = datetime.now(_PACIFIC)
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
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
                self._daily_hits = 0
                return self._parse(data)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "ignore")
                if e.code == 429:
                    # Yalnızca Google'ın bildirdiği kota kimliği "PerDay" ise günlük kota dolmuştur.
                    # Dakikalık sınır (aynı anahtarı kullanan başka uygulamalar da olabilir) için
                    # kısa bekleyip devam edilir; günlük şüphesi iki kez üst üste doğrulanmadan
                    # bütün gün beklenmez.
                    quota_ids = re.findall(r'"quotaId":\s*"([^"]+)"', detail)
                    daily = any("PerDay" in q for q in quota_ids) and not any("PerMinute" in q for q in quota_ids)
                    if daily:
                        self._daily_hits += 1
                        if self._daily_hits >= 2:
                            self._daily_hits = 0
                            self._pause_until_tomorrow("Gemini ücretsiz günlük kotası doldu")
                            raise QuotaExhausted()
                        self.last_error = "Günlük kota dolmuş olabilir; 15 dk sonra tekrar denenecek"
                        self.paused_until = int(time.time()) + 900
                        raise QuotaExhausted()
                    m = re.search(r'"retryDelay":\s*"(\d+)', detail)
                    self.last_error = "Dakikalık sınır; kısa süre bekleniyor"
                    time.sleep(int(m.group(1)) + 2 if m else 30 * (attempt + 1))
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
