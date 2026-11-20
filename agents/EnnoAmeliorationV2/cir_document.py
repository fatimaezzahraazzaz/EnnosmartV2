"""Read a CIR into numbered paragraphs, and write the improved Word file.

Paragraph ids (P1, P2, ...) are what the consultant, the model and the proposals
share. For a Word upload, each editable paragraph keeps its position in the
original file so the export rewrites it in place and keeps the original layout.
"""
from __future__ import annotations

import itertools
import re
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

EXTENSIONS = {".docx", ".pdf", ".txt", ".md"}
NUMBERED_TITLE = re.compile(r"^((?:\d+\.)*\d+\.?|[IVX]+\.|[A-H]\.)\s+\S")
TOC_STYLE = re.compile(r"^(?:toc|tm)\s*\d")
TOC_TITLES = {"table des matières", "sommaire", "table of contents", "contents"}


def read(data, filename):
    suffix = Path(filename).suffix.lower()
    if suffix not in EXTENSIONS:
        raise ValueError("Formats acceptés : Word DOCX, PDF, TXT et Markdown.")
    if suffix == ".docx":
        kind, blocks = "docx", list(_docx_blocks(data))
    elif suffix == ".pdf":
        kind, blocks = "pdf", list(_pdf_blocks(data))
    else:
        kind, blocks = "text", list(_text_blocks(data.decode("utf-8", errors="replace")))
    paragraphs, section = [], ""
    for number, (index, text, level, editable) in enumerate(blocks, 1):
        section = text if level else section
        paragraphs.append({"id": f"P{number}", "source_index": index, "text": text,
                           "level": level, "section": section, "editable": editable})
    if not paragraphs:
        raise ValueError("Aucun texte lisible dans ce document. Un PDF scanné doit d'abord passer par l'OCR.")
    return {"kind": kind, "paragraphs": paragraphs}


def outline(paragraphs):
    """Each heading with its paragraph range; always given to the model."""
    rows, current = [], None
    for p in paragraphs:
        if p["level"] or current is None:
            current = {"title": p["text"] if p["level"] else "(début du document)", "level": p["level"] or 1,
                       "from": p["id"], "to": p["id"], "words": 0}
            rows.append(current)
        current["to"] = p["id"]
        current["words"] += 0 if p["level"] else len(p["text"].split())
    return rows


def title_level(text):
    """A short numbered line without final punctuation reads as a heading."""
    match = NUMBERED_TITLE.match(text)
    if not match or len(text) > 120 or text.endswith((".", ":", ";", ",")):
        return 0
    return len([part for part in match[1].split(".") if part]) or 1


def _docx_blocks(data):
    from docx import Document
    from docx.table import Table

    try:
        document = Document(BytesIO(data))
    except Exception:
        raise ValueError("Le fichier Word est illisible ou protégé.") from None
    index = -1
    for block in document.iter_inner_content():
        if isinstance(block, Table):
            rows = [" | ".join(dict.fromkeys(c.text.strip() for c in row.cells)) for row in block.rows]
            text = " / ".join(r for r in rows if r.strip(" |"))
            if text:
                yield None, "Tableau : " + text[:3000], 0, False
            continue
        index += 1
        text = " ".join(block.text.split())
        style = (block.style.name if block.style is not None else "").casefold()
        # A table of contents repeats the headings with page numbers: navigation, not content.
        if text and not (TOC_STYLE.match(style) or text.casefold() in TOC_TITLES):
            heading = re.search(r"(?:heading|titre)\s*(\d)", style)
            level = int(heading[1]) if heading else 1 if style in {"title", "titre"} else title_level(text)
            yield index, text, level, True


def _pdf_blocks(data):
    import pymupdf

    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise ValueError("Le PDF est illisible ou protégé.") from None
    pages = []
    with document:
        for page in document:
            texts = [" ".join(b[4].split()) for b in page.get_text("blocks", sort=True) if b[6] == 0]
            pages.append([t for t in texts if t and not re.fullmatch(r"(?:page\s*)?\d{1,4}(?:\s*/\s*\d{1,4})?", t, re.I)])
    # Running headers and footers repeat on most pages; they are not CIR content.
    repeats = Counter(t for texts in pages for t in set(texts) if len(t) < 100)
    running = {t for t, n in repeats.items() if len(pages) >= 3 and n >= max(3, len(pages) // 2)}
    for texts in pages:
        for text in texts:
            if text not in running:
                yield None, text, title_level(text), True


def _text_blocks(text):
    for chunk in re.split(r"\n\s*\n", text):
        lines = [line.strip() for line in chunk.strip().splitlines() if line.strip()]
        while lines and (heading := re.match(r"^(#{1,6})\s+(.+)", lines[0])):
            yield None, heading[2].strip(), len(heading[1]), True
            lines = lines[1:]
        if lines:
            flat = " ".join(" ".join(lines).split())
            yield None, flat, title_level(flat), True


# Export ---------------------------------------------------------------------

AUTHOR = "EnnoAmélioration"
ARTICLE_CODE = re.compile(r"\[?(A_[a-f0-9]{16})\]?")


def lines(text):
    """Proposed text: one paragraph per line, Markdown emphasis removed."""
    return [re.sub(r"(\*\*|__)", "", line).strip() for line in text.splitlines() if line.strip()]


def citation_label(article):
    """« Tufano et al., 2020 »: how a consultant cites an article."""
    authors = [a for a in article.get("authors") or [] if a.strip()]
    name = authors[0].split()[-1] if authors else "Auteur non renseigné"
    return f"{name}{' et al.' if len(authors) > 1 else ''}, {article.get('year') or 's.d.'}"


def cite(text, articles):
    """Internal article codes become (Author et al., year); an unknown code stays visible."""
    known = {a["id"]: a for a in articles}
    return ARTICLE_CODE.sub(lambda m: f"({citation_label(known[m[1]])})" if m[1] in known else m[0], text)


def reference_line(article):
    authors = ", ".join(article.get("authors") or []) or "Auteur non renseigné"
    parts = [f"{authors} ({article.get('year') or 's.d.'}). {article.get('title', '').rstrip('.')}."]
    if article.get("venue"):
        parts.append(article["venue"] + ".")
    if article.get("doi"):
        parts.append("https://doi.org/" + article["doi"])
    elif article.get("url"):
        parts.append(article["url"])
    return " ".join(parts)


def improved_docx(document, accepted, articles, tracked=False):
    """The CIR with accepted proposals applied, plus the references they cite.

    Proposals on uploaded paragraphs replace them in place. Proposals on text
    pasted in the chat have no place in the file and are listed after it.
    tracked=True shows every change as a Word revision (« suivi des modifications »),
    so the client can accept or reject it in Word.
    """
    from docx import Document

    marks = Marks() if tracked and document is not None else None
    bound = [r for r in accepted if r["paragraph_ids"]]
    loose = [r for r in accepted if not r["paragraph_ids"]]
    added = []
    if document is None:
        word = Document()
        word.add_heading("EnnoAmélioration — passages améliorés", level=1)
    else:
        word = _apply(document, bound, articles, marks)
        if loose:
            added.append(word.add_heading("Autres passages améliorés", level=2))
    for revision in loose:
        added.append(word.add_heading(f"Proposition {revision['number']}", level=3))
        added += [word.add_paragraph(cite(line, articles)) for line in lines(revision["proposed"])]
    cited = {ref for r in accepted for ref in r["refs"]}
    references = [a for a in articles if a["id"] in cited]
    if references:
        added.append(word.add_heading("Références bibliographiques ajoutées", level=2))
        added += [word.add_paragraph(reference_line(article)) for article in references]
    for paragraph in added if marks else []:
        marks.insert(paragraph)
    output = BytesIO()
    word.save(output)
    return output.getvalue()


class Marks:
    """Word revision marks (« suivi des modifications »), signed EnnoAmélioration."""

    def __init__(self):
        self.ids = itertools.count(1)
        self.date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def insert(self, paragraph):
        self._wrap(paragraph, "w:ins", deleted=False)

    def delete(self, paragraph):
        self._wrap(paragraph, "w:del", deleted=True)

    def _mark(self, tag):
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        element = OxmlElement(tag)
        element.set(qn("w:id"), str(next(self.ids)))
        element.set(qn("w:author"), AUTHOR)
        element.set(qn("w:date"), self.date)
        return element

    def _wrap(self, paragraph, tag, deleted):
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        p = paragraph._p
        for run in p.findall(".//" + qn("w:r")):
            if run.getparent().tag in (qn("w:ins"), qn("w:del")):
                continue  # already a revision in the client's file
            if deleted:
                for text in run.findall(qn("w:t")):
                    text.tag = qn("w:delText")
            wrapper = self._mark(tag)
            run.addprevious(wrapper)
            wrapper.append(run)
        # The paragraph mark changes too: accepting merges the deleted paragraphs away.
        ppr = p.get_or_add_pPr()
        rpr = ppr.find(qn("w:rPr"))
        if rpr is None:
            rpr = OxmlElement("w:rPr")
            # In a paragraph's properties, rPr comes before sectPr and pPrChange.
            later = next((child for child in ppr if child.tag in (qn("w:sectPr"), qn("w:pPrChange"))), None)
            if later is not None:
                later.addprevious(rpr)
            else:
                ppr.append(rpr)
        rpr.insert(0, self._mark(tag))


def title_like(text):
    return len(text) <= 200 and not text.rstrip().endswith((".", ":", ";", "]"))


def layout(replaced, texts):
    """Give each proposed line the style of a replaced paragraph of the same kind.

    Title-like lines take the replaced headings in order, other lines the replaced
    body paragraphs in order, the last one being reused for extra lines. A heading
    in the middle of the passage that no title takes disappears with the passage.
    A replaced heading whose title the proposal leaves out is kept as it is.
    Returns [(line, style source or None for plain body text)] and the paragraphs replaced.
    """
    if replaced[0]["level"] and len(replaced) > 1 and texts and not title_like(texts[0]):
        replaced = replaced[1:]
    pools = {True: [p for p in replaced if p["level"]], False: [p for p in replaced if not p["level"]]}
    used, plan = {True: 0, False: 0}, []
    for text in texts or [""]:
        heading = bool(pools[True]) and title_like(text)
        pool = pools[heading]
        plan.append((text, pool[min(used[heading], len(pool) - 1)] if pool else None))
        used[heading] += 1
    return plan, replaced


def _apply(document, revisions, articles, marks=None):
    """Accepted proposals in the CIR; with marks, old text is kept as deleted and new text as inserted."""
    from docx import Document
    from docx.text.paragraph import Paragraph

    by_id = {p["id"]: p for p in document["paragraphs"]}
    plans = [([by_id[pid] for pid in r["paragraph_ids"]], [cite(line, articles) for line in lines(r["proposed"])])
             for r in revisions]
    if document["kind"] == "docx":
        word = Document(BytesIO(document["data"]))
        body = word.paragraphs
        for replaced, texts in plans:
            plan, removed = layout(replaced, texts)
            # New paragraphs copy their style source before any revision mark touches it,
            # and follow the passage they replace.
            copies = [deepcopy(body[(source or removed[0])["source_index"]]._p) for _, source in plan]
            anchor = body[removed[-1]["source_index"]]
            for (text, source), element in zip(plan, copies):
                target = Paragraph(element, anchor._parent)
                anchor._p.addnext(element)
                _set_text(target, text)
                if source is None:
                    target.style = word.styles["Normal"]
                if marks:
                    marks.insert(target)
                anchor = target
            for original in removed:
                paragraph = body[original["source_index"]]
                if marks:
                    marks.delete(paragraph)
                else:
                    paragraph._p.getparent().remove(paragraph._p)
        return word
    # A PDF or text upload has no layout to keep: rebuild a clean Word file.
    placed, skipped = {}, set()
    for replaced, texts in plans:
        plan, removed = layout(replaced, texts)
        old = [(o["text"], o["level"], "delete") for o in removed] if marks else []
        placed[removed[0]["id"]] = old + [(text, source["level"] if source else 0, "insert" if marks else "")
                                          for text, source in plan]
        skipped.update(o["id"] for o in removed[1:])
    word = Document()
    blocks = []
    for p in document["paragraphs"]:
        if p["id"] in placed:
            blocks += placed[p["id"]]
        elif p["id"] not in skipped:
            blocks.append((p["text"], p["level"], ""))
    for text, level, change in blocks:
        paragraph = word.add_heading(text, level=min(level, 4)) if level else word.add_paragraph(text)
        if change:
            getattr(marks, change)(paragraph)
    return word


def _set_text(paragraph, text):
    """New text in the paragraph, keeping its style and its main character format."""
    from docx.oxml.ns import qn

    p = paragraph._p
    runs = p.findall(".//" + qn("w:r"))
    main = max(runs, key=lambda r: len("".join(t.text or "" for t in r.iter(qn("w:t")))), default=None)
    rpr = main.find(qn("w:rPr")) if main is not None else None
    rpr = deepcopy(rpr) if rpr is not None else None
    for child in list(p):
        if child.tag != qn("w:pPr"):
            p.remove(child)
    run = paragraph.add_run(text)
    if rpr is not None:
        run._r.insert(0, rpr)
