"""Offline doubles: a scripted model and a fixed scholarly search. No network, no paid call."""
from __future__ import annotations

import json
from io import BytesIO

from docx import Document

ARTICLE_ID = "A_0123456789abcdef"
ARTICLE = {"id": ARTICLE_ID, "title": "Thermal drift compensation in MEMS gyroscopes", "authors": ["Ana Lopez", "Wei Chen"],
           "year": 2023, "doi": "10.1000/mems.2023.1", "venue": "Sensors", "abstract": "We show that drift remains above 0.1°/h.",
           "url": "https://doi.org/10.1000/mems.2023.1", "provider": "openalex"}


def call(name, arguments, call_id="call_1"):
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}


def message(text):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


def reply(*items, status="completed"):
    return {"status": status, "output": list(items), "usage": {"input_tokens": 1200, "output_tokens": 300}}


class FakeModel:
    """Plays a script of responses; a callable step receives the payload it answers."""
    def __init__(self, *script):
        self.script, self.payloads = list(script), []
        self.responses = self

    def create(self, **payload):
        self.payloads.append(payload)
        step = self.script.pop(0)
        return step(payload) if callable(step) else step

    def tool_outputs(self, index):
        return [json.loads(item["output"]) for item in self.payloads[index]["input"] if item.get("type") == "function_call_output"]


class FakeScholar:
    def __init__(self):
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        return [dict(ARTICLE)], []

    def lookup(self, doi):
        return dict(ARTICLE)


def word_cir():
    document = Document()
    document.add_heading("1. Contexte et objectifs", level=1)
    document.add_paragraph("Notre produit révolutionnaire va conquérir le marché des capteurs.")
    table = document.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Poste", "Montant"
    table.rows[1].cells[0].text, table.rows[1].cells[1].text = "Personnel", "120 k€"
    document.add_heading("2. Verrous scientifiques", level=1)
    styled = document.add_paragraph(style="Quote")
    styled.add_run("Verrou : ").bold = True
    styled.add_run("il fallait développer un module de compensation en six mois.")
    document.add_paragraph("Le budget était limité.")
    document.add_paragraph("Conclusion de la section.")
    output = BytesIO()
    document.save(output)
    return output.getvalue()
