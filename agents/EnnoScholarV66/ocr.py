"""Local Tesseract via MuPDF: cached page text, never a second scientific LLM."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from functools import wraps
from pathlib import Path

PDF_LOCK = threading.RLock()


def serialized_pdf(function):
    @wraps(function)
    def call(*args, **kwargs):
        with PDF_LOCK:
            return function(*args, **kwargs)
    return call


def status(config):
    folder = config.ocr_data_dir or Path(os.getenv("TESSDATA_PREFIX") or config.data_dir / "tessdata")
    languages = config.ocr_languages.split("+")
    missing = [lang for lang in languages if not re.fullmatch(r"[a-z_]{3,12}", lang)
               or not (folder / (lang + ".traineddata")).is_file()]
    return {"enabled": config.ocr_enabled, "ready": config.ocr_enabled and not missing,
            "languages": config.ocr_languages, "missing_languages": missing, "data_dir": str(folder),
            "engine": "MuPDF/Tesseract", "notice": "L'OCR peut altérer chiffres et symboles ; vérifier les résultats importants dans l'original."}


@serialized_pdf
def page_text(page, config):
    info = status(config)
    if not info["ready"]:
        raise ValueError("OCR local non prêt : installer les données linguistiques avec python -m agents.EnnoScholarV66.setup_ocr.")
    # Bound the raster size for unusually large posters / engineering drawings.
    dpi = min(config.ocr_dpi, max(72, int(3500 * 72 / max(page.rect.width, page.rect.height))))
    textpage = page.get_textpage_ocr(language=config.ocr_languages, dpi=dpi, full=True, tessdata=info["data_dir"])
    return page.get_text(textpage=textpage, sort=True)


def install(config, languages=("eng", "fra")):
    from .transfer import download
    folder = Path(status(config)["data_dir"])
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for lang in languages:
        if not re.fullmatch(r"[a-z_]{3,12}", lang):
            raise ValueError("Code de langue Tesseract invalide.")
        path = folder / (lang + ".traineddata")
        url = f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/{lang}.traineddata"
        if not path.exists():
            data, _, _ = download(url, 20 * 1024 * 1024, cache_dir=folder / "downloads")
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
        manifest[lang] = {"source": url, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "license": "Apache-2.0"}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return status(config)
