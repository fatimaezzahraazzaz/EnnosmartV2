"""Word report of an expert-mode pre-assessment, to keep in the client file or share internally."""
from __future__ import annotations

from datetime import datetime
from io import BytesIO

from .cir_document import cite

ORDER = {"élevé": 0, "moyen": 1, "faible": 2}
SHADE = {"élevé": "F8D7DA", "moyen": "FFF3CD", "faible": "D4EDDA"}


def assessment_docx(assessment, articles, document_name=""):
    from docx import Document

    word = Document()
    word.add_heading("Pré-expertise du dossier CIR", level=1)
    meta = word.add_paragraph()
    meta.add_run(f"Document évalué : {document_name or 'passage du chat'}\n").italic = True
    meta.add_run(f"Évaluation EnnoAmélioration du {datetime.now():%d/%m/%Y}, mode expert du ministère.").italic = True
    verdict = word.add_paragraph()
    verdict.add_run(f"Risque global : {assessment['overall_risk']}").bold = True
    word.add_paragraph(cite(assessment["summary"], articles))

    sections = sorted(assessment["sections"], key=lambda s: ORDER.get(s["risk"], 3))
    word.add_heading("Synthèse des risques", level=2)
    table = word.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    for cell, title in zip(table.rows[0].cells, ("Partie", "Paragraphes", "Risque")):
        cell.text = title
        cell.paragraphs[0].runs[0].bold = True
    for section in sections:
        row = table.add_row().cells
        row[0].text = section["title"]
        row[1].text = _range(section)
        row[2].text = section["risk"]
        _shade(row[2], SHADE.get(section["risk"]))

    for section in sections:
        word.add_heading(f"{section['title']} — risque {section['risk']}", level=2)
        if _range(section):
            word.add_paragraph().add_run(f"Paragraphes {_range(section)}").italic = True
        word.add_paragraph(cite(section["findings"], articles))
        for title, items in (("Questions de l'expert", section["expert_questions"]), ("À préparer", section["to_prepare"])):
            if items:
                word.add_heading(title, level=3)
                for item in items:
                    word.add_paragraph(cite(item, articles), style="List Bullet")
    word.add_paragraph().add_run(
        "Évaluation produite par IA à partir du texte du dossier : à vérifier par le consultant avant tout usage.").italic = True
    output = BytesIO()
    word.save(output)
    return output.getvalue()


def _range(section):
    first, last = section.get("first_id"), section.get("last_id")
    return "" if not first else first if first == last else f"{first}–{last}"


def _shade(cell, color):
    if not color:
        return
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:fill"), color)
    cell._tc.get_or_add_tcPr().append(shading)
