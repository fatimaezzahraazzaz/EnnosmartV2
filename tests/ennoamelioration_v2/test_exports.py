"""Word with tracked changes, the pre-assessment report and readable citations."""
from io import BytesIO

from docx import Document
from docx.oxml.ns import qn

from agents.EnnoAmeliorationV2 import cir_document, report
from tests.ennoamelioration_v2.doubles import ARTICLE, ARTICLE_ID, word_cir

PROPOSAL = {"number": 1, "paragraph_ids": ["P4", "P5", "P6"], "refs": [ARTICLE_ID],
            "proposed": f"Verrou 1 : dérive thermique\nAucune méthode ne compense la dérive [{ARTICLE_ID}].\nHypothèse testée en 2024."}


def cir():
    data = word_cir()
    return {**cir_document.read(data, "cir.docx"), "name": "cir.docx", "data": data}


def accept_all(data):
    """What Word does with « Accepter toutes les modifications »."""
    word = Document(BytesIO(data))
    body = word.element.body
    for p in list(body.iter(qn("w:p"))):
        mark = p.find(f"{qn('w:pPr')}/{qn('w:rPr')}/{qn('w:del')}")
        for deleted in p.findall(f".//{qn('w:del')}"):
            deleted.getparent().remove(deleted)
        for inserted in p.findall(f".//{qn('w:ins')}"):
            parent = inserted.getparent()
            for child in list(inserted):
                inserted.addprevious(child)
            parent.remove(inserted)
        if mark is not None and not p.findall(qn("w:r")):
            p.getparent().remove(p)
    return [(p.style.name, p.text) for p in word.paragraphs]


def test_tracked_word_shows_old_and_new_text_and_accepting_gives_the_clean_version():
    document = cir()
    tracked = cir_document.improved_docx(document, [PROPOSAL], [ARTICLE], tracked=True)
    clean = cir_document.improved_docx(document, [PROPOSAL], [ARTICLE])
    xml = Document(BytesIO(tracked)).element.xml
    assert 'w:author="EnnoAmélioration"' in xml
    assert "<w:delText>Le budget était limité.</w:delText>" in xml
    assert xml.count("<w:ins ") >= 4 and xml.count("<w:del ") >= 4
    assert accept_all(tracked) == [(p.style.name, p.text) for p in Document(BytesIO(clean)).paragraphs]
    assert "Aucune méthode ne compense la dérive (Lopez et al., 2023)." in [p.text for p in Document(BytesIO(clean)).paragraphs]


def test_tracked_rebuild_for_text_uploads():
    data = b"# Titre\n\nAncien texte.\n\nAutre texte."
    document = {**cir_document.read(data, "cir.txt"), "name": "cir.txt", "data": data}
    proposal = {"number": 1, "paragraph_ids": ["P2"], "refs": [], "proposed": "Nouveau texte."}
    tracked = cir_document.improved_docx(document, [proposal], [], tracked=True)
    xml = Document(BytesIO(tracked)).element.xml
    assert "<w:delText>Ancien texte.</w:delText>" in xml and "Nouveau texte." in xml
    assert accept_all(tracked) == [("Heading 1", "Titre"), ("Normal", "Nouveau texte."), ("Normal", "Autre texte.")]


def test_citation_labels():
    assert cir_document.citation_label(ARTICLE) == "Lopez et al., 2023"
    assert cir_document.citation_label({"authors": ["Earl T. Barr"], "year": None}) == "Barr, s.d."
    text = f"Voir [{ARTICLE_ID}] et A_ffffffffffffffff."
    assert cir_document.cite(text, [ARTICLE]) == "Voir (Lopez et al., 2023) et A_ffffffffffffffff."


def test_pre_assessment_report_lists_risks_by_priority_with_readable_citations():
    assessment = {"overall_risk": "élevé", "summary": f"Antériorité [{ARTICLE_ID}] à discuter.", "sections": [
        {"title": "Contexte", "first_id": "P1", "last_id": "P3", "risk": "moyen", "findings": "Objectif flou.",
         "expert_questions": ["Quel objectif de connaissance ?"], "to_prepare": []},
        {"title": "Verrou 1", "first_id": "P4", "last_id": "P4", "risk": "élevé", "findings": "Pas d'incertitude.",
         "expert_questions": [], "to_prepare": ["Essais datés"]}]}
    word = Document(BytesIO(report.assessment_docx(assessment, [ARTICLE], "cir.docx")))
    texts = [p.text for p in word.paragraphs]
    assert texts[0] == "Pré-expertise du dossier CIR" and "Risque global : élevé" in texts
    assert "Antériorité (Lopez et al., 2023) à discuter." in texts
    rows = [[c.text for c in row.cells] for row in word.tables[0].rows]
    assert rows == [["Partie", "Paragraphes", "Risque"], ["Verrou 1", "P4", "élevé"], ["Contexte", "P1–P3", "moyen"]]
    assert 'w:fill="F8D7DA"' in word.tables[0]._tbl.xml
    assert texts.index("Verrou 1 — risque élevé") < texts.index("Contexte — risque moyen")
    assert "Essais datés" in texts and "Quel objectif de connaissance ?" in texts
