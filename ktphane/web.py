"""Flask uygulaması: API + arayüz."""
import hmac
import logging
import mimetypes
import os

from flask import Flask, Response, abort, jsonify, render_template, request, send_file

from . import __version__
from .categories import category_tree
from .library import Library

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
    lib.start_background()
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
                       version=__version__)

    @app.get("/api/status")
    def api_status():
        return jsonify(status=lib.status, count=len(lib.files))

    @app.post("/api/rescan")
    def api_rescan():
        full = request.args.get("full") == "1"
        started = lib.trigger_scan(full=full)
        return jsonify(started=started, status=lib.status)

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
