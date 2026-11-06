import os
from flask import Flask, render_template_string, send_from_directory

app = Flask(__name__)
BOOK_DIR = "/books"

@app.route("/")
def index():
    books = []
    if os.path.exists(BOOK_DIR):
        for root, dirs, files in os.walk(BOOK_DIR):
            for file in files:
                ext = file.lower().split('.')[-1]
                if ext in ['pdf', 'epub']:
                    rel_path = os.path.relpath(os.path.join(root, file), BOOK_DIR)
                    books.append({"name": file, "ext": ext.upper(), "path": rel_path})

    return render_template_string('''
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <title>K-Tphane</title>
        <style>
            body { font-family: -apple-system, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 40px; }
            .container { max-width: 900px; margin: 0 auto; background: #1e293b; padding: 30px; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.3); }
            h1 { color: #38bdf8; margin-top: 0; }
            .book-item { display: flex; justify-content: space-between; align-items: center; background: #334155; padding: 12px 16px; margin-top: 8px; border-radius: 6px; }
            .btn-group { display: flex; gap: 8px; }
            .btn { padding: 6px 12px; border-radius: 4px; font-size: 12px; font-weight: bold; text-decoration: none; color: white; background: #0284c7; }
            .btn-download { background: #059669; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📚 K-Tphane Arşivi</h1>
            <p>Toplam Bulunan Kitap: <strong>{{ books|length }}</strong></p>
            {% for book in books %}
                <div class="book-item">
                    <span>📖 {{ book.name }}</span>
                    <div class="btn-group">
                        <a href="/read/{{ book.path }}" target="_blank" class="btn">Oku</a>
                        <a href="/download/{{ book.path }}" class="btn btn-download">İndir</a>
                    </div>
                </div>
            {% endfor %}
        </div>
    </body>
    </html>
    ''', books=books)

@app.route("/read/<path:filename>")
def read_book(filename):
    return send_from_directory(BOOK_DIR, filename)

@app.route("/download/<path:filename>")
def download_book(filename):
    return send_from_directory(BOOK_DIR, filename, as_attachment=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
