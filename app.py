import os
import re
from flask import Flask, render_template_string, send_from_directory
from pypdf import PdfReader
import warnings

# PDF uyarılarını gizleyelim ki loglar şişmesin
warnings.filterwarnings("ignore")

app = Flask(__name__)
BOOK_DIR = "/books"

# Bellek önbelleği (Cache) - Sayfaların anında açılmasını sağlar
CACHED_CATEGORIES = None

CATEGORIES = {
    "Din": {
        "İslam": ["islam", "kur'an", "hadis", "tefsir", "fikiht", "risale", "imam", "hz", "sunnet", "kuran", "peygamber", "tasavvuf", "ilahiyat", "mezhep", "itikat", "mevlana", "mesnevi", "muhyiddin"],
        "Yahudilik ve Hristiyanlık": ["yahudi", "hristiyan", "incil", "tevrat", "kilise", "musevi"],
        "Genel Din": ["din", "kutsal", "inanc", "religion", "theology"]
    },
    "Felsefe": {
        "İslam Felsefesi": ["farabi", "ibni sina", "gazali", "ibn rüşd", "islam felsefesi"],
        "Mantık": ["mantik", "logic", "akil"],
        "Genel Felsefe": ["felsefe", "philosophy", "etik", "metafizik", "epistemoloji", "ontoloji", "dusunce"]
    },
    "Tarih": {
        "Osmanlı Tarihi": ["osmanli", "padisah", "devlet-i", "tarih-i", "sadrazam"],
        "Dünya ve Diğer Tarih": ["tarih", "history", "savas", "imparatorluk", "yuzyil", "devrim", "antik", "ortaçag", "geschiedenis"]
    },
    "Sosyoloji": {
        "Toplum ve Kültür": ["sosyoloji", "sociology", "toplum", "kultur", "medeniyet", "halk", "kent", "society"]
    },
    "Psikoloji": {
        "Psikoloji ve Gelişim": ["psikoloji", "psychology", "bilinc", "terapi", "davranis", "kisisel gelisim", "freud", "jung", "psychologie"]
    },
    "Yazilim ve Teknoloji": {
        "Programlama ve Kod": ["python", "docker", "linux", "programming", "kod", "algoritma", "software", "javascript", "react", "git", "bilgisayar", "computer"]
    },
    "Edebiyat ve Roman": {
        "Roman ve Edebiyat": ["roman", "hikaye", "siir", "novel", "story", "poem", "edebiyat", "kitap", "divan", "literature", "telli", "gümüş", "flaubert", "aykol", "atam", "ozlük"],
        "Fantastik ve Bilim Kurgu": ["sci-fi", "science fiction", "bilim kurgu", "uzay", "galaksi", "fantastik", "fantasy"]
    },
    "Ekonomi ve İş": {
        "Ekonomi ve Finans": ["ekonomi", "economy", "finans", "finance", "pazarlama", "marketing", "yonetim", "business", "economie"]
    }
}

def detect_language(filename):
    name_lower = filename.lower()
    if any(k in name_lower for k in ["osmanlıca", "osmanlica", "elifba", "risale", "lügat", "lugat"]):
        return "Osmanlıca"
    elif any(k in name_lower for k in ["geschiedenis", "economie", "van", "een"]):
        return "Nederlands"
    elif any(k in name_lower for k in ["history", "philosophy", "the book", "introduction"]):
        return "English"
    else:
        return "Türkçe"

def check_pdf_ocr(file_path):
    try:
        reader = PdfReader(file_path)
        total_text = ""
        for i in range(min(1, len(reader.pages))):
            txt = reader.pages[i].extract_text()
            if txt:
                total_text += txt
        if len(total_text.strip()) < 30:
            return "PDF (OCR Gerekli)"
        else:
            return "PDF (Metin Tabanlı)"
    except Exception:
        return "PDF"

def parse_name_and_author(filename):
    clean_fn = os.path.splitext(filename)[0].replace('_', ' ').replace('-', ' - ')
    parts = clean_fn.split(' - ')
    if len(parts) >= 2:
        author = parts[0].strip().title()
        title = " - ".join(parts[1:]).strip().title()
    else:
        title = clean_fn.strip().title()
        author = "Bilinmeyen Yazar"
    return title, author

def classify_book(filename):
    name_lower = filename.lower()
    for main_cat, sub_cats in CATEGORIES.items():
        for sub_cat, keywords in sub_cats.items():
            for kw in keywords:
                if kw in name_lower:
                    return main_cat, sub_cat
    return "Genel Diğer", "Genel"

def get_categorized_books():
    global CACHED_CATEGORIES
    if CACHED_CATEGORIES is not None:
        return CACHED_CATEGORIES

    structured_data = {}
    try:
        if os.path.exists(BOOK_DIR):
            book_groups = {}
            for root, dirs, files in os.walk(BOOK_DIR):
                for file in files:
                    ext = file.lower().split('.')[-1]
                    if ext in ['pdf', 'epub']:
                        full_path = os.path.join(root, file)
                        title, author = parse_name_and_author(file)
                        lang = detect_language(file)
                        main_cat, sub_cat = classify_book(file)
                        
                        if ext == 'pdf':
                            ext_label = check_pdf_ocr(full_path)
                        else:
                            ext_label = "EPUB"
                        
                        group_key = re.sub(r'[^a-z0-9]', '', title.lower())
                        if not group_key:
                            group_key = file.lower()

                        rel_path = os.path.relpath(full_path, BOOK_DIR)
                        if group_key not in book_groups:
                            book_groups[group_key] = {
                                "main_cat": main_cat,
                                "sub_cat": sub_cat,
                                "title": title,
                                "author": author,
                                "formats": []
                            }
                        book_groups[group_key]["formats"].append({
                            "filename": file,
                            "ext_label": ext_label,
                            "ext_short": ext.upper(),
                            "lang": lang,
                            "path": rel_path
                        })

            for key, data in book_groups.items():
                m_cat = data["main_cat"]
                s_cat = data["sub_cat"]
                if m_cat not in structured_data:
                    structured_data[m_cat] = {}
                if s_cat not in structured_data[m_cat]:
                    structured_data[m_cat][s_cat] = []
                structured_data[m_cat][s_cat].append(data)
    except Exception as e:
        print(f"Tarama Hatası: {e}")
        
    CACHED_CATEGORIES = structured_data
    return structured_data

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <title>K-Tphane | Akıllı Arşiv</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 40px; }
        .container { max-width: 1050px; margin: 0 auto; background: #1e293b; padding: 30px; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.3); }
        h1 { color: #38bdf8; margin-top: 0; }
        details { background: #334155; margin-top: 15px; border-radius: 8px; overflow: hidden; border-left: 5px solid #38bdf8; }
        summary { padding: 18px 20px; font-size: 18px; font-weight: bold; color: #f43f5e; cursor: pointer; user-select: none; background: #334155; display: flex; justify-content: space-between; align-items: center; }
        summary:hover { background: #3b4252; }
        .content-area { padding: 15px 20px; background: #1e293b; border-top: 1px solid #475569; }
        details.sub-details { background: #263345; border-left: 4px solid #fbbf24; margin-top: 10px; }
        details.sub-details summary { padding: 12px 15px; font-size: 15px; color: #38bdf8; background: #263345; }
        .book-item { display: flex; justify-content: space-between; align-items: center; background: #0f172a; padding: 10px 14px; margin-top: 6px; border-radius: 6px; font-size: 13px; }
        .book-info { display: flex; flex-direction: column; gap: 3px; margin-right: 15px; }
        .book-title { color: #f8fafc; font-weight: bold; font-size: 14px; }
        .book-meta { color: #94a3b8; font-size: 12px; }
        .badges { display: flex; gap: 5px; margin-top: 4px; flex-wrap: wrap; }
        .badge { padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: bold; color: white; text-transform: uppercase; }
        .badge-pdf-metin { background: #059669; }
        .badge-pdf-ocr { background: #d97706; }
        .badge-epub { background: #8b5cf6; }
        .badge-lang { background: #0d9488; }
        .btn-group { display: flex; gap: 6px; flex-shrink: 0; }
        .btn { padding: 4px 10px; border-radius: 4px; font-size: 11px; font-weight: bold; text-decoration: none; color: white; }
        .btn-read { background: #0284c7; }
        .btn-read:hover { background: #0369a1; }
        .btn-download { background: #059669; }
        .btn-download:hover { background: #047857; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📚 K-Tphane Akıllı Arşiv</h1>
        <p>Kitaplarınız OCR durumu, yazar, format ve dil analizleriyle listelenmiştir.</p>

        {% if categories %}
            {% for main_cat, sub_cats in categories.items() %}
                <details>
                    <summary>📂 {{ main_cat }} ({{ sub_cats|length }})</summary>
                    <div class="content-area">
                        {% for sub_cat, books in sub_cats.items() %}
                            <details class="sub-details">
                                <summary>🔹 {{ sub_cat }} ({{ books|length }})</summary>
                                <div class="content-area">
                                    {% for book in books %}
                                        <div class="book-item">
                                            <div class="book-info">
                                                <span class="book-title">📖 {{ book.title }}</span>
                                                <span class="book-meta">Yazar: <strong>{{ book.author }}</strong></span>
                                                <div class="badges">
                                                    {% for fmt in book.formats %}
                                                        <span class="badge {% if 'OCR' in fmt.ext_label %}badge-pdf-ocr{% elif 'Metin' in fmt.ext_label %}badge-pdf-metin{% else %}badge-epub{% endif %}">{{ fmt.ext_label }}</span>
                                                        <span class="badge badge-lang">{{ fmt.lang }}</span>
                                                    {% endfor %}
                                                </div>
                                            </div>
                                            <div class="btn-group">
                                                {% for fmt in book.formats %}
                                                    <a href="/read/{{ fmt.path }}" target="_blank" class="btn btn-read">Oku ({{ fmt.ext_short }})</a>
                                                    <a href="/download/{{ fmt.path }}" class="btn btn-download">İndir ({{ fmt.ext_short }})</a>
                                                {% endfor %}
                                            </div>
                                        </div>
                                    {% endfor %}
                                </div>
                            </details>
                        {% endfor %}
                    </div>
                </details>
            {% endfor %}
        {% else %}
            <p>Kitap dizini taranamadı veya klasör boş.</p>
        {% endif %}
    </div>
</body>
</html>
"""

@app.route("/")
def index():
    categories = get_categorized_books()
    return render_template_string(HTML_TEMPLATE, categories=categories)

@app.route("/read/<path:filename>")
def read_book(filename):
    return send_from_directory(BOOK_DIR, filename)

@app.route("/download/<path:filename>")
def download_book(filename):
    return send_from_directory(BOOK_DIR, filename, as_attachment=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
