from io import BytesIO

import pymupdf
import pytest
from docx import Document

from agents.EnnoAmeliorationV2 import cir_document
from tests.ennoamelioration_v2.doubles import ARTICLE, ARTICLE_ID, word_cir


def revision(number, paragraph_ids, proposed, refs=()):
    return {"number": number, "paragraph_ids": paragraph_ids, "proposed": proposed, "refs": list(refs)}


def test_word_cir_is_numbered_with_sections_and_read_only_tables():
    parsed = cir_document.read(word_cir(), "cir.docx")
    paragraphs = parsed["paragraphs"]
    assert parsed["kind"] == "docx"
    assert [p["id"] for p in paragraphs] == ["P1", "P2", "P3", "P4", "P5", "P6", "P7"]
    assert paragraphs[0]["level"] == 1 and paragraphs[0]["text"] == "1. Contexte et objectifs"
    assert paragraphs[2]["text"].startswith("Tableau : Poste | Montant") and not paragraphs[2]["editable"]
    assert paragraphs[4]["section"] == "2. Verrous scientifiques"
    outline = cir_document.outline(paragraphs)
    assert [(row["title"], row["from"], row["to"]) for row in outline] == [
        ("1. Contexte et objectifs", "P1", "P3"), ("2. Verrous scientifiques", "P4", "P7")]


def test_word_table_of_contents_is_not_read_as_headings():
    from docx.enum.style import WD_STYLE_TYPE

    document = Document()
    document.styles.add_style("toc 1", WD_STYLE_TYPE.PARAGRAPH)
    document.add_paragraph("Table des matières", style="Title")
    document.add_paragraph("1.3.2. Verrous et incertitudes 7", style="toc 1")
    document.add_heading("Verrous et incertitudes", level=2)
    document.add_paragraph("Texte du verrou.")
    output = BytesIO()
    document.save(output)
    paragraphs = cir_document.read(output.getvalue(), "cir.docx")["paragraphs"]
    assert [(p["id"], p["text"], p["level"], p["source_index"]) for p in paragraphs] == [
        ("P1", "Verrous et incertitudes", 2, 2), ("P2", "Texte du verrou.", 0, 3)]


def test_pdf_drops_running_headers_and_page_numbers():
    with pymupdf.open() as pdf:
        for number in range(3):
            page = pdf.new_page()
            page.insert_text((40, 30), "Société Exemple — Dossier CIR 2025", fontsize=8)
            page.insert_textbox(pymupdf.Rect(40, 80, 520, 200), f"Paragraphe de contenu numéro {number} sur la démarche.", fontsize=10)
            page.insert_text((290, 800), str(number + 1), fontsize=8)
        data = pdf.tobytes()
    texts = [p["text"] for p in cir_document.read(data, "cir.pdf")["paragraphs"]]
    assert texts == [f"Paragraphe de contenu numéro {n} sur la démarche." for n in range(3)]


def test_markdown_headings_and_unreadable_files():
    parsed = cir_document.read("# État de l'art\nLes travaux existants.\n\nSuite du texte.".encode(), "notes.md")
    assert [(p["text"], p["level"]) for p in parsed["paragraphs"]] == [
        ("État de l'art", 1), ("Les travaux existants.", 0), ("Suite du texte.", 0)]
    with pytest.raises(ValueError, match="Formats acceptés"):
        cir_document.read(b"x", "cir.xlsx")
    with pytest.raises(ValueError, match="Word est illisible"):
        cir_document.read(b"not a zip", "cir.docx")


def test_word_export_rewrites_in_place_keeps_style_and_adds_references():
    data = word_cir()
    document = {**cir_document.read(data, "cir.docx"), "name": "cir.docx", "data": data}
    accepted = [revision(1, ["P5", "P6"], "Verrou : aucune méthode publiée ne compense la dérive sous 0,1 °/h (Lopez et al., 2023).\n"
                                           "Hypothèse : un modèle thermique embarqué suffit.", [ARTICLE_ID]),
                revision(2, [], "Passage collé amélioré.")]
    word = Document(BytesIO(cir_document.improved_docx(document, accepted, [ARTICLE])))
    texts = [p.text for p in word.paragraphs]
    assert "Le budget était limité." not in texts
    assert texts[texts.index("2. Verrous scientifiques") + 1].startswith("Verrou : aucune méthode")
    rewritten = word.paragraphs[texts.index("2. Verrous scientifiques") + 1]
    assert rewritten.style.name == "Quote" and not rewritten.runs[0].bold  # main run format, not the bold lead
    assert texts[texts.index("2. Verrous scientifiques") + 2] == "Hypothèse : un modèle thermique embarqué suffit."
    assert "Conclusion de la section." in texts and len(word.tables) == 1
    assert "Autres passages améliorés" in texts and "Passage collé amélioré." in texts
    assert texts[-1].startswith("Ana Lopez, Wei Chen (2023). Thermal drift compensation") and "doi.org/10.1000" in texts[-1]


def test_replacing_a_heading_and_its_body_keeps_each_style():
    data = word_cir()
    document = {**cir_document.read(data, "cir.docx"), "name": "cir.docx", "data": data}
    proposal = "Verrou 1 : compensation thermique\nCorps A.\nCorps B.\nCorps C."
    word = Document(BytesIO(cir_document.improved_docx(document, [revision(1, ["P4", "P5", "P6"], proposal)], [])))
    rows = [(p.style.name, p.text) for p in word.paragraphs if p.text]
    start = rows.index(("Heading 1", "Verrou 1 : compensation thermique"))
    assert rows[start:start + 5] == [("Heading 1", "Verrou 1 : compensation thermique"), ("Quote", "Corps A."),
                                     ("Normal", "Corps B."), ("Normal", "Corps C."), ("Normal", "Conclusion de la section.")]

    without_title = revision(2, ["P4", "P5"], "Un paragraphe réécrit qui ne reprend pas le titre.")
    word = Document(BytesIO(cir_document.improved_docx(document, [without_title], [])))
    rows = [(p.style.name, p.text) for p in word.paragraphs if p.text]
    start = rows.index(("Heading 1", "2. Verrous scientifiques"))
    assert rows[start + 1] == ("Quote", "Un paragraphe réécrit qui ne reprend pas le titre.")


def test_a_heading_inside_the_replaced_passage_disappears_and_never_styles_body_text():
    text = "# Verrou 2\n\nAncien texte.\n\n# Verrou 3\n\nContrainte.\n\nSuite.".encode()
    document = {**cir_document.read(text, "cir.md"), "name": "cir.md", "data": b""}
    merged = revision(1, ["P1", "P2", "P3", "P4"], "Oracle de test\nPhrase un.\n[À COMPLÉTER : exemple]\n[À COMPLÉTER : essais]")
    word = Document(BytesIO(cir_document.improved_docx(document, [merged], [])))
    assert [(p.style.name, p.text) for p in word.paragraphs] == [
        ("Heading 1", "Oracle de test"), ("Normal", "Phrase un."), ("Normal", "[À COMPLÉTER : exemple]"),
        ("Normal", "[À COMPLÉTER : essais]"), ("Normal", "Suite.")]


def test_text_upload_and_chat_only_exports_build_a_new_word_file():
    document = {**cir_document.read(b"# Titre\n\nAncien texte.\n\nAutre texte.", "cir.txt"), "name": "cir.txt", "data": b""}
    word = Document(BytesIO(cir_document.improved_docx(document, [revision(1, ["P2"], "**Nouveau** texte.")], [])))
    assert [p.text for p in word.paragraphs] == ["Titre", "Nouveau texte.", "Autre texte."]
    word = Document(BytesIO(cir_document.improved_docx(document, [revision(1, ["P1", "P2"], "Nouveau titre\nA.\nB.")], [])))
    assert [(p.style.name, p.text) for p in word.paragraphs] == [
        ("Heading 1", "Nouveau titre"), ("Normal", "A."), ("Normal", "B."), ("Normal", "Autre texte.")]
    chat_only = Document(BytesIO(cir_document.improved_docx(None, [revision(3, [], "Version améliorée.")], [])))
    assert [p.text for p in chat_only.paragraphs] == ["EnnoAmélioration — passages améliorés", "Proposition 3", "Version améliorée."]
