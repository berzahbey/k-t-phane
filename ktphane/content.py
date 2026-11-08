"""Kitabın içinden metin örneği çıkarma (EPUB bölümleri, PDF sayfaları, gerekirse OCR)
ve metne dayalı dil tespiti."""
import html
import logging
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET

from .textutil import ARABIC_RE, words

log = logging.getLogger("ktphane.content")

NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container", "opf": "http://www.idpf.org/2007/opf"}
TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)
WS_RE = re.compile(r"[ \t\r\f\v]+")

TESSERACT = shutil.which("tesseract")
OCR_LANGS = os.environ.get("OCR_LANGS", "tur+ara+eng")
OCR_PAGES = int(os.environ.get("OCR_PAGES", "3"))


def _clean(t):
    t = html.unescape(TAG_RE.sub(" ", t))
    t = WS_RE.sub(" ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def epub_text(path, limit=9000):
    """Okuma sırasına göre (spine) ilk bölümlerin metni + içindekiler başlıkları."""
    toc, parts, total = [], [], 0
    with zipfile.ZipFile(path) as z:
        try:
            opf_path = ET.fromstring(z.read("META-INF/container.xml")).find(".//c:rootfile", NS).get("full-path")
        except Exception:
            opf_path = next((n for n in z.namelist() if n.lower().endswith(".opf")), None)
        names = []
        if opf_path:
            opf = ET.fromstring(z.read(opf_path))
            base = posixpath.dirname(opf_path)
            items = {i.get("id"): i for i in opf.iter() if i.tag.endswith("item")}
            for ref in (r for r in opf.iter() if r.tag.endswith("itemref")):
                it = items.get(ref.get("idref"))
                if it is not None:
                    names.append(posixpath.normpath(posixpath.join(base, it.get("href", "").split("#")[0])))
            # içindekiler (EPUB3 nav veya EPUB2 ncx)
            for it in items.values():
                href = posixpath.normpath(posixpath.join(base, it.get("href", "")))
                if "nav" in (it.get("properties") or "") or href.endswith(".ncx"):
                    try:
                        raw = z.read(href).decode("utf-8", "ignore")
                        labels = re.findall(r"<(?:a|text)[^>]*>(.*?)</(?:a|text)>", raw, re.S)
                        toc += [_clean(l) for l in labels if _clean(l)]
                    except KeyError:
                        pass
        if not names:
            names = sorted(n for n in z.namelist() if n.lower().endswith((".xhtml", ".html", ".htm")))
        for n in names:
            try:
                t = _clean(z.read(n).decode("utf-8", "ignore"))
            except KeyError:
                continue
            if len(t) < 200 and total == 0:
                continue  # kapak / boş sayfa
            parts.append(t)
            total += len(t)
            if total >= limit:
                break
    return "\n".join(parts)[:limit], toc[:60]


def pdf_text(path, limit=9000, max_pages=15):
    from pypdf import PdfReader
    reader = PdfReader(path, strict=False)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            return ""
    out, total = [], 0
    for i in range(min(max_pages, len(reader.pages))):
        try:
            t = reader.pages[i].extract_text() or ""
        except Exception:
            continue
        t = WS_RE.sub(" ", t).strip()
        out.append(t)
        total += len(t)
        if total >= limit:
            break
    return "\n".join(out)[:limit]


def pdf_ocr(path, pages=OCR_PAGES):
    """Taranmış PDF'in ilk sayfalarını OCR'lar (yalnızca örnek metin için)."""
    if not TESSERACT:
        return ""
    import pypdfium2 as pdfium
    texts = []
    pdf = pdfium.PdfDocument(path)
    try:
        n = len(pdf)
        # kapak ve boş ilk sayfalar yerine biraz içeriden başla
        start = 1 if n > 3 else 0
        for i in range(start, min(n, start + pages)):
            page = pdf[i]
            img = page.render(scale=300 / 72).to_pil().convert("L")
            page.close()
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                img.save(tmp.name)
            try:
                res = subprocess.run(["nice", "-n", "15", TESSERACT, tmp.name, "stdout", "-l", OCR_LANGS,
                                      "--psm", "3"], capture_output=True, text=True, timeout=180)
                texts.append(res.stdout)
            except Exception as e:
                log.info("OCR hatası %s s.%d: %s", path, i, e)
            finally:
                os.unlink(tmp.name)
    finally:
        pdf.close()
    return WS_RE.sub(" ", "\n".join(texts)).strip()


def extract(rec, path):
    """{'text','toc','ocr'} döndürür."""
    ext = rec["ext"]
    if ext == "epub":
        text, toc = epub_text(path)
        return {"text": text, "toc": toc, "ocr": False}
    if ext == "pdf":
        text = pdf_text(path)
        if len(text.strip()) >= 300:
            return {"text": text, "toc": [], "ocr": False}
        return {"text": pdf_ocr(path), "toc": [], "ocr": True}
    if ext == "txt":
        with open(path, "rb") as f:
            return {"text": f.read(9000).decode("utf-8", "ignore"), "toc": [], "ocr": False}
    return {"text": "", "toc": [], "ocr": False}


# ---------- metinden dil tespiti ----------
_LATIN = {
    "Türkçe": "ve bir bu da de ile için olan gibi daha çok ne ki olarak en kadar sonra ama her şey "
              "değil var olduğu oldu ancak böyle diye büyük onun bunun şu hem",
    "English": "the of and to in is that it was for on with as by be this are from at his which "
               "not have or an but they were their one",
    "Nederlands": "de het een van en in is dat op te zijn voor met die niet aan er als ook maar "
                  "door bij om nog wordt naar",
    "Deutsch": "der die und das ist nicht zu den mit von sich des auf ein eine dem im für es auch "
               "als wird bei werden aus",
    "Français": "le la les de des et est un une du en que qui dans pour pas sur au par ce sont "
                "avec il plus",
}
_ARABIC_SCRIPT = {
    "Arapça": "في من على إلى الى عن التي الذي الذين هذا هذه ذلك تلك كان كانت قال أن ان لا ما وهو وهي "
              "ثم قد كل بن الله عليه وسلم إن إذا لم لن حتى بعد قبل بين أو أي غير",
    "Osmanlıca": "بر ایله ايله ایچون ايچون ایچین اولان اولدیغی اولور ایدی ايدى دخی دخى ایسه اوله اولمق "
                 "حقنده طرفندن کبی كبى دها اولنور اولنان ایدر ایتمک ایتمك اوزره بو شو دیو ایچنده "
                 "بیک کندی هپ دکل دكل اولوب اولدی ایتدی اولنمش ایدن دیمک ایدرک ایکن ایمش اولمش "
                 "اولدقده نه کیم ایچره ایله‌ ابتدا لکن بیله",
    "Farsça": "است این که را از با برای می آن بود شد یک هم تا خود کرد نیز اند های نمی باشد",
}
# Türkçe ekler: -ler/-lar, mastar -mek/-mak, -dır (Arapça'da bu kalıplar nadirdir)
_OTTOMAN_SUFFIX = re.compile(r"[\u0600-\u06FF]{2,}(?:لر|لری|لرى|لرک|لرك|لرینی|لرده|لردن|لرینه|مق|مك|مک|دیر|دير)$")
_LATIN_SETS = {k: set(words(v)) for k, v in _LATIN.items()}
_AR_SETS = {k: set(v.split()) for k, v in _ARABIC_SCRIPT.items()}


def detect_text_language(text):
    """(dil, güven 0..1) — metin yetersizse (None, 0)."""
    sample = text[:6000]
    letters = [c for c in sample if c.isalpha()]
    if len(letters) < 150:
        return None, 0.0
    ar_ratio = sum(1 for c in letters if ARABIC_RE.match(c)) / len(letters)
    if ar_ratio > 0.5:
        toks = re.findall(r"[؀-ۿ]+", sample)
        scores = {k: sum(1 for t in toks if t in s) for k, s in _AR_SETS.items()}
        # Osmanlıca/Farsça'ya özgü harfler
        scores["Osmanlıca"] += len(re.findall(r"[ڭ]", sample)) * 3
        scores["Osmanlıca"] += sum(1 for t in toks if _OTTOMAN_SUFFIX.match(t))
        if re.search(r"[پچژگ]", sample):
            scores["Farsça"] += 1
            scores["Osmanlıca"] += 1
    else:
        toks = words(sample)
        scores = {k: sum(1 for t in toks if t in s) for k, s in _LATIN_SETS.items()}
        scores["Türkçe"] += 3 * len(re.findall(r"[ğış]", sample.lower()))
    total = sum(scores.values()) or 1
    best = max(scores, key=scores.get)
    if scores[best] < 5:
        return None, 0.0
    return best, round(scores[best] / total, 2)


# ---------- metin katmanı kalitesi ----------
_ALL_STOP = set().union(*_LATIN_SETS.values())


def text_layer_ok(text):
    """PDF'in gömülü metni gerçek bir dil mi, yoksa yanlış dilde yapılmış OCR'ın çöpü mü?
    (archive.org gibi kaynaklardaki Osmanlıca/Arapça taramalar İngilizce OCR'la gelir.)"""
    sample = (text or "")[:8000]
    letters = [c for c in sample if c.isalpha()]
    if len(letters) < 200:
        return False
    if sum(1 for c in letters if ARABIC_RE.match(c)) / len(letters) > 0.5:
        return True  # gerçek Arap harfli metin katmanı
    toks = words(sample)
    if len(toks) < 40:
        return False
    hits = sum(1 for t in toks if t in _ALL_STOP)
    avg = sum(len(t) for t in toks) / len(toks)
    # gerçek metinde bağlaç/edat oranı ~%10-40; çöp OCR'da ~%0-3 ve çok kısa/çok uzun "kelimeler"
    return hits / len(toks) >= 0.04 and 2.5 <= avg <= 12
