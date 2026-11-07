"""Dosyaların içinden kitap bilgisi okuma (EPUB OPF, PDF info) ve kapak üretimi."""
import io
import logging
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET

log = logging.getLogger("ktphane.meta")

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}

JUNK_RE = re.compile(
    r"(\(?z-?lib(\.org)?\)?|\(?libgen(\.\w+)?\)?|\[?pdf\]?$|\[?epub\]?$|www\.[\w.-]+|"
    r"\bkitapyurdu\b|\bebook\b|\be-?kitap\b)", re.I)


def _text(el):
    return (el.text or "").strip() if el is not None else ""


def read_epub(path):
    """{'title','author','language','subjects','description','publisher','year','cover'} döndürür.
    'cover' zip içindeki kapak dosyasının yoludur (bayt değil)."""
    out = {}
    with zipfile.ZipFile(path) as z:
        try:
            container = ET.fromstring(z.read("META-INF/container.xml"))
            opf_path = container.find(".//c:rootfile", NS).get("full-path")
        except Exception:
            opf_path = next((n for n in z.namelist() if n.lower().endswith(".opf")), None)
        if not opf_path:
            return out
        opf = ET.fromstring(z.read(opf_path))
        md = opf.find("opf:metadata", NS)
        if md is None:
            md = opf.find(".//{*}metadata")
        if md is not None:
            out["title"] = _text(md.find("dc:title", NS))
            creators = [_text(c) for c in md.findall("dc:creator", NS) if _text(c)]
            out["author"] = ", ".join(creators[:3])
            out["language"] = _text(md.find("dc:language", NS))
            out["subjects"] = [_text(s) for s in md.findall("dc:subject", NS) if _text(s)][:10]
            desc = re.sub(r"<[^>]+>", " ", _text(md.find("dc:description", NS)))
            out["description"] = re.sub(r"\s+", " ", desc).strip()[:1500]
            out["publisher"] = _text(md.find("dc:publisher", NS))
            m = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", _text(md.find("dc:date", NS)))
            out["year"] = m.group(1) if m else ""

        # kapak: EPUB3 properties="cover-image" veya EPUB2 <meta name="cover">
        base = posixpath.dirname(opf_path)
        manifest = opf.find("opf:manifest", NS)
        items = manifest.findall("opf:item", NS) if manifest is not None else []
        cover_href = None
        for it in items:
            if "cover-image" in (it.get("properties") or ""):
                cover_href = it.get("href")
                break
        if not cover_href and md is not None:
            meta = next((m for m in md.iter() if m.get("name") == "cover"), None)
            if meta is not None:
                cid = meta.get("content")
                for it in items:
                    if it.get("id") == cid:
                        cover_href = it.get("href")
                        break
        if not cover_href:
            for it in items:
                if (it.get("media-type") or "").startswith("image/") and \
                        "cover" in ((it.get("id") or "") + (it.get("href") or "")).lower():
                    cover_href = it.get("href")
                    break
        if cover_href:
            out["cover"] = posixpath.normpath(posixpath.join(base, cover_href))
    return out


def read_pdf(path):
    """{'title','author','pages','needs_ocr'} döndürür."""
    from pypdf import PdfReader
    out = {}
    reader = PdfReader(path, strict=False)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            out["needs_ocr"] = None
            return out
    try:
        info = reader.metadata or {}
        out["title"] = str(info.get("/Title") or "").strip()
        out["author"] = str(info.get("/Author") or "").strip()
    except Exception:
        pass
    n = len(reader.pages)
    out["pages"] = n
    text = ""
    # Kapak/boş sayfaya takılmamak için ilk 3 sayfadan örnek al
    for i in range(min(3, n)):
        try:
            text += reader.pages[i].extract_text() or ""
        except Exception:
            pass
        if len(text.strip()) >= 80:
            break
    out["needs_ocr"] = len(text.strip()) < 80
    return out


def clean_meta_value(s):
    """PDF'lerdeki 'Microsoft Word - belge1.doc' gibi işe yaramaz değerleri ele."""
    s = (s or "").strip()
    if not s or len(s) < 2:
        return ""
    low = s.lower()
    bad = ("microsoft word", "untitled", "adsız", "adsiz", "isimsiz", "unknown", "administrator",
           "user", "kullanıcı", "pdf", "document", "belge", ".doc", ".indd", ".tmp", "owner")
    if any(low.startswith(b) or low.endswith(b) for b in bad) or low in bad:
        return ""
    return s


def make_cover(fmt, path, cover_member, dest, width=360):
    """Kapak görselini üretip dest'e JPEG olarak yazar. Başarılıysa True."""
    from PIL import Image
    img = None
    try:
        if fmt == "epub" and cover_member:
            with zipfile.ZipFile(path) as z:
                img = Image.open(io.BytesIO(z.read(cover_member)))
                img.load()
        elif fmt == "pdf":
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(path)
            try:
                page = pdf[0]
                w = page.get_width() or 600
                img = page.render(scale=min(2.0, width * 1.5 / w)).to_pil()
                page.close()
            finally:
                pdf.close()
    except Exception as e:  # bozuk dosya, şifreli PDF vb.
        log.info("Kapak üretilemedi %s: %s", path, e)
        return False
    if img is None:
        return False
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, "white")
        try:
            bg.paste(img, mask=img.convert("RGBA").split()[-1])
        except Exception:
            bg.paste(img.convert("RGB"))
        img = bg
    img.thumbnail((width, int(width * 1.6)))
    img.convert("RGB").save(dest, "JPEG", quality=82, optimize=True)
    return True
