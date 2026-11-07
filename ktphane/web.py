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
        return Response("Giriş gerekli", 401, {"WWW-Authenticate": 'Basic realm="K-Tphane"'})

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
        return jsonify(ai=lib.enricher.ai.public_settings(), ai_status=lib.enricher.ai.status())

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
