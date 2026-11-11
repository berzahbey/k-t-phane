"""Okuma kayıtları: kaldığın yer, vurgular, notlar, yer imleri.

Sunucuda tutulur (/data/okuma.json), böylece iPhone'da bıraktığın yerden Mac'te devam edilir.
Kayıtlar dosya içeriğine bağlı anahtarla (ckey) tutulur: kitap taşınsa da kaybolmaz;
adı değişirse library.rekey ile taşınır.
"""
import json
import os
import threading
import time
import uuid

NOTE_TYPES = {"highlight", "note", "bookmark"}
COLORS = {"yellow", "green", "blue", "pink", "orange"}


class Reading:
    def __init__(self, lib):
        self.lib = lib
        self.file = os.path.join(lib.data_dir, "okuma.json")
        self._lock = threading.Lock()
        try:
            with open(self.file, encoding="utf-8") as f:
                self.data = json.load(f)
        except Exception:
            self.data = {}

    def _save(self):
        tmp = self.file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False)
        os.replace(tmp, self.file)

    def get(self, ckey):
        with self._lock:
            d = self.data.get(ckey) or {}
            return {"pos": d.get("pos"), "notes": list(d.get("notes", []))}

    def set_pos(self, ckey, pos):
        clean = {"updated": int(time.time() * 1000)}
        for k in ("cfi", "chapter", "device", "href"):
            if isinstance(pos.get(k), str):
                clean[k] = pos[k][:500]
        for k in ("page", "pages"):
            if isinstance(pos.get(k), (int, float)):
                clean[k] = int(pos[k])
        if isinstance(pos.get("percent"), (int, float)):
            clean["percent"] = max(0.0, min(1.0, float(pos["percent"])))
        with self._lock:
            d = self.data.setdefault(ckey, {})
            d["pos"] = clean
            d["opened"] = clean["updated"]
            self._save()
        return clean

    def upsert_note(self, ckey, note):
        t = note.get("type")
        if t not in NOTE_TYPES:
            raise ValueError("geçersiz not türü")
        now = int(time.time() * 1000)
        with self._lock:
            d = self.data.setdefault(ckey, {})
            notes = d.setdefault("notes", [])
            nid = note.get("id")
            cur = next((n for n in notes if n["id"] == nid), None) if nid else None
            if cur is None:
                cur = {"id": uuid.uuid4().hex[:12], "type": t, "created": now}
                notes.append(cur)
            for k in ("cfi", "text", "note", "chapter", "label"):
                if isinstance(note.get(k), str):
                    cur[k] = note[k][:20000 if k == "note" else 4000]
            if isinstance(note.get("page"), (int, float)):
                cur["page"] = int(note["page"])
            if isinstance(note.get("percent"), (int, float)):
                cur["percent"] = max(0.0, min(1.0, float(note["percent"])))
            if isinstance(note.get("rects"), list):  # PDF vurguları: sayfaya göre oransal dikdörtgenler
                rects = []
                for r in note["rects"][:80]:
                    if isinstance(r, list) and len(r) >= 4 and all(isinstance(v, (int, float)) for v in r[:4]):
                        rects.append([round(max(0.0, min(1.0, float(v))), 4) for v in r[:4]])
                cur["rects"] = rects
            if note.get("color") in COLORS:
                cur["color"] = note["color"]
            cur["updated"] = now
            self._save()
            return cur

    def delete_note(self, ckey, nid):
        with self._lock:
            d = self.data.get(ckey) or {}
            before = len(d.get("notes", []))
            d["notes"] = [n for n in d.get("notes", []) if n["id"] != nid]
            if len(d.get("notes", [])) != before:
                self._save()
                return True
        return False

    def recent(self, limit=12):
        with self._lock:
            items = [(k, v) for k, v in self.data.items() if v.get("pos")]
        items.sort(key=lambda kv: -kv[1].get("opened", 0))
        return [{"ckey": k, **v["pos"], "notes": len(v.get("notes", []))} for k, v in items[:limit]]

    def rekey(self, old, new):
        with self._lock:
            if old in self.data and new not in self.data:
                self.data[new] = self.data.pop(old)
                self._save()
