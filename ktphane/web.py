"""Flask uygulaması: API + arayüz."""
import hmac
import logging
import mimetypes
import os

from flask import Flask, Response, abort, jsonify, render_template, request, send_file

from . import __version__
from .categories import category_tree
from .library import Library
from . import organizer

MIME = {"epub": "application/epub+zip", "pdf": "application/pdf", "mobi": "application/x-mobipocket-ebook",
        "azw3": "application/vnd.amazon.ebook", "azw": "application/vnd.amazon.ebook",
        "djvu": "image/vnd.djvu", "djv": "image/vnd.djvu", "fb2": "application/x-fictionbook+xml",
        "txt": "text/plain; charset=utf-8", "cbz": "application/vnd.comicbook+zip"}


def create_app():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    app.json.ensure_ascii = False

    lib = Library(
        book_dir=os.environ.get("BOOK_DIR", "/books"),
        data_dir=os.environ.get("DATA_DIR", "/data"),
        scan_interval=int(os.environ.get("SCAN_INTERVAL", "1800")),
    )
    from .enricher import Enricher
    lib.enricher = Enricher(lib)
    from .trash import Trash
    lib.trash = Trash(lib)
    from .dedupe import Dedupe
    lib.dedupe = Dedupe(lib)
    from .favorites import Favorites
    lib.favorites = Favorites(lib)
    lib.start_background()
    lib.enricher.start()
    app.extensions["library"] = lib

    user = os.environ.get("APP_USER", "")
    password = os.environ.get("APP_PASSWORD", "")

    @app.before_request
    def auth():
        if not password or request.path == "/health":
            return None
        a = request.authorization
        if a and hmac.compare_digest(a.username or "", user or (a.username or "")) \
                and hmac.compare_digest(a.password or "", password):
            return None
        return Response("Giriş gerekli", 401, {"WWW-Authenticate": 'Basic realm="Kitaplik"'})

    @app.get("/health")
    def health():
        return {"ok": True, "version": __version__}

    @app.get("/")
    def index():
        return render_template("index.html", version=__version__)

    @app.get("/api/books")
    def api_books():
        return jsonify(books=lib.books(), status=lib.status, tree=category_tree(),
                       version=__version__, analysis=_analysis_status())

    @app.get("/api/status")
    def api_status():
        return jsonify(status=lib.status, count=len(lib.files), organize=lib.org_status,
                       analysis=_analysis_status())

    def _analysis_status():
        e = lib.enricher
        return {**e.status, "ai": e.ai.status(), "analysed": len(e.data)}

    @app.post("/api/rescan")
    def api_rescan():
        full = request.args.get("full") == "1"
        started = lib.trigger_scan(full=full)
        return jsonify(started=started, status=lib.status)

    # ---------- klasör düzenleme ----------
    def _org_state(with_plan=False):
        out = {"writable": organizer.writable(lib), "auto": bool(lib.org.get("auto")),
               "status": lib.org_status, "can_undo": bool(lib.org.get("journal")),
               "scanning": lib.status.get("scanning")}
        if lib.org.get("journal"):
            last = lib.org["journal"][-1]
            out["last_batch"] = {"time": last["time"], "count": len(last["moves"]), "auto": last.get("auto")}
        if with_plan:
            moves = organizer.plan(lib)
            out["count"] = len(moves)
            out["pending_books"] = sum(1 for b in lib.books() if b.get("pending"))
            out["moves"] = [{"from": m["from"], "to": m["to"]} for m in moves[:500]]
            tree = {}
            for m in moves:
                parts = m["to"].split(os.sep)
                key = os.sep.join(parts[:2]) if len(parts) > 2 else parts[0]
                tree[key] = tree.get(key, 0) + 1
            out["summary"] = sorted(tree.items(), key=lambda kv: (-kv[1], kv[0]))
        return out

    @app.get("/api/organize")
    def org_get():
        return jsonify(_org_state(with_plan=True))

    @app.post("/api/organize/apply")
    def org_apply():
        if not organizer.writable(lib):
            return jsonify(error="Kitap klasörü salt okunur bağlanmış. Compose dosyasında "
                                 "'/books:ro' yerine '/books' yazın."), 409
        auto = (request.get_json(silent=True) or {}).get("auto", True)
        lib.org["auto"] = bool(auto)
        lib.save_org()
        started = organizer.run_in_background(lib, organizer.apply_locked, auto=False)
        return jsonify(started=started, **_org_state())

    @app.post("/api/organize/undo")
    def org_undo():
        started = organizer.run_in_background(lib, organizer.undo_locked)
        return jsonify(started=started, **_org_state())

    @app.post("/api/organize/auto")
    def org_auto():
        lib.org["auto"] = bool((request.get_json(silent=True) or {}).get("on"))
        lib.save_org()
        return jsonify(_org_state())

    # ---------- ayarlar ----------
    @app.get("/api/settings")
    def settings_get():
        return jsonify(ai=lib.enricher.ai.public_settings(), ai_status=lib.enricher.ai.status(), prefs=lib.prefs)

    @app.post("/api/settings")
    def settings_post():
        d = request.get_json(silent=True) or {}
        ai = lib.enricher.ai
        try:
            ai.update_settings(key=d.get("gemini_api_key"), model=d.get("gemini_model"),
                               daily_limit=d.get("gemini_daily_limit"))
        except (ValueError, OSError) as e:
            return jsonify(error=str(e)), 400
        ok, msg = ai.test() if ai.enabled and d.get("gemini_api_key") else (None, None)
        lib.enricher.wake()
        return jsonify(ai=ai.public_settings(), ai_status=ai.status(), test_ok=ok, test_message=msg)

    @app.post("/api/settings/test")
    def settings_test():
        ok, msg = lib.enricher.ai.test()
        if ok:
            lib.enricher.wake()
        return jsonify(ok=ok, message=msg, ai_status=lib.enricher.ai.status())

    # ---------- silme (kalıcı, iki aşamalı onay) ----------
    @app.post("/api/delete")
    def api_delete():
        from .trash import CONFIRM_TOKEN
        if not organizer.writable(lib):
            return jsonify(error="Kitap klasörü salt okunur; silmek için compose'ta ':ro' kaldırılmalı."), 409
        d = request.get_json(silent=True) or {}
        if d.get("confirm") != CONFIRM_TOKEN:
            return jsonify(error="Silme onayı eksik"), 400
        ids = [i for i in (d.get("ids") or []) if isinstance(i, str)][:1000]
        if not ids:
            return jsonify(error="Silinecek dosya seçilmedi"), 400
        done, errors = lib.trash.delete(ids, title=d.get("title") or "")
        return jsonify(deleted=done, errors=errors)

    # ---------- kopyalar ----------
    @app.get("/api/duplicates")
    def dup_list():
        groups = lib.dedupe.find()
        active = [g for g in groups if not g["ignored"]]
        return jsonify(groups=groups, auto=bool(lib.dedupe.state.get("auto")),
                       count=len(active), extra=sum(len(g["files"]) - 1 for g in active),
                       different=getattr(lib.dedupe, "different", []))

    @app.post("/api/duplicates/ignore")
    def dup_ignore():
        d = request.get_json(silent=True) or {}
        ign = set(lib.dedupe.state.get("ignored", []))
        for k in d.get("keys") or []:
            (ign.discard if d.get("undo") else ign.add)(k)
        lib.dedupe.state["ignored"] = sorted(ign)
        lib.dedupe.save_state()
        return jsonify(ok=True)

    @app.post("/api/duplicates/auto")
    def dup_auto():
        d = request.get_json(silent=True) or {}
        lib.dedupe.state["auto"] = bool(d.get("on")) and d.get("confirm") == "KALICI-SIL"
        lib.dedupe.save_state()
        return jsonify(auto=lib.dedupe.state["auto"])

    # ---------- elle düzeltme / Gemini'ye sor ----------
    @app.post("/api/book/category")
    def book_category():
        d = request.get_json(silent=True) or {}
        ckeys = [k for k in d.get("ckeys") or [] if isinstance(k, str)]
        if not ckeys:
            return jsonify(error="Kitap seçilmedi"), 400
        lib.set_manual(ckeys, cat=d.get("cat") or None, sub=d.get("sub") or None, lang=d.get("lang") or None)
        if lib.org.get("auto") and organizer.writable(lib):
            organizer.run_in_background(lib, organizer.apply_locked, auto=True)
        return jsonify(ok=True)

    @app.post("/api/book/edit")
    def book_edit():
        d = request.get_json(silent=True) or {}
        fids = [i for i in d.get("ids") or [] if isinstance(i, str)]
        title = (d.get("title") or "").strip()
        if not fids or not title:
            return jsonify(error="Başlık boş olamaz"), 400
        rename = bool(d.get("rename")) and organizer.writable(lib)
        renamed, errors = lib.edit_book(fids, title, d.get("author") or "", rename=rename)
        return jsonify(ok=True, renamed=renamed, errors=errors)

    @app.post("/api/book/ask-ai")
    def book_ask_ai():
        d = request.get_json(silent=True) or {}
        ckeys = [k for k in d.get("ckeys") or [] if isinstance(k, str)]
        if d.get("clear_manual"):
            lib.set_manual(ckeys)
        lib.enricher.ask_again(ckeys, redo=bool(d.get("redo")))
        return jsonify(ok=True, ai=lib.enricher.ai.status())

    @app.post("/api/prefs")
    def prefs_post():
        d = request.get_json(silent=True) or {}
        if "folder_strict" in d:
            lib.set_pref("folder_strict", bool(d["folder_strict"]))
            lib.enricher.wake()
        return jsonify(prefs=lib.prefs)

    # ---------- favoriler ----------
    @app.post("/api/favorites")
    def favorites_post():
        d = request.get_json(silent=True) or {}
        ckeys = [k for k in d.get("ckeys") or [] if isinstance(k, str)]
        if not ckeys:
            return jsonify(error="Kitap seçilmedi"), 400
        if not organizer.writable(lib):
            lib.favorites.keys.update(ckeys) if d.get("on") else lib.favorites.keys.difference_update(ckeys)
            lib.favorites._save()
            with lib._lock:
                lib._version += 1
            return jsonify(ok=True, warning="Kitap klasörü salt okunur; Favoriler klasörü oluşturulamadı")
        lib.favorites.set(ckeys, bool(d.get("on")))
        return jsonify(ok=True, count=len(lib.favorites.keys))

    def _rec(fid):
        rec = lib.get_file(fid)
        if not rec:
            abort(404)
        path = lib.abs_path(rec)
        if not os.path.isfile(path):
            abort(404)
        return rec, path

    @app.get("/file/<fid>")
    def file_inline(fid):
        rec, path = _rec(fid)
        mime = MIME.get(rec["ext"]) or mimetypes.guess_type(rec["name"])[0] or "application/octet-stream"
        return send_file(path, mimetype=mime, conditional=True, download_name=rec["name"],
                         as_attachment=False, max_age=3600)

    @app.get("/download/<fid>")
    def file_download(fid):
        rec, path = _rec(fid)
        return send_file(path, as_attachment=True, download_name=rec["name"], conditional=True)

    @app.get("/cover/<fid>")
    def cover(fid):
        if not lib.get_file(fid):
            abort(404)
        p = lib.cover_path(fid)
        if not p:
            return Response(status=204)
        return send_file(p, mimetype="image/jpeg", max_age=86400 * 7)

    @app.get("/read/<fid>")
    def reader(fid):
        rec, _ = _rec(fid)
        if rec["ext"] != "epub":
            return send_file(lib.abs_path(rec), mimetype=MIME.get(rec["ext"]) or "application/octet-stream",
                             conditional=True, download_name=rec["name"])
        return render_template("reader.html", rec=rec, title=rec.get("title") or rec["name"])

    return app
