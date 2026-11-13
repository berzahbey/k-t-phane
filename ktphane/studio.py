"""Dedplay Studio'ya gönderme (tek kitap veya toplu).

Kitaplık ile Studio aynı kitap klasörünü görür (Kitaplık'ta /books, Studio'da /kaynak; ikisi de
/media/ZimaOS-HD/Kitap). Bu yüzden kitap kopyalanmaz: Studio'ya yalnızca klasördeki göreli yolu
söylenir (POST /api/kitaplar/sunucudan). Studio dosyayı bulamazsa (klasörler farklıysa) dosya
yüklenir (POST /api/kitaplar/yukle).

- Her kitaptan tek dosya gider: EPUB > metin PDF > taranmış PDF > DOCX > TXT (Studio diğer
  formatları kabul etmez: MOBI, AZW3, DJVU… "desteklenmiyor" diye atlanır).
- Studio'da zaten varsa (GET /api/arsiv-kontrol) atlanır; "yeniden gönder" seçilirse gönderilir.
- Gönderimler sırayla, arka planda yapılır; sonuçlar /data/studyo-gonderilenler.json'a yazılır.
"""
import http.client
import json
import logging
import os
import socket
import struct
import threading
import time
import uuid
from collections import deque
from urllib.parse import quote, urlencode, urlsplit

log = logging.getLogger("ktphane.studio")

STUDIO_EXTS = ("epub", "pdf", "docx", "txt")
PORT = int(os.environ.get("STUDIO_PORT", "8070"))
KEEP = 20000  # kayıt dosyasında tutulacak en fazla kitap


def _gateway():
    """Konteynerin varsayılan ağ geçidi = sunucunun kendisi (Studio'nun portu orada açık)."""
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                p = line.split()
                if len(p) > 2 and p[1] == "00000000" and int(p[3], 16) & 2:
                    return socket.inet_ntoa(struct.pack("<L", int(p[2], 16)))
    except Exception:
        pass
    return None


def _best(formats):
    """Kitabın Studio'ya gidecek en iyi dosyası (yoksa None)."""
    ok = [f for f in formats if f.get("ext") in STUDIO_EXTS and not f.get("broken")]
    if not ok:
        return None

    def rank(f):
        e = f["ext"]
        if e == "epub":
            return 0
        if e == "pdf":
            return 1 if f.get("needs_ocr") is False else 2
        return 3 if e == "docx" else 4
    return sorted(ok, key=lambda f: (rank(f), -(f.get("size") or 0)))[0]


class StudioError(Exception):
    pass


class Studio:
    def __init__(self, lib):
        self.lib = lib
        self.file = os.path.join(lib.data_dir, "studyo-gonderilenler.json")
        self._lock = threading.Lock()
        d = lib._read_json(self.file, {})
        self.url = d.get("url") or ""          # elle girilen adres (boşsa otomatik bulunur)
        self.sent = d.get("sent") or {}        # ckey -> {t, title, path, status, msg, kimlik}
        self._found = None                     # otomatik bulunan adres
        self.queue = deque()
        self.status = {"running": False, "total": 0, "done": 0, "sent": 0, "skipped": 0, "failed": 0,
                       "current": None, "error": None, "finished": None}
        self._thread = None

    # ---------- kalıcılık ----------
    def _save(self):
        with self._lock:
            if len(self.sent) > KEEP:
                for k in sorted(self.sent, key=lambda k: self.sent[k].get("t", 0))[:len(self.sent) - KEEP]:
                    self.sent.pop(k, None)
            data = {"url": self.url, "sent": dict(self.sent)}
        self.lib.write_json(self.file, data)

    def set_url(self, url):
        url = (url or "").strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            url = "http://" + url
        self.url = url
        self._found = None
        self._save()

    # ---------- bağlantı ----------
    def candidates(self):
        out = []
        for u in (self.url, os.environ.get("STUDIO_URL", "").rstrip("/"), self._found,
                  f"http://host.docker.internal:{PORT}",
                  f"http://{_gateway()}:{PORT}" if _gateway() else None,
                  f"http://172.17.0.1:{PORT}"):
            if u and u not in out:
                out.append(u)
        return out

    def _req(self, base, method, path, body=None, headers=None, timeout=20):
        u = urlsplit(base)
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        conn = cls(u.hostname, u.port or (443 if u.scheme == "https" else 80), timeout=timeout)
        try:
            conn.request(method, (u.path or "") + path, body=body, headers=headers or {})
            r = conn.getresponse()
            raw = r.read()
        finally:
            conn.close()
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            data = {"detail": raw[:200].decode("utf-8", "replace")}
        return r.status, data

    def _probe(self, base):
        try:
            st, d = self._req(base, "GET", "/api/kaynak", timeout=4)
            # 200: klasör bağlı; 404: Studio var ama kitap klasörü bağlı değil (yükleme kullanılır)
            return st in (200, 404) and isinstance(d, dict) and ("klasor" in d or "detail" in d)
        except Exception:
            return False

    def base(self, force=False):
        if self.url and not force:
            return self.url
        if self._found and not force:
            return self._found
        for c in self.candidates():
            if self._probe(c):
                self._found = c
                return c
        raise StudioError("Dedplay Studio'ya ulaşılamadı. ⚙ Ayarlar'dan Studio adresini girin "
                          f"(örn. http://192.168.1.2:{PORT}).")

    def check(self):
        try:
            b = self.url or self.base(force=True)
            if not self._probe(b):
                return {"ok": False, "url": b, "message": f"{b} adresinde Dedplay Studio yanıt vermiyor."}
            st, d = self._req(b, "GET", "/api/kaynak", timeout=6)
            shared = st == 200
            return {"ok": True, "url": b, "shared": shared,
                    "message": f"Bağlandı: {b}" + ("" if shared else
                               " — Studio kitap klasörünü görmüyor; kitaplar yüklenerek gönderilecek (daha yavaş).")}
        except StudioError as e:
            return {"ok": False, "url": "", "message": str(e)}

    # ---------- kuyruk ----------
    def enqueue(self, groups, force=False):
        """groups: her kitap için dosya kimlikleri listesi. Döner: kuyruğa giren / desteklenmeyen sayıları."""
        by_id = {}
        for b in self.lib.books():
            for f in b["formats"]:
                by_id[f["id"]] = (b, f)
        added, unsupported, already, seen = 0, [], 0, set()
        with self._lock:
            queued = {item["ckey"] for item in self.queue}
            for ids in groups:
                pairs = [by_id[i] for i in ids if i in by_id]
                if not pairs:
                    continue
                book = pairs[0][0]
                best = _best([f for _, f in pairs])
                if not best:
                    unsupported.append(book["title"])
                    continue
                if best["ckey"] in seen or best["ckey"] in queued:
                    continue
                seen.add(best["ckey"])
                prev = self.sent.get(best["ckey"])
                if prev and prev.get("status") == "sent" and not force:
                    already += 1
                    continue
                self.queue.append({"id": best["id"], "ckey": best["ckey"], "title": book["title"], "force": force})
                added += 1
            if added:
                if not self.status["running"]:
                    self.status.update(total=0, done=0, sent=0, skipped=0, failed=0, error=None, finished=None)
                self.status["total"] += added
        if added:
            self._start()
        return {"queued": added, "unsupported": len(unsupported), "unsupported_titles": unsupported[:20],
                "already": already}

    def cancel(self):
        with self._lock:
            n = len(self.queue)
            self.queue.clear()
            self.status["total"] -= n
        return n

    def _start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self.status["running"] = True
            self._thread = threading.Thread(target=self._run, name="studio-send", daemon=True)
            self._thread.start()

    def _run(self):
        last_save = 0
        try:
            while True:
                with self._lock:
                    if not self.queue:
                        break
                    item = self.queue.popleft()
                    self.status["current"] = item["title"]
                try:
                    res = self._send_one(item)
                except StudioError as e:
                    # Studio'ya hiç ulaşılamıyor: sıradakileri de bekletmeden durdur
                    with self._lock:
                        self.status["error"] = str(e)
                        self.status["failed"] += 1 + len(self.queue)
                        self.status["done"] += 1 + len(self.queue)
                        self.queue.clear()
                    self._found = None
                    break
                with self._lock:
                    self.status["done"] += 1
                    self.status[{"sent": "sent", "skipped": "skipped"}.get(res["status"], "failed")] += 1
                    self.sent[item["ckey"]] = {**res, "t": int(time.time()), "title": item["title"]}
                if time.time() - last_save > 5:
                    self._save()
                    last_save = time.time()
        finally:
            with self._lock:
                self.status.update(running=False, current=None, finished=int(time.time()))
            self._save()

    def _send_one(self, item):
        rec = self.lib.get_file(item["id"])
        if not rec:
            return {"status": "failed", "msg": "Dosya artık kitaplıkta yok"}
        path = self.lib.abs_path(rec)
        if not os.path.isfile(path):
            return {"status": "failed", "msg": "Dosya sunucuda bulunamadı"}
        rel = rec["path"]
        base = self.base()
        try:
            # Studio'da zaten var mı?
            if not item.get("force"):
                st, d = self._req(base, "GET", "/api/arsiv-kontrol?" + urlencode({"ad": rec["name"], "yol": rel}))
                if st == 400:  # Studio bu yolu görmüyor; yalnız adla sor
                    st, d = self._req(base, "GET", "/api/arsiv-kontrol?" + urlencode({"ad": rec["name"]}))
                if st == 200 and d.get("var"):
                    where = {"studyo": "Studio'da zaten var", "kutuphane": "Studio Kütüphanesi'nde zaten var",
                             "arsiv": "Studio'da daha önce işlenmiş"}.get(d.get("nerede"), "Studio'da zaten var")
                    return {"status": "skipped", "msg": where, "kimlik": d.get("kimlik"), "path": rel}
            # 1) aynı klasör: yalnız yolu bildir
            st, d = self._req(base, "POST", "/api/kitaplar/sunucudan", body=json.dumps({"yol": rel}),
                              headers={"Content-Type": "application/json"})
            if st == 200 and d.get("ok"):
                return {"status": "sent", "msg": "Gönderildi", "kimlik": d.get("kimlik"), "path": rel, "via": "yol"}
            # 2) Studio dosyayı göremiyor: yükle
            if st in (400, 404):
                st, d = self._upload(base, path, rec["name"])
                if st == 200 and d.get("ok"):
                    return {"status": "sent", "msg": "Gönderildi (yüklendi)", "kimlik": d.get("kimlik"),
                            "path": rel, "via": "yukleme"}
            return {"status": "failed", "msg": str(d.get("detail") or d.get("error") or f"Studio hata verdi ({st})")[:200],
                    "path": rel}
        except (OSError, http.client.HTTPException) as e:
            if not self._probe(base):
                raise StudioError(f"Dedplay Studio'ya ulaşılamadı ({base}): {e}")
            return {"status": "failed", "msg": f"Bağlantı hatası: {e}", "path": rel}

    def _upload(self, base, path, name):
        """multipart/form-data ile dosyayı akıtarak yükler (büyük PDF'ler belleğe alınmaz)."""
        b = "----kitaplik" + uuid.uuid4().hex
        safe = name.replace('"', "'")
        head = (f"--{b}\r\nContent-Disposition: form-data; name=\"dosya\"; filename=\"{safe}\"; "
                f"filename*=UTF-8''{quote(name)}\r\nContent-Type: application/octet-stream\r\n\r\n").encode("utf-8")
        tail = f"\r\n--{b}--\r\n".encode()
        size = os.path.getsize(path)
        u = urlsplit(base)
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        conn = cls(u.hostname, u.port or (443 if u.scheme == "https" else 80), timeout=600)
        try:
            conn.putrequest("POST", (u.path or "") + "/api/kitaplar/yukle")
            conn.putheader("Content-Type", f"multipart/form-data; boundary={b}")
            conn.putheader("Content-Length", str(len(head) + size + len(tail)))
            conn.endheaders()
            conn.send(head)
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(1 << 20)
                    if not chunk:
                        break
                    conn.send(chunk)
            conn.send(tail)
            r = conn.getresponse()
            raw = r.read()
        finally:
            conn.close()
        try:
            return r.status, json.loads(raw.decode("utf-8"))
        except ValueError:
            return r.status, {"detail": raw[:200].decode("utf-8", "replace")}

    # ---------- arayüz için ----------
    def public(self):
        with self._lock:
            st = dict(self.status)
            st["queued"] = len(self.queue)
            sent = {k: {"s": v.get("status"), "m": v.get("msg"), "t": v.get("t")} for k, v in self.sent.items()}
        return {"url": self.url, "found": self._found, "status": st, "sent": sent}

    def forget(self, ckeys):
        with self._lock:
            for k in ckeys:
                self.sent.pop(k, None)
        self._save()
