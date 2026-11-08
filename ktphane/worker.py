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
        try:
            reader = PdfReader(path, strict=False)
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception:
                    return {**out, "encrypted": True}
            len(reader.pages)
        except Exception as e:
            return {"error": f"PDF açılamadı: {type(e).__name__}: {e}"[:300], "open_failed": True}
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
        out["tq"] = True  # metin kalitesi kontrol edildi
        text = ""
        if not out["needs_ocr"]:
            try:
                text = content.pdf_text(path)
            except Exception as e:
                out["warning"] = f"metin okunamadı: {e}"[:200]
            if not content.text_layer_ok(text):
                # gömülü metin anlamsız (yanlış dilde OCR) -> aslında taranmış kitap
                out["needs_ocr"] = True
                out["bad_text_layer"] = True
                text = ""
        if len(text.strip()) < 300:
            try:
                text = content.pdf_ocr(path)
                out["ocr"] = True
            except Exception as e:
                out["warning"] = f"OCR yapılamadı: {e}"[:200]
        out["text"] = text
    else:
        out.update(content.extract({"ext": ext}, path))
    return out


if __name__ == "__main__":
    try:
        if len(sys.argv) > 3 and sys.argv[3] == "stats":
            warnings.filterwarnings("ignore")
            from . import content
            res = content.book_stats(sys.argv[2], sys.argv[1])
        elif len(sys.argv) > 3 and sys.argv[3] == "mid":
            warnings.filterwarnings("ignore")
            from . import content
            res = {"mid": content.mid_text(sys.argv[2], sys.argv[1])}
        else:
            res = run(sys.argv[1], sys.argv[2])
    except Exception as e:  # bozuk dosya
        res = {"error": f"{type(e).__name__}: {e}"[:300]}
    sys.stdout.write(json.dumps(res, ensure_ascii=False))
