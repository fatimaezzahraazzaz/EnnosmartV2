"""Layer 2 and 3 of data protection: nothing identifying the client reaches the model or outside services."""
import json
from io import BytesIO

from docx import Document

from agents.EnnoAmeliorationV2 import cir_document, privacy
from agents.EnnoAmeliorationV2.agent import Agent
from agents.EnnoAmeliorationV2.privacy import Privacy
from agents.EnnoAmeliorationV2.store import Store
from tests.ennoamelioration_v2.doubles import ARTICLE_ID, FakeModel, FakeScholar, call, message, reply

FILENAME = "NOVATEC-DS_DT-2024_NEO-CODE_VF_AD.docx"
SECRETS = ("NOVATEC", "Novatec", "Alice Martin", "alice.martin@novatec.example", "intranet.novatec.example", FILENAME)


def client_cir():
    document = Document()
    document.core_properties.author = "Alice Martin"
    document.core_properties.title = "NOVATEC DS confidentiel"
    document.sections[0].header.paragraphs[0].text = "Document confidentiel NOVATEC"
    document.add_heading("Verrous et incertitudes", level=1)
    document.add_paragraph("Le projet NEO CODE de NOVATEC étudie la génération de code de tests unitaires.")
    document.add_paragraph("Responsable scientifique : Alice Martin (alice.martin@novatec.example).")
    document.add_paragraph("Les traces sont sur https://intranet.novatec.example/neo-code et l'article https://doi.org/10.1000/x.")
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def test_mask_and_unmask_keep_tokens_stable_and_ordinary_words_intact():
    shield = Privacy([{"token": "[ENTREPRISE_1]", "value": "Novatec"}, {"token": "[PROJET_1]", "value": "NEO CODE"},
                      {"token": "[ENTREPRISE_2]", "value": "Novatec Group"}])
    text = "NOVATEC et Novatec Group lancent NEO-CODE ; le code reste. Novateco n'est pas visé."
    masked = shield.mask(text)
    assert masked == "[ENTREPRISE_1] et [ENTREPRISE_2] lancent [PROJET_1] ; le code reste. Novateco n'est pas visé."
    assert shield.unmask(masked) == "Novatec et Novatec Group lancent NEO CODE ; le code reste. Novateco n'est pas visé."
    assert json.loads(shield.mask(json.dumps({"t": "Novatec \"cité\""}))) == {"t": "[ENTREPRISE_1] \"cité\""}
    assert shield.secret_in("tests chez novatec") and shield.secret_in("[PERSONNE_9] tests")
    assert not shield.secret_in("unit test generation with LLMs")
    assert shield.unknown_tokens("[ENTREPRISE_1] et [PERSONNE_4]") == ["[PERSONNE_4]"]


def test_detection_finds_identifiers_names_and_client_from_file_name_only():
    data = client_cir()
    found = dict((value, kind) for kind, value in privacy.detect(cir_document.read(data, FILENAME)["paragraphs"], FILENAME, data))
    assert found["NOVATEC"] == "entreprise" and found["NEO CODE"] == "projet"
    assert found["Alice Martin"] == "personne" and found["alice.martin@novatec.example"] == "email"
    assert found["https://intranet.novatec.example/neo-code"] == "url"
    assert "CODE" not in found and "DS" not in found and not any("doi.org" in v for v in found)
    text = "Tél. 06 12 34 56 78, SIRET : 123 456 789 00012, M. Jean Dupont. Budget : 120 000 000 €."
    assert sorted(privacy.identifiers(text)) == [("personne", "Jean Dupont"), ("siret", "123 456 789 00012"),
                                                 ("telephone", "06 12 34 56 78")]


def test_the_model_never_receives_client_names_and_answers_come_back_restored(config):
    data = client_cir()
    store = Store(config.data_dir)
    sid = store.create(1, "Test")["id"]
    parsed = cir_document.read(data, FILENAME)
    store.set_document(sid, FILENAME, parsed["kind"], data, parsed["paragraphs"])
    store.add_privacy_terms(sid, privacy.detect(parsed["paragraphs"], FILENAME, data), "auto")
    store.add_message(sid, "user", "Que penses-tu du dossier Novatec ?")
    store.add_message(sid, "assistant", "Le dossier de NOVATEC manque de références.")
    store.add_revision(sid, "T_old", ["P2"], "x", "NOVATEC étudie la génération de tests.", "Pour Alice Martin.", [])
    store.add_message(sid, "user", "Renforce le verrou de NOVATEC.")
    rid = store.start_run(sid)["id"]
    model = FakeModel(
        reply(call("search_articles", {"query": "[ENTREPRISE_1] unit test generation"})),
        reply(call("propose_revision", {"paragraph_ids": ["P2"], "original": "", "reference_ids": [],
                                        "proposed": "Le projet [PROJET_1] de [ENTREPRISE_1] étudie l'oracle des tests.",
                                        "explanation": "Verrou recentré pour [PERSONNE_1]."})),
        reply(message("Le verrou de [ENTREPRISE_1] est maintenant une incertitude, à valider avec [PERSONNE_1] et [PERSONNE_8].")))
    scholar = FakeScholar()
    Agent(store, config, model, scholar).run(sid, rid)

    sent = json.dumps(model.payloads, ensure_ascii=False)
    assert "[ENTREPRISE_1]" in sent and "[PROJET_1]" in sent
    assert not [secret for secret in SECRETS if secret in sent]
    assert "Document confidentiel" not in sent and "DS confidentiel" not in sent  # header and file properties stay home
    assert scholar.queries == []
    assert model.tool_outputs(1)[0]["error"].startswith("Recherche refusée")
    revision = store.revisions(sid)[-1]
    assert revision["proposed"] == "Le projet NEO CODE de NOVATEC étudie l'oracle des tests."
    assert revision["explanation"] == "Verrou recentré pour Alice Martin."
    answer = store.messages(sid)[-1]
    assert answer["content"] == "Le verrou de NOVATEC est maintenant une incertitude, à valider avec Alice Martin et [PERSONNE_8]."
    assert any("repère de confidentialité inconnu" in w for w in answer["extra"]["warnings"])


def test_terms_keep_their_token_and_numbering(config):
    store = Store(config.data_dir)
    sid = store.create(1, "Test")["id"]
    first = store.add_privacy_terms(sid, [("entreprise", "Novatec"), ("produit", "Nova"), ("entreprise", "NOVATEC")], "auto")
    assert [t["token"] for t in first] == ["[ENTREPRISE_1]", "[PRODUIT_1]"]
    store.remove_privacy_term(sid, "[PRODUIT_1]")
    assert store.add_privacy_terms(sid, [("produit", "Orion")], "consultant")[0]["token"] == "[PRODUIT_1]"
    assert store.add_privacy_terms(sid, [("produit", "Vega")], "consultant")[0]["token"] == "[PRODUIT_2]"
    assert store.add_privacy_terms(sid, [("inconnu", "Sirius")], "consultant") == []


def test_article_ids_and_scientific_links_are_not_masked():
    shield = Privacy([{"token": "[ENTREPRISE_1]", "value": "Novatec"}])
    text = f"Voir [{ARTICLE_ID}] et https://doi.org/10.1000/x"
    assert shield.mask(text) == text
