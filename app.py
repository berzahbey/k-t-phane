import os
import shutil
from flask import Flask, render_template_string, request, redirect, url_for

app = Flask(__name__)
BOOK_DIR = "/books"

CATEGORY_KEYWORDS = {
    "Felsefe": ["felsefe", "philosophy", "mantik", "logic", "etik", "eta", "dusunce"],
    "Tarih": ["tarih", "history", "osmanli", "savas", "imparatorluk", "yuzyil", "devrim"],
    "Yazilim_Teknoloji": ["python", "docker", "linux", "programming", "kod", "algoritma", "software", "javascript", "react", "git"],
    "Edebiyat_Roman": ["roman", "hikaye", "siir", "novel", "story", "poem", "edebiyat", "kitap"],
    "Bilim_Kurgu": ["sci-fi", "science fiction", "bilim kurgu", "uzay", "galaksi"],
    "Ekonomi_Is": ["ekonomi", "economy", "finans", "finance", "pazarlama", "marketing", "yonetim", "business"],
    "Psikoloji": ["psikoloji", "psychology", "bilinc", "terapi", "davranis", "kisisel gelisim"]
}

def classify_book(filename):
    name_lower = filename.lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in name_lower:
                return cat
    return "Genel_Diger"

def organize_books_action():
    if not os.path.exists(BOOK_DIR):
        return "Kitap dizini bulunamadı!"
    count = 0
    for root, dirs, files in os.walk(BOOK_DIR):
        if "PDF" in root or "ePub" in root:
            continue
        for file in files:
            ext = file.lower().split('.')[-1]
            if ext in ['pdf', 'epub']:
                source_path = os.path.join(root, file)
                category = classify_book(file)
                format_folder = "PDF" if ext == 'pdf' else "ePub"
                target_folder = os.path.join(BOOK_DIR, category, format_folder)
                os.makedirs(target_folder, exist_ok=True)
                target_path = os.path.join(target_folder, file)
                if source_path != target_path:
                    try:
                        shutil.move(source_path, target_path)
                        count += 1
                    except Exception as e:
                        print(f"Hata ({file}): {e}")
    return f"{count} adet kitap başarıyla kategorize edildi ve yerleştirildi!"

def scan_status():
    structure = {}
    if not os.path.exists(BOOK_DIR):
        return structure
    for cat in os.listdir(BOOK_DIR):
        cat_path = os.path.join(BOOK_DIR, cat)
        if os.path.isdir(cat_path) and cat not in ['PDF', 'ePub']:
            structure[cat] = {"PDF": [], "ePub": []}
            for fmt in ["PDF", "ePub"]:
                fmt_path = os.path.join(cat_path, fmt)
                if os.path.exists(fmt_path):
                    structure[cat][fmt] = os.listdir(fmt_path)
    return structure

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <title>K-Tphane Otomatik Sınıflandırıcı</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 40px; }
        .container { max-width: 900px; margin: 0 auto; background: #1e293b; padding: 30px; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.3); }
        h1 { color: #38bdf8; margin-top: 0; }
        .btn { background: #0284c7; color: white; padding: 12px 24px; border: none; border-radius: 6px; font-size: 16px; cursor: pointer; text-decoration: none; display: inline-block; font-weight: bold; }
        .btn:hover { background: #0369a1; }
        .category-box { background: #334155; margin-top: 20px; padding: 15px; border-radius: 8px; }
        .category-box h3 { color: #f43f5e; margin-top: 0; border-bottom: 1px solid #475569; padding-bottom: 8px; }
        .format-title { color: #fbbf24; font-weight: bold; margin-top: 10px; font-size: 14px; }
        ul { margin: 5px 0 15px 20px; font-size: 14px; color: #cbd5e1; }
        .msg { background: #065f46; color: #34d399; padding: 12px; border-radius: 6px; margin-bottom: 20px; font-weight: bold; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📚 K-Tphane Arşiv Sınıflandırıcı</h1>
        <p>Binlerce kitabınızı analiz edip konularına ayırır, PDF ve ePub klasörlerine yerleştirir.</p>
        {% if msg %}
            <div class="msg">{{ msg }}</div>
        {% endif %}
        <form action="/organize" method="post" style="margin-bottom: 30px;">
            <button type="submit" class="btn">🚀 Kitapları Otomatik Sınıflandır ve Dağıt</button>
        </form>
        <h2>📁 Mevcut Kategori Durumu</h2>
        {% if categories %}
            {% for cat, formats in categories.items() %}
                <div class="category-box">
                    <h3>📂 {{ cat }}</h3>
                    <div class="format-title">📄 PDF Kutusu ({{ formats['PDF']|length }})</div>
                    <ul>
                        {% for b in formats['PDF'][:10] %}
                            <li>{{ b }}</li>
                        {% endfor %}
                        {% if formats['PDF']|length > 10 %}
                            <li><em>... ve {{ formats['PDF']|length - 10 }} dosya daha ...</em></li>
                        {% endif %}
                    </ul>
                    <div class="format-title">📖 ePub Kutusu ({{ formats['ePub']|length }})</div>
                    <ul>
                        {% for b in formats['ePub'][:10] %}
                            <li>{{ b }}</li>
                        {% endfor %}
                        {% if formats['ePub']|length > 10 %}
                            <li><em>... ve {{ formats['ePub']|length - 10 }} dosya daha ...</em></li>
                        {% endif %}
                    </ul>
                </div>
            {% endfor %}
        {% else %}
            <p>Henüz düzenlenmiş kategori bulunamadı veya klasör boş.</p>
        {% endif %}
    </div>
</body>
</html>
"""

@app.route("/")
def index():
    msg = request.args.get('msg')
    categories = scan_status()
    return render_template_string(HTML_TEMPLATE, categories=categories, msg=msg)

@app.route("/organize", methods=["POST"])
def organize():
    result_msg = organize_books_action()
    return redirect(url_for('index', msg=result_msg))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
