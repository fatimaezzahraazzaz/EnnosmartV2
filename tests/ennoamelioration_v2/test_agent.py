import json

import pytest

from agents.EnnoAmeliorationV2 import cir_document
from agents.EnnoAmeliorationV2.agent import LAST_STEP, Agent
from agents.EnnoAmeliorationV2.store import Busy, Store
from tests.ennoamelioration_v2.doubles import ARTICLE, ARTICLE_ID, FakeModel, FakeScholar, call, message, reply, word_cir

PROPOSAL = {"paragraph_ids": ["P5", "P6"], "original": "",
            "proposed": "Verrou : aucune méthode publiée ne compense la dérive sous 0,1 °/h (Lopez et al., 2023).",
            "explanation": "Le verrou devient une incertitude scientifique.", "reference_ids": [ARTICLE_ID]}


def setup(config, model, *, document=None, text="Renforce les verrous.", mode="amelioration"):
    store = Store(config.data_dir)
    sid = store.create(1, "Test", mode)["id"]
    if document:
        parsed = cir_document.read(document, "cir.docx")
        store.set_document(sid, "cir.docx", parsed["kind"], document, parsed["paragraphs"])
    store.add_message(sid, "user", text)
    rid = store.start_run(sid)["id"]
    return store, sid, rid, Agent(store, config, model, FakeScholar())


def test_search_then_proposal_then_explained_answer(config):
    model = FakeModel(
        reply(call("search_articles", {"query": "MEMS gyroscope thermal drift compensation"})),
        reply(call("propose_revision", PROPOSAL)),
        reply(message(f"Le verrou [P5] décrit un objectif, pas une incertitude [{ARTICLE_ID}; A_ffffffffffffffff].")))
    store, sid, rid, agent = setup(config, model, document=word_cir())
    run = agent.run(sid, rid)

    assert run["status"] == "completed" and run["usage"]["estimated_cost_usd"] > 0
    first = model.payloads[0]
    assert first["store"] is False and first["instructions"].startswith("Tu es EnnoAmélioration")
    # The whole CIR is in the context: no reading tool to call again.
    assert [t["name"] for t in first["tools"]] == ["search_articles", "get_article", "propose_revision", "decide_revisions"]
    assert "Texte complet ci-dessous" in first["input"][0]["content"]
    assert "CIR déposé" in first["input"][0]["content"] and '"id": "P5"' in first["input"][0]["content"]
    assert first["input"][-1] == {"role": "user", "content": "Renforce les verrous."}
    [revision] = store.revisions(sid)
    assert revision["paragraph_ids"] == ["P5", "P6"] and revision["status"] == "proposed"
    assert revision["original"] == "Verrou : il fallait développer un module de compensation en six mois.\nLe budget était limité."
    answer = store.messages(sid)[-1]
    assert f"[{ARTICLE_ID}]" in answer["content"] and "[référence non vérifiée]" in answer["content"]
    assert answer["extra"]["references"] == [ARTICLE] and answer["extra"]["revision_ids"] == [revision["id"]]
    assert answer["extra"]["warnings"] == ["Une référence inconnue a été retirée de la réponse."]
    assert [e["label"] for e in run["events"]][:2] == ["Lecture de la demande", "Recherche d'articles scientifiques"]


def test_invalid_proposals_are_explained_to_the_model_and_not_saved(config):
    bad = [
        ("c1", {**PROPOSAL, "paragraph_ids": ["P5", "P7"]}),
        ("c2", {**PROPOSAL, "paragraph_ids": ["P3"], "reference_ids": []}),
        ("c3", {**PROPOSAL, "reference_ids": ["A_ffffffffffffffff"]}),
        ("c4", {**PROPOSAL, "paragraph_ids": [], "original": "", "reference_ids": []}),
        ("c5", {"paragraph_ids": []}),
    ]
    model = FakeModel(reply(*[call("propose_revision", args, cid) for cid, args in bad]), reply(message("Je corrige.")))
    store, sid, rid, agent = setup(config, model, document=word_cir())
    agent.run(sid, rid)
    errors = [output["error"] for output in model.tool_outputs(1)]
    assert errors[0].startswith("Les paragraphes remplacés doivent être consécutifs")
    assert errors[1].startswith("Un tableau ne se réécrit pas")
    assert errors[2].startswith("Références inconnues : A_ffffffffffffffff")
    assert errors[3].startswith("Indique les paragraphes du CIR déposé")
    assert errors[4].startswith("Arguments invalides")
    assert store.revisions(sid) == []


def test_pasted_paragraph_can_be_proposed_and_accepted_on_request(config):
    text = "Améliore : notre solution innovante révolutionne la mesure."
    model = FakeModel(
        reply(call("propose_revision", {"paragraph_ids": [], "original": "notre solution innovante révolutionne la mesure.",
                                        "proposed": "Les travaux visent à établir…", "explanation": "Registre factuel.",
                                        "reference_ids": []})),
        lambda payload: reply(call("decide_revisions", {"revision_ids": [json.loads(payload["input"][-1]["output"])["revision_id"]],
                                                        "decision": "accepted"})),
        reply(message("C'est accepté.")))
    store, sid, rid, agent = setup(config, model, text=text)
    agent.run(sid, rid)
    [revision] = store.revisions(sid)
    assert revision["status"] == "accepted" and revision["original"].startswith("notre solution")
    assert not any("CIR déposé" in str(item.get("content")) for item in model.payloads[0]["input"])


def test_long_cir_is_read_in_pages(config):
    long = "\n\n".join(f"Paragraphe {n} " + "mesure " * 400 for n in range(30)).encode()
    model = FakeModel(reply(call("read_cir", {"first_id": "P2", "count": 80})), reply(message("Lu.")))
    store, sid, rid, agent = setup(config, model)
    parsed = cir_document.read(long, "cir.txt")
    store.set_document(sid, "cir.txt", parsed["kind"], long, parsed["paragraphs"])
    agent.run(sid, rid)
    assert "read_cir" in model.payloads[0]["input"][0]["content"] and '"paragraphs"' not in model.payloads[0]["input"][0]["content"]
    assert "read_cir" in [t["name"] for t in model.payloads[0]["tools"]]
    [page] = model.tool_outputs(1)
    assert page["paragraphs"][0]["id"] == "P2" and page["total"] == 30
    assert sum(len(p["text"]) for p in page["paragraphs"]) <= 24_000 and page["next_id"] == f"P{2 + len(page['paragraphs'])}"


def test_last_step_answers_without_tools(config):
    config = config.model_copy(update={"max_steps": 2})
    model = FakeModel(reply(call("search_articles", {"query": "thermal drift"})), reply(message("Voici ce que j'ai.")))
    store, sid, rid, agent = setup(config, model)
    assert agent.run(sid, rid)["status"] == "completed"
    assert model.payloads[1]["tools"] == [] and model.payloads[1]["input"][-1] == {"role": "developer", "content": LAST_STEP}


def test_cancel_budget_and_provider_failure_end_honestly(config):
    def cancel_then_call(payload):
        store.cancel(rid)
        return reply(call("search_articles", {"query": "thermal drift"}))
    model = FakeModel(cancel_then_call)
    store, sid, rid, agent = setup(config, model)
    run = agent.run(sid, rid)
    assert run["status"] == "cancelled" and store.messages(sid)[-1]["content"].startswith("Réponse arrêtée")

    poor = config.model_copy(update={"max_turn_usd": 0.0001})
    store, sid, rid, agent = setup(poor, FakeModel())
    assert agent.run(sid, rid)["status"] == "partial"

    def fail(payload):
        raise RuntimeError("https://user:secret@example.invalid")
    store, sid, rid, agent = setup(config, FakeModel(fail))
    run = agent.run(sid, rid)
    assert run["status"] == "failed" and "secret" not in run["answer"] and "indisponible" in run["answer"]


def test_store_keeps_one_accepted_text_per_paragraph_and_one_active_run(config):
    store, sid, rid, _ = setup(config, FakeModel(), document=word_cir())
    with pytest.raises(Busy):
        store.start_run(sid)
    old = store.add_revision(sid, rid, ["P5"], "a", "b", "c", [])
    new = store.add_revision(sid, rid, ["P5", "P6"], "a", "d", "c", [])
    store.decide(sid, old["id"], "accepted")
    store.decide(sid, new["id"], "accepted")
    assert [r["status"] for r in store.revisions(sid)] == ["replaced", "accepted"]
    parsed = cir_document.read(word_cir(), "cir.docx")
    store.set_document(sid, "v2.docx", "docx", word_cir(), parsed["paragraphs"])
    assert {r["status"] for r in store.revisions(sid)} == {"obsolete"}
    with pytest.raises(ValueError, match="version précédente"):
        store.decide(sid, new["id"], "accepted")


def test_a_second_server_process_does_not_interrupt_a_live_run(config):
    store, sid, rid, _ = setup(config, FakeModel())
    store.update_run(rid, status="running")
    Store(config.data_dir, stale_seconds=600).interrupt_unfinished()  # another worker starting up
    assert store.run(rid)["status"] == "running" and store.active_run(sid)["id"] == rid
    with store.connect() as db:
        db.execute("UPDATE runs SET updated = updated - 700 WHERE id=?", (rid,))
    assert store.active_run(sid) is None and store.run(rid)["status"] == "interrupted"
