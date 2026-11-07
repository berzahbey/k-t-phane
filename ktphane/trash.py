"""Kitap silme: dosya sunucudan KALICI olarak silinir.

Arayüz iki aşamalı onay ister (önce "silinsin mi?", sonra "geri alınamaz, emin misiniz?").
API de yalnızca istekte confirm="KALICI-SIL" varsa siler; tek tıklık/yanlışlıkla silme olmaz.
Ne silindiği /data/silinenler-kaydi.json'a not edilir (dosyanın kendisi değil, adı ve yolu).
"""
import json
import logging
import os
import threading
import time

log = logging.getLogger("ktphane.trash")
CONFIRM_TOKEN = "KALICI-SIL"


class Trash:
    def __init__(self, lib):
        self.lib = lib
        self.log_file = os.path.join(lib.data_dir, "silinenler-kaydi.json")
        self._lock = threading.Lock()

    def _append_log(self, items):
        try:
            with open(self.log_file, encoding="utf-8") as f:
                old = json.load(f)
        except Exception:
            old = []
        old = (old + items)[-5000:]
        tmp = self.log_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(old, f, ensure_ascii=False)
        os.replace(tmp, self.log_file)

    def delete(self, fids, title=""):
        """Dosyaları diskten siler; silinenlerin listesini döndürür."""
        from .organizer import _cleanup_dirs
        done, errors = [], []
        with self._lock:
            for fid in fids:
                rec = self.lib.get_file(fid)
                if not rec:
                    continue
                path = self.lib.abs_path(rec)
                try:
                    if os.path.exists(path):
                        os.remove(path)
                    side = os.path.join(os.path.dirname(path), "._" + os.path.basename(path))
                    if os.path.exists(side):
                        os.remove(side)
                except OSError as e:
                    errors.append(f"{rec['name']}: {e}")
                    continue
                done.append({"path": rec["path"], "name": rec["name"], "size": rec["size"],
                             "title": title or rec.get("title") or rec["name"], "time": int(time.time())})
                with self.lib._lock:
                    self.lib.files.pop(fid, None)
                    self.lib.org.get("placed", {}).pop(rec["path"], None)
                    self.lib._version += 1
                _cleanup_dirs(self.lib, [os.path.dirname(path)])
        if done:
            try:
                self._append_log(done)
                self.lib._save_cache()
                self.lib.save_org()
            except OSError:
                pass
            log.info("%d dosya kalıcı olarak silindi: %s", len(done), ", ".join(d["path"] for d in done))
        return done, errors

    def auto_purge(self):  # eski sürümle uyum
        pass
