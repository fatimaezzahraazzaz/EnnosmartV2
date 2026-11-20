"""Expert mode: the model judges the dossier like a ministry expert and never rewrites it."""
import sqlite3

from agents.EnnoAmeliorationV2.store import Store
from agents.EnnoAmeliorationV2.tools import definitions
from tests.ennoamelioration_v2.doubles import FakeModel, call, message, reply, word_cir
from tests.ennoamelioration_v2.test_agent import PROPOSAL, setup

SECTION = {"title": "Verrous scientifiques", "first_id": "P4", "last_id": "P7", "risk": "élevé",
           "findings": "Le verrou décrit un délai de six mois et un budget, pas une incertitude scientifique.",
           "expert_questions": ["Quelle connaissance manquait dans l'état de l'art ?"],
           "to_prepare": ["Essais et échecs datés de 2024"]}
ASSESSMENT = {"overall_risk": "élevé", "summary": "Le verrou principal n'est pas démontré.", "sections": [SECTION]}


def test_expert_mode_records_a_risk_table_with_expert_instructions_and_tools(config):
    model = FakeModel(reply(call("record_assessment", ASSESSMENT)),
                      reply(message("Verdict : risque élevé, surtout sur les verrous [P4].")))
    store, sid, rid, agent = setup(config, model, document=word_cir(), text="Évalue mon CIR.", mode="expert")
    assert agent.run(sid, rid)["status"] == "completed"

    first = model.payloads[0]
    assert "MODE DE TRAVAIL : EXPERT DU MINISTÈRE" in first["instructions"]
    assert "MODE DE TRAVAIL : AMÉLIORATION" not in first["instructions"]
    assert [t["name"] for t in first["tools"]] == ["search_articles", "get_article", "record_assessment"]
    [assessment] = store.assessments(sid)
    assert assessment["overall_risk"] == "élevé" and assessment["sections"][0]["first_id"] == "P4"
    answer = store.messages(sid)[-1]
    assert answer["extra"]["mode"] == "expert" and answer["extra"]["assessment_ids"] == [assessment["id"]]
    assert answer["extra"]["revision_ids"] == [] and store.revisions(sid) == []


def test_expert_mode_refuses_rewrites_and_invalid_ranges(config):
    bad_range = {**ASSESSMENT, "sections": [{**SECTION, "first_id": "P7", "last_id": "P4"}]}
    model = FakeModel(reply(call("propose_revision", PROPOSAL, "c1"), call("record_assessment", bad_range, "c2")),
                      reply(message("Je corrige.")))
    store, sid, rid, agent = setup(config, model, document=word_cir(), mode="expert")
    agent.run(sid, rid)
    rewrite, wrong_range = model.tool_outputs(1)
    assert rewrite["error"] == "Outil indisponible dans ce mode de travail."
    assert wrong_range["error"].startswith("Plage de paragraphes invalide : P7–P4")
    assert store.revisions(sid) == [] and store.assessments(sid) == []


def test_improvement_mode_sees_the_earlier_assessment_but_cannot_record_one(config):
    model = FakeModel(reply(message("Je commence par le verrou à risque élevé.")))
    store, sid, rid, agent = setup(config, model, document=word_cir(), text="Corrige les risques élevés.")
    store.add_assessment(sid, "T_old", ASSESSMENT)
    agent.run(sid, rid)
    payload = model.payloads[0]
    assert "record_assessment" not in [t["name"] for t in payload["tools"]]
    state = next(item["content"] for item in payload["input"] if "État de la conversation" in str(item.get("content")))
    assert '"risk": "élevé"' in state and '"first_id": "P4"' in state


def test_nested_tool_schema_is_strict():
    [tool] = definitions(["record_assessment"])
    section = tool["parameters"]["$defs"]["SectionRisk"]
    assert tool["strict"] is True and set(section["required"]) == set(section["properties"])
    assert section["additionalProperties"] is False


def test_conversations_created_before_the_expert_mode_default_to_improvement(tmp_path):
    with sqlite3.connect(tmp_path / "amelioration_v2.sqlite3") as db:
        db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, owner INTEGER NOT NULL, title TEXT NOT NULL, "
                   "created REAL NOT NULL, updated REAL NOT NULL)")
        db.execute("INSERT INTO sessions VALUES ('S_old', 1, 'Ancienne', 0, 0)")
    store = Store(tmp_path)
    assert store.owned(1, "S_old")["mode"] == "amelioration"
    store.set_mode("S_old", "expert")
    assert store.mode("S_old") == "expert"
