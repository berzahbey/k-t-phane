"""Geliştirme için: python app.py  (Docker içinde gunicorn kullanılır)."""
import os

from ktphane.web import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
