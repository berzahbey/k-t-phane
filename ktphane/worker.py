"""Tek bir kitabın içeriğini ayrı bir süreçte okur.

Ana uygulama bunu zaman aşımıyla çağırır: bozuk veya devasa bir PDF takılırsa yalnızca bu süreç
öldürülür, tarama ve arayüz etkilenmez.

    python -m ktphane.worker <yol> <uzantı>   ->  stdout'a JSON
"""
import json
import sys
import warnings


def run(path, ext):
    warnings.filterwarnings("ignore")
    from . import content
    out = {"text": "", "toc": [], "ocr": False}
    if ext == "pdf":
        from pypdf import PdfReader
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return {**out, "encrypted": True}
        try:
            info = reader.metadata or {}
            out["meta_title"] = str(info.get("/Title") or "").strip()
            out["meta_author"] = str(info.get("/Author") or "").strip()
        except Exception:
            pass
        out["pages"] = len(reader.pages)
        first = ""
        for i in range(min(3, out["pages"])):
            try:
                first += reader.pages[i].extract_text() or ""
            except Exception:
                pass
            if len(first.strip()) >= 80:
                break
        out["needs_ocr"] = len(first.strip()) < 80
        text = content.pdf_text(path) if not out["needs_ocr"] else ""
        if len(text.strip()) < 300:
            text = content.pdf_ocr(path)
            out["ocr"] = True
        out["text"] = text
    else:
        out.update(content.extract({"ext": ext}, path))
    return out


if __name__ == "__main__":
    try:
        res = run(sys.argv[1], sys.argv[2])
    except Exception as e:  # bozuk dosya
        res = {"error": f"{type(e).__name__}: {e}"[:300]}
    sys.stdout.write(json.dumps(res, ensure_ascii=False))
