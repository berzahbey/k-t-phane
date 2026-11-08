"""Favoriler: kitap listesi + kitap klasöründe "Favoriler" klasörü.

Favoriler klasöründeki dosyalar kopya DEĞİL, sabit bağlantıdır (hard link): ek disk yeri
kaplamaz, ZimaOS Dosyalar'da ve Mac/Windows paylaşımında normal dosya gibi görünür. Asıl dosya
düzenleyici tarafından taşınsa da bağlantı çalışmaya devam eder. Sabit bağlantı yapılamazsa
(farklı disk) göreli kısayol (symlink) kullanılır.

Favoriler klasörü taranmaz (kitaplar iki kez görünmesin, kopya sayılmasın).
Uygulama yalnızca kendi oluşturduğu bağlantıları siler; klasöre elle konan dosyalara dokunmaz.
"""
import json
import logging
import os
import threading

log = logging.getLogger("ktphane.favorites")
FAV_DIR = "Favoriler"


class Favorites:
    def __init__(self, lib):
        self.lib = lib
        self.file = os.path.join(lib.data_dir, "favoriler.json")
        self._lock = threading.Lock()
        try:
            with open(self.file, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            d = {}
        self.keys = set(d.get("keys", []))     # favori dosyaların içerik anahtarları (ckey)
        self.links = d.get("links", {})        # Favoriler altındaki göreli yol -> ckey (bizim oluşturduklarımız)

    def _save(self):
        tmp = self.file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"keys": sorted(self.keys), "links": self.links}, f, ensure_ascii=False)
        os.replace(tmp, self.file)

    def set(self, ckeys, on):
        with self._lock:
            for k in ckeys:
                (self.keys.add if on else self.keys.discard)(k)
            self._save()
            with self.lib._lock:
                self.lib._version += 1
        self.sync()

    def sync(self):
        """Favoriler klasörünü listeyle eşitler."""
        from .organizer import safe_folder
        root = os.path.join(self.lib.book_dir, FAV_DIR)
        with self._lock:
            want = {}
            for b in self.lib.books():
                for f in b["formats"]:
                    if f["ckey"] in self.keys:
                        sub = safe_folder(b["series"]) if b.get("series") else ""
                        want[os.path.join(sub, f["name"])] = f
            # artık favori olmayan / kaynağı kaybolan bağlantıları kaldır (yalnızca bizimkiler)
            for rel, ck in list(self.links.items()):
                p = os.path.join(root, rel)
                if rel not in want or want[rel]["ckey"] != ck:
                    try:
                        if os.path.lexists(p):
                            os.remove(p)
                        d = os.path.dirname(p)
                        if d != root and os.path.isdir(d) and not os.listdir(d):
                            os.rmdir(d)
                    except OSError as e:
                        log.warning("Favori bağlantısı silinemedi %s: %s", p, e)
                        continue
                    self.links.pop(rel, None)
            # eksikleri oluştur
            for rel, f in want.items():
                p = os.path.join(root, rel)
                if rel in self.links and os.path.lexists(p):
                    continue
                if os.path.lexists(p):
                    continue  # aynı adda kullanıcı dosyası var, dokunma
                src = self.lib.abs_path(f)
                try:
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    try:
                        os.link(src, p)
                    except OSError:
                        os.symlink(os.path.relpath(src, os.path.dirname(p)), p)
                    self.links[rel] = f["ckey"]
                except OSError as e:
                    log.warning("Favori bağlantısı oluşturulamadı %s: %s", p, e)
            if not self.links and os.path.isdir(root) and not os.listdir(root):
                try:
                    os.rmdir(root)
                except OSError:
                    pass
            self._save()

    def forget(self, ckeys):
        """Silinen kitaplar favorilerden düşer."""
        if any(k in self.keys for k in ckeys):
            self.set([k for k in ckeys if k in self.keys], False)
