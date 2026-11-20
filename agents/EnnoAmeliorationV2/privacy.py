"""Data protection, layer 2: identifying client data never reaches the model.

Each conversation keeps a list of confidential terms (company, people, products,
identifiers…). Before a request leaves the server, every term is replaced by a stable
token such as [ENTREPRISE_1]; the model's answers and tool arguments are restored
before anything is stored or shown. The technical content stays readable, so the
analysis keeps its quality. Images, logos, headers, footers and file properties are
never sent at all: cir_document reads the body text only.
"""
from __future__ import annotations

import re
import zipfile
from io import BytesIO
from pathlib import Path

KINDS = {"entreprise": "ENTREPRISE", "personne": "PERSONNE", "produit": "PRODUIT", "projet": "PROJET",
         "email": "EMAIL", "telephone": "TELEPHONE", "siret": "SIRET", "iban": "IBAN", "url": "URL",
         "terme": "TERME"}
TOKEN = re.compile(r"\[(?:%s)_\d+\]" % "|".join(KINDS.values()))
UPPER = "A-ZÀÂÄÇÉÈÊËÎÏÔÖÙÛÜ"
NAME = rf"[{UPPER}][\w'’-]+(?:\s+[{UPPER}][\w'’-]+){{0,2}}"
IDENTIFIERS = (
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("telephone", re.compile(r"(?<![\d+])(?:\+33\s?|0)[1-9](?:[\s.-]?\d{2}){4}(?!\d)")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,3})?\b")),
    ("url", re.compile(r"https?://[^\s<>\"')\]]+")),
)
COMPANY_NUMBER = re.compile(r"\b(?:SIRET|SIREN)\b\D{0,15}(\d(?:[\s.]?\d){8,13})", re.I)
PERSON = re.compile(rf"\b(?:M\.|Mme|Mlle|Monsieur|Madame|Dr\.?|Docteur|Pr\.?|Professeur)\s+({NAME})")
ROLE = re.compile(rf"\b(?:Responsable|Chef de projet|Directeur|Directrice|Ingénieur|Ingénieure|Doctorant|Doctorante)"
                  rf"(?:\s+(?:scientifique|technique|R&D|de recherche))?\s*:\s*({NAME})")
# Scientific references stay readable: they are public and useful to the analysis.
PUBLIC_HOSTS = ("doi.org", "arxiv.org", "openalex.org", "semanticscholar.org", "crossref.org", "acm.org",
                "ieee.org", "springer.com", "sciencedirect.com", "hal.science", "archives-ouvertes.fr", "wikipedia.org")
GENERIC = {"cir", "cii", "jei", "ds", "dt", "vf", "final", "finale", "dossier", "justificatif", "technique",
           "scientifique", "rapport", "version", "draft", "brouillon", "copie", "amélioré", "ameliore", "annexe",
           "projet", "fiche", "descriptive", "doc", "docx", "pdf", "rd", "r&d"}


def normalize(value):
    return re.sub(r"[\s\-]+", " ", value.casefold()).strip()


def _pattern(value):
    words = [re.escape(w) for w in re.split(r"[\s\-]+", value.strip()) if w]
    return r"(?<![\w])" + r"[\s\-]+".join(words) + r"(?![\w])"


class Privacy:
    """Masks the registered terms of one conversation and restores them."""

    def __init__(self, terms):
        self.by_token = {t["token"]: t["value"] for t in terms}
        self.tokens = {normalize(t["value"]): t["token"] for t in terms}
        ordered = sorted(terms, key=lambda t: -len(t["value"]))
        self.pattern = re.compile("|".join(_pattern(t["value"]) for t in ordered), re.I) if ordered else None

    def mask(self, text):
        if not self.pattern or not text:
            return text
        return self.pattern.sub(lambda m: self.tokens.get(normalize(m.group()), m.group()), text)

    def unmask(self, text):
        return TOKEN.sub(lambda m: self.by_token.get(m.group(), m.group()), text) if text else text

    def unmask_json(self, value):
        if isinstance(value, str):
            return self.unmask(value)
        if isinstance(value, list):
            return [self.unmask_json(v) for v in value]
        if isinstance(value, dict):
            return {k: self.unmask_json(v) for k, v in value.items()}
        return value

    def mask_items(self, items):
        masked = []
        for item in items:
            if isinstance(item.get("content"), str):
                item = {**item, "content": self.mask(item["content"])}
            if item.get("type") == "function_call_output":
                item = {**item, "output": self.mask(item["output"])}
            masked.append(item)
        return masked

    def secret_in(self, text):
        """A registered term, or a token, appears in text meant for an outside service."""
        return bool(TOKEN.search(text) or (self.pattern and self.pattern.search(text)))

    def unknown_tokens(self, text):
        return [t for t in dict.fromkeys(TOKEN.findall(text or "")) if t not in self.by_token]


def identifiers(text):
    """Structured identifiers, reliable enough to mask without asking."""
    found = [(kind, m.group().rstrip(".,;:")) for kind, rule in IDENTIFIERS for m in rule.finditer(text)]
    found = [(k, v) for k, v in found if not (k == "url" and any(h in v.lower() for h in PUBLIC_HOSTS))]
    found += [("siret", m.group(1)) for m in COMPANY_NUMBER.finditer(text)]
    found += [("personne", m.group(1)) for rule in (PERSON, ROLE) for m in rule.finditer(text)]
    return found


def detect(paragraphs, filename="", data=None):
    """Terms to mask in an uploaded CIR: identifiers, file metadata and file name."""
    text = "\n".join(p["text"] for p in paragraphs)
    found = identifiers(text) + _metadata(data, text) + _filename(filename, text)
    unique = {}
    for kind, value in found:
        value = " ".join(value.split())
        if len(value) >= 3 and normalize(value) not in unique:
            unique[normalize(value)] = (kind, value)
    return list(unique.values())


def _metadata(data, text):
    """Author and company recorded in a Word file, when the body text names them."""
    if not data or not zipfile.is_zipfile(BytesIO(data)):
        return []
    with zipfile.ZipFile(BytesIO(data)) as archive:
        names = set(archive.namelist())
        xml = "".join(archive.read(n).decode("utf-8", "ignore") for n in ("docProps/core.xml", "docProps/app.xml") if n in names)
    found = [("personne", v) for v in re.findall(r"<(?:dc:creator|cp:lastModifiedBy)>([^<]+)<", xml)]
    found += [("entreprise", v) for v in re.findall(r"<Company>([^<]+)<", xml)]
    return [(k, v.strip()) for k, v in found if v.strip() and re.search(_pattern(v), text, re.I)]


def _filename(filename, text):
    """Client and project names in a file name such as CLIENT-DS_DT-2024_PROJET-X_VF.docx.

    A name counts only if the body text writes it as a proper noun (capitalised every
    time), so ordinary words such as « code » are never masked.
    """
    stem = re.sub(r"\s*\(\d+\)$", "", Path(filename).stem)
    # A company domain in an e-mail or a URL is lowercase by nature: it says nothing about the word.
    text = IDENTIFIERS[3][1].sub(" ", IDENTIFIERS[0][1].sub(" ", text))
    found = []
    for segment in re.split(r"[_\s]+", stem):
        words = [w for w in segment.split("-") if w]
        for candidate in ([" ".join(words)] if len(words) > 1 else []) + words:
            plain = candidate.replace(" ", "")
            if candidate.casefold() in GENERIC or plain.isdigit() or re.fullmatch(r"v\d+", plain, re.I):
                continue
            if len(plain) < (4 if " " not in candidate else 3) or any(w.casefold() in GENERIC for w in candidate.split()):
                continue
            matches = [m.group() for m in re.finditer(_pattern(candidate), text, re.I)]
            if matches and all(m != m.lower() for m in matches):
                found.append(("entreprise" if not found else "projet", candidate))
    return found
