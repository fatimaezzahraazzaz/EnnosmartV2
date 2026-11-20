"""The tools the model can call, each checked against the conversation's real data."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .privacy import Privacy
from .store import NotFound

READ_CHARS = 24_000
ARTICLE = r"A_[a-f0-9]{16}"
CITATION_GROUP = re.compile(rf"\[({ARTICLE}(?:\s*[,;]\s*{ARTICLE})*)\]")


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadCir(Arguments):
    first_id: str = Field(min_length=2, max_length=12, description="Premier paragraphe à lire, par exemple P1.")
    count: int = Field(ge=1, le=80, description="Nombre de paragraphes à lire.")


class SearchArticles(Arguments):
    query: str = Field(min_length=3, max_length=300,
                       description="Concepts scientifiques ; jamais le nom du client ni une information confidentielle.")


class GetArticle(Arguments):
    doi: str = Field(min_length=6, max_length=300)


class ProposeRevision(Arguments):
    paragraph_ids: list[str] = Field(max_length=30, description=(
        "Paragraphes consécutifs du CIR déposé que la proposition remplace ; liste vide pour un passage collé dans le chat."))
    original: str = Field(max_length=20_000, description="Passage d'origine collé dans le chat ; vide pour un CIR déposé.")
    proposed: str = Field(min_length=1, max_length=20_000, description=(
        "Texte final du dossier, affirmatif, sans Markdown, un paragraphe par ligne ; "
        "si un titre est remplacé, la première ligne est ce titre."))
    explanation: str = Field(min_length=1, max_length=2000, description="Ce qui a changé et pourquoi.")
    reference_ids: list[str] = Field(max_length=20, description="Identifiants A_ des articles cités dans proposed.")


class DecideRevisions(Arguments):
    revision_ids: list[str] = Field(min_length=1, max_length=30)
    decision: Literal["accepted", "rejected"]


Risk = Literal["faible", "moyen", "élevé"]


class SectionRisk(Arguments):
    title: str = Field(min_length=1, max_length=200, description="Partie évaluée, par exemple « Verrou 1 ».")
    first_id: str = Field(max_length=12, description="Premier paragraphe évalué (P…) ; vide pour un passage collé.")
    last_id: str = Field(max_length=12, description="Dernier paragraphe évalué (P…) ; vide pour un passage collé.")
    risk: Risk
    findings: str = Field(min_length=1, max_length=1500, description="Ce que l'expert contesterait, et pourquoi.")
    expert_questions: list[str] = Field(max_length=6, description="Questions que l'expert poserait.")
    to_prepare: list[str] = Field(max_length=6, description="Preuves ou précisions que le consultant doit préparer.")


class RecordAssessment(Arguments):
    overall_risk: Risk
    summary: str = Field(min_length=1, max_length=1500, description="Verdict global en quelques phrases.")
    sections: list[SectionRisk] = Field(min_length=1, max_length=20)


SCHEMAS = {
    "read_cir": (ReadCir, "Lire le texte exact de paragraphes consécutifs du CIR déposé, avec leurs identifiants et leur section."),
    "search_articles": (SearchArticles, "Chercher des publications scientifiques (OpenAlex, Semantic Scholar, Crossref, arXiv) "
                        "pour étayer l'état de l'art ou un verrou. Renvoie des notices et des résumés."),
    "get_article": (GetArticle, "Vérifier une publication à partir de son DOI et l'ajouter aux articles citables."),
    "propose_revision": (ProposeRevision, "Enregistrer une proposition de réécriture, affichée au consultant pour acceptation."),
    "decide_revisions": (DecideRevisions, "Accepter ou refuser des propositions, uniquement à la demande explicite du consultant."),
    "record_assessment": (RecordAssessment, "Enregistrer la pré-expertise : niveau de risque, griefs, questions de l'expert "
                          "et preuves à préparer pour chaque partie évaluée. Affichée comme un tableau des risques."),
}
LABELS = {"read_cir": "Lecture du CIR", "search_articles": "Recherche d'articles scientifiques",
          "get_article": "Vérification d'une référence", "propose_revision": "Rédaction d'une proposition",
          "decide_revisions": "Mise à jour des propositions", "record_assessment": "Évaluation des risques"}
# The expert judges; only the improvement mode rewrites the dossier.
MODE_TOOLS = {
    "amelioration": ("read_cir", "search_articles", "get_article", "propose_revision", "decide_revisions"),
    "expert": ("read_cir", "search_articles", "get_article", "record_assessment"),
}


def _strict(schema):
    """Strict function calling: every property required, no defaults, nested models included."""
    for part in [schema, *schema.get("$defs", {}).values()]:
        part["required"] = list(part["properties"])
        for value in part["properties"].values():
            value.pop("default", None)
    return schema


def definitions(names=None):
    return [{"type": "function", "name": name, "description": description,
             "parameters": _strict(model.model_json_schema()), "strict": True}
            for name, (model, description) in SCHEMAS.items() if names is None or name in names]


def normalize_citations(text):
    """[A_x; A_y] becomes [A_x] [A_y] so each reference is checked and linked."""
    return CITATION_GROUP.sub(lambda m: " ".join(f"[{ref.strip()}]" for ref in re.split(r"[,;]", m[1])), text)


def cited_ids(text):
    return list(dict.fromkeys(re.findall(rf"\[({ARTICLE})\]", normalize_citations(text))))


class Tools:
    def __init__(self, store, sid, run_id, scholar, config, allowed=tuple(SCHEMAS), privacy=None):
        self.store, self.sid, self.run_id = store, sid, run_id
        self.scholar, self.config, self.allowed = scholar, config, allowed
        self.privacy = privacy or Privacy([])
        self.searches = 0
        self.created, self.assessed = [], []

    def execute(self, name, arguments):
        if name not in self.allowed:
            raise ValueError("Outil indisponible dans ce mode de travail.")
        model = SCHEMAS[name][0]
        return getattr(self, name)(model.model_validate(arguments))

    def _paragraphs(self):
        document = self.store.document(self.sid)
        if not document:
            raise ValueError("Aucun CIR n'est déposé dans cette conversation.")
        return document["paragraphs"]

    def read_cir(self, args):
        paragraphs = self._paragraphs()
        positions = {p["id"]: i for i, p in enumerate(paragraphs)}
        if args.first_id not in positions:
            raise ValueError(f"Paragraphe inconnu. Le document va de P1 à P{len(paragraphs)}.")
        selected, size = [], 0
        for p in paragraphs[positions[args.first_id]:positions[args.first_id] + args.count]:
            if selected and size + len(p["text"]) > READ_CHARS:
                break
            size += len(p["text"])
            selected.append({"id": p["id"], "section": p["section"], "heading": bool(p["level"]),
                             "editable": p["editable"], "text": p["text"]})
        end = positions[selected[-1]["id"]] + 1
        return {"paragraphs": selected, "next_id": paragraphs[end]["id"] if end < len(paragraphs) else None,
                "total": len(paragraphs)}

    def search_articles(self, args):
        if self.privacy.secret_in(args.query):
            # Outside databases never receive the client's identity, not even as a token.
            raise ValueError("Recherche refusée : la requête contient une information confidentielle du client. "
                             "Utilise uniquement des concepts scientifiques et techniques.")
        if self.searches >= self.config.max_searches:
            raise ValueError("Limite de recherches atteinte pour cette réponse ; utilise les articles déjà trouvés.")
        self.searches += 1
        cards, errors = self.scholar.search(args.query)
        self.store.put_articles(self.sid, cards)
        return {"articles": cards, "unavailable_sources": [e["provider"] for e in errors],
                "notice": "Notices et résumés seulement : ne pas attribuer à un article plus que son résumé."}

    def get_article(self, args):
        card = self.scholar.lookup(args.doi)
        self.store.put_articles(self.sid, [card])
        return card

    def propose_revision(self, args):
        ids = list(dict.fromkeys(args.paragraph_ids))
        if ids:
            paragraphs = self._paragraphs()
            positions = {p["id"]: i for i, p in enumerate(paragraphs)}
            if unknown := [i for i in ids if i not in positions]:
                raise ValueError("Paragraphes inconnus : " + ", ".join(unknown))
            indexes = [positions[i] for i in ids]
            if indexes != list(range(indexes[0], indexes[0] + len(indexes))):
                raise ValueError("Les paragraphes remplacés doivent être consécutifs et dans l'ordre du document.")
            if any(not paragraphs[i]["editable"] for i in indexes):
                raise ValueError("Un tableau ne se réécrit pas ici : propose un texte pour les paragraphes voisins.")
            original = "\n".join(paragraphs[i]["text"] for i in indexes)
        else:
            original = args.original.strip()
            if not original:
                raise ValueError("Indique les paragraphes du CIR déposé, ou recopie le passage d'origine collé dans le chat.")
        known = {a["id"] for a in self.store.articles(self.sid)}
        if unknown := [ref for ref in args.reference_ids if ref not in known]:
            raise ValueError("Références inconnues : " + ", ".join(unknown)
                             + ". Cite seulement des articles trouvés avec search_articles ou get_article.")
        revision = self.store.add_revision(self.sid, self.run_id, ids, original, args.proposed.strip(),
                                           args.explanation.strip(), list(dict.fromkeys(args.reference_ids)))
        self.created.append(revision["id"])
        return {"revision_id": revision["id"], "number": revision["number"], "status": "proposed",
                "notice": "La proposition est affichée au consultant, qui l'accepte ou la refuse."}

    def decide_revisions(self, args):
        decided = []
        for rev in dict.fromkeys(args.revision_ids):
            try:
                revision = self.store.decide(self.sid, rev, args.decision)
            except NotFound:
                raise ValueError(f"Proposition inconnue : {rev}") from None
            decided.append({"id": revision["id"], "number": revision["number"], "status": revision["status"]})
        return {"revisions": decided}

    def record_assessment(self, args):
        ranges = [(s.first_id, s.last_id) for s in args.sections if s.first_id or s.last_id]
        if ranges:
            positions = {p["id"]: i for i, p in enumerate(self._paragraphs())}
            for first, last in ranges:
                if first not in positions or last not in positions or positions[first] > positions[last]:
                    raise ValueError(f"Plage de paragraphes invalide : {first}–{last}. Indique le premier puis le dernier paragraphe évalué.")
        assessment = self.store.add_assessment(self.sid, self.run_id, args.model_dump())
        self.assessed.append(assessment["id"])
        return {"assessment_id": assessment["id"], "notice": "Le tableau des risques est affiché au consultant."}
