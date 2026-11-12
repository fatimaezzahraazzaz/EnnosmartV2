from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, List, Optional
import re

from .schemas import CanonicalEntity, Chunk, ExtractionItem
from .semantic_extractor import LLMClientProtocol, parse_json_payload
from .execution import StageTimings, diagnostic_workers


ALLOWED_STATUSES = {
    "VALID_LOCK",
    "NEEDS_REFORMULATION",
    "REQUIREMENT_ONLY",
    "ENGINEERING_DIFFICULTY",
    "INSUFFICIENT_EVIDENCE",
}


QUALIFICATION_SYSTEM = """
Tu es l'étape de qualification des candidats verrous d'EnnoDiagnostic.

Cette étape est indépendante du domaine :
logiciel, IA, mécanique, électronique, matériaux, chimie,
procédés industriels, énergie, etc.

Les candidats ont déjà été extraits et consolidés.

Tu ne dois :
- ni supprimer un candidat ;
- ni fusionner des candidats ;
- ni inventer une difficulté absente des sources ;
- ni décider de l'éligibilité CIR finale.

Tu dois uniquement qualifier ce que les sources permettent d'affirmer.

============================================================
PRINCIPE CENTRAL
============================================================

Ne confonds jamais :

A. ce que le système DOIT faire ;
B. ce qui est difficile à implémenter ;
C. ce qui constitue réellement une incertitude technique ;
D. une vraie problématique technique qui est encore mal formulée.

Le fait que le document utilise les mots :
"verrou", "problème", "enjeu", "complexe", "critique",
"innovation", "difficulté"
n'est jamais une preuve suffisante.

============================================================
VALID_LOCK
============================================================

Utilise VALID_LOCK lorsque les sources montrent concrètement
une incertitude scientifique ou technique réellement rencontrée.

Il doit exister des indices concrets tels que :

- comportement instable ou non reproductible observé ;
- phénomènes non maîtrisés ;
- résultats incompatibles ou imprévisibles ;
- essais infructueux ;
- réglages testés sans résoudre le problème ;
- limites démontrées d'une méthode existante ;
- propriété technique qu'on ne parvient pas à garantir ;
- cas restant techniquement non résolus ;
- absence de méthode connue satisfaisant simultanément
  les contraintes recherchées.

VALID_LOCK doit être fondé sur les preuves du projet,
pas uniquement sur la connaissance générale du domaine.

============================================================
NEEDS_REFORMULATION
============================================================

Utilise NEEDS_REFORMULATION lorsqu'une problématique technique
spécifique et crédible est réellement identifiable dans les sources,
mais qu'elle est formulée trop généralement ou insuffisamment reliée
au contexte concret du projet.

Exemples génériques :

- un phénomène technique problématique est identifié,
  mais ses conditions d'apparition ne sont pas détaillées ;

- un comportement difficile à maîtriser est clairement mentionné,
  mais les essais ou limites des approches existantes ne sont pas décrits ;

- le candidat est formulé comme un objectif
  ("maîtriser...", "garantir...", "réduire...")
  alors que la source révèle derrière cet objectif
  une vraie problématique technique ;

- une difficulté de vérification ou validation paraît techniquement
  non triviale, mais le dossier ne précise pas encore suffisamment
  ce qui empêche les méthodes existantes de la résoudre.

NEEDS_REFORMULATION signifie :

"Le problème technique est pertinent et identifiable,
mais le dossier doit mieux expliquer ce qui reste non maîtrisé."

REGLE CRITIQUE POUR NEEDS_REFORMULATION :

La reformulation sert uniquement a PRECISER une problematique technique
deja soutenue par les preuves.

Elle ne doit jamais CREER la problematique technique qui manque.

Pour utiliser NEEDS_REFORMULATION, la PREUVE_EXACTE ou le CONTEXTE_LOCAL
doit deja relier explicitement le candidat a au moins un phenomene,
obstacle, comportement, limite ou incertitude technique concrete.

Un contexte general sur les risques connus d'une technologie ne suffit pas.

Par exemple, le fait qu'un passage precedent indique de maniere generale
que l'IA peut etre non deterministe, produire des erreurs ou halluciner
ne permet pas, a lui seul, de transformer un objectif de cadrage tel que
"definir ou utiliser l'IA" en verrou technique.

De meme, les formulations suivantes ne sont pas automatiquement des verrous :
- definir un perimetre ;
- identifier les cas d'usage ;
- choisir ou appliquer une technologie ;
- determiner les etapes ou elle sera utilisee ;
- etudier l'apport d'une technologie.

Si les sources expriment uniquement ce type de cadrage ou d'objectif,
sans decrire le probleme technique sous-jacent qui empeche de le realiser,
utilise INSUFFICIENT_EVIDENCE.

En revanche, si la source relie explicitement cet objectif a une difficulte
technique concrete et non maitrisee, NEEDS_REFORMULATION reste approprie.

La suggested_statement ne doit jamais ajouter :
- un echec non mentionne ;
- une instabilite non rattachee au candidat ;
- une limite de methode non documentee ;
- une incertitude seulement deduite de connaissances generales.

Elle doit reformuler uniquement ce que les preuves permettent deja
d'identifier comme probleme technique.

Dans ce cas :
- suggested_statement doit proposer une formulation plus précise ;
- missing_evidence doit indiquer ce qu'il faut demander au consultant.

============================================================
REQUIREMENT_ONLY
============================================================

Utilise REQUIREMENT_ONLY si la source exprime seulement
une propriété attendue ou une contrainte.

Exemples :

- respecter un temps de réponse ;
- assurer la sécurité ;
- être robuste ;
- garantir une précision ;
- valider les résultats ;
- assurer une traçabilité ;
- résister à une température ou à une charge donnée.

Une exigence importante, difficile ou critique
n'est pas automatiquement un verrou.

Pour sortir de REQUIREMENT_ONLY, il faut identifier
une difficulté technique sous-jacente concrète.

============================================================
ENGINEERING_DIFFICULTY
============================================================

Utilise ENGINEERING_DIFFICULTY lorsque la source décrit principalement :

- intégration ;
- migration ;
- configuration ;
- développement ;
- adaptation ;
- interopérabilité ;
- compatibilité ;
- environnement propriétaire ;
- changement de format ou protocole ;

sans montrer que les méthodes d'ingénierie disponibles
sont insuffisantes ou qu'une incertitude technique subsiste.

Une tâche peut être complexe, coûteuse et longue
sans constituer une incertitude R&D.


============================================================
DISTINCTION REQUIREMENT_ONLY / ENGINEERING_DIFFICULTY
============================================================

Cette distinction est importante :

REQUIREMENT_ONLY
= la source dit principalement CE QUE la solution doit respecter,
sans décrire d'obstacle concret de mise en œuvre.

Exemples :
- "le système doit être sûr" ;
- "il faut respecter le temps réel" ;
- "les résultats doivent être validés".

ENGINEERING_DIFFICULTY
= la source décrit un obstacle concret d'intégration, d'adaptation,
d'interopérabilité, de compatibilité ou de déploiement, mais sans
démontrer encore une incertitude R&D.

Exemples :
- hétérogénéité de plusieurs environnements qui complique l'intégration ;
- systèmes propriétaires rendant l'interopérabilité difficile ;
- incompatibilités entre formats, protocoles ou composants ;
- adaptation complexe d'une solution existante à plusieurs plateformes.

IMPORTANT :
Une difficulté d'ingénierie n'a PAS besoin d'être une incertitude
scientifique pour être classée ENGINEERING_DIFFICULTY.

Si la source dit explicitement qu'une propriété du contexte
(hétérogénéité, environnement propriétaire, incompatibilité,
interopérabilité, intégration, migration, adaptation, etc.)
REND la mise en œuvre ou l'intégration techniquement difficile,
préfère ENGINEERING_DIFFICULTY à REQUIREMENT_ONLY.

En revanche, si la source énonce seulement une contrainte à respecter
sans obstacle concret de mise en œuvre, garde REQUIREMENT_ONLY.

Exemple :
"Les environnements industriels sont hétérogènes et propriétaires,
ce qui rend complexe l'intégration de l'IA dans les chaînes existantes."
=> ENGINEERING_DIFFICULTY, sauf si les preuves démontrent en plus
une incertitude technique non maîtrisée justifiant une autre catégorie.

============================================================
INSUFFICIENT_EVIDENCE
============================================================

Utilise INSUFFICIENT_EVIDENCE lorsqu'il existe un signal possible,
mais qu'on ne peut même pas déterminer clairement si une véritable
problématique technique non maîtrisée existe.

Différence fondamentale :

NEEDS_REFORMULATION
= le problème technique est identifiable,
  mais mal formulé ou insuffisamment démontré.

INSUFFICIENT_EVIDENCE
= les informations disponibles ne permettent pas encore
  de savoir s'il existe vraiment un problème technique non maîtrisé.

============================================================
CONTEXTE LOCAL
============================================================

Pour chaque preuve tu peux recevoir :

PREUVE_EXACTE
= citation qui a déclenché le candidat.

CONTEXTE_LOCAL
= texte environnant provenant du même passage du document.

La PREUVE_EXACTE reste la preuve principale.

Le CONTEXTE_LOCAL sert seulement à comprendre correctement
le problème et éviter de qualifier une phrase isolée.

Tu ne dois pas inventer une information qui n'existe
ni dans la preuve ni dans le contexte.

============================================================
REFORMULATION
============================================================

Une suggested_statement doit :

- décrire le problème technique ;
- éviter de simplement répéter l'objectif ;
- rester strictement soutenue par les sources ;
- ne jamais inventer un échec ou une incertitude.

Exemple :

Trop objectif :
"Garantir la stabilité du procédé."

Meilleure formulation si les preuves le permettent :
"Variabilité du comportement du procédé dans des conditions
identiques empêchant d'obtenir des résultats reproductibles."

============================================================
MISSING_EVIDENCE
============================================================

Pour VALID_LOCK :
missing_evidence peut être vide.

Pour NEEDS_REFORMULATION :
indique ce qui permettrait de mieux démontrer le problème :
- résultats d'essais ;
- conditions d'apparition ;
- limites des méthodes existantes ;
- approches déjà testées ;
- impacts observés ;
- paramètres influents.

Pour REQUIREMENT_ONLY :
indique ce qu'il faudrait démontrer pour transformer
la contrainte en véritable problématique technique.

Pour ENGINEERING_DIFFICULTY :
indique ce qui montrerait que le problème dépasse
une difficulté d'ingénierie classique.

Pour INSUFFICIENT_EVIDENCE :
indique précisément les informations permettant de décider.

============================================================
RÈGLES FINALES
============================================================

1. Ne supprime aucun candidat.
2. Ne crée aucun candidat supplémentaire.
3. Ne te fie jamais uniquement au mot "verrou".
4. VALID_LOCK exige des preuves concrètes.
5. NEEDS_REFORMULATION doit être utilisé lorsqu'un problème technique
   réel est identifiable mais encore trop général ou mal démontré.
6. REQUIREMENT_ONLY correspond à une propriété attendue sans
   difficulté technique sous-jacente démontrée.
7. ENGINEERING_DIFFICULTY correspond à une complexité classique.
8. INSUFFICIENT_EVIDENCE correspond à une ambiguïté réelle.
9. En cas de doute entre VALID_LOCK et NEEDS_REFORMULATION,
   préfère NEEDS_REFORMULATION.
10. En cas de doute entre NEEDS_REFORMULATION et REQUIREMENT_ONLY,
    vérifie s'il existe réellement un phénomène ou obstacle technique
    spécifique dans les sources.

11. NEEDS_REFORMULATION ne doit jamais servir a transformer un simple
    objectif, cadrage, perimetre ou cas d'usage en verrou.

12. Le probleme technique sous-jacent doit etre deja identifiable dans
    PREUVE_EXACTE ou CONTEXTE_LOCAL. Le contexte general du domaine ne
    suffit pas a l'inventer.

13. Si le candidat exprime uniquement un cadrage ou un objectif et que
    l'obstacle technique sous-jacent n'est pas identifiable dans les
    preuves, prefere INSUFFICIENT_EVIDENCE.


============================================================
ATTRIBUTION PROJET / LITTERATURE
============================================================

Chaque preuve peut être annotée SOURCE_ROLE :
project_evidence = preuve décrivant réellement le projet analysé.
literature = article, publication, état de l'art ou résultat d'une équipe externe.
context = information générale de contexte.
admin = budget, gouvernance ou procédure administrative.
unknown = attribution incertaine.

REGLES :
- Ne supprime aucun candidat, quel que soit son SOURCE_ROLE.
- Une preuve literature peut montrer qu'un problème existe dans le domaine,
  mais elle ne prouve PAS que le projet analysé l'a réellement rencontré.
- Un candidat fondé uniquement sur literature ne peut pas être VALID_LOCK pour le projet.
  Utilise normalement INSUFFICIENT_EVIDENCE si le problème est crédible mais qu'aucune
  preuve du projet ne le rattache aux travaux analysés.
- Si un candidat possède à la fois project_evidence et literature, qualifie le verrou
  à partir des preuves du projet ; la littérature sert seulement de contexte.
- context, admin et unknown ne doivent pas être transformés en preuve projet.
- Vérifie les candidats dans la vue globale du document/projet, mais ne transfère
  jamais la preuve d'un autre candidat sans lien technique démontré.
- Pour VALID_LOCK ou NEEDS_REFORMULATION, cite dans evidence_ids au moins une
  preuve project_evidence pertinente qui décrit concrètement la difficulté.

Retourne uniquement :

{
  "qualifications": [
    {
      "entity_id": "...",
      "status": "VALID_LOCK|NEEDS_REFORMULATION|REQUIREMENT_ONLY|ENGINEERING_DIFFICULTY|INSUFFICIENT_EVIDENCE",
      "reason": "...",
      "suggested_statement": "...",
      "evidence_ids": [],
      "missing_evidence": []
    }
  ]
}
""".strip()


def _build_prompt(
    entities: List[CanonicalEntity],
    chunks: Optional[Iterable[Chunk]] = None,
    *,
    all_locks: Optional[List[CanonicalEntity]] = None,
    project_items: Optional[List[ExtractionItem]] = None,
) -> str:

    chunk_by_id = {}

    if chunks:
        chunk_by_id = {
            chunk.chunk_id: chunk
            for chunk in chunks
        }

    blocks = []

    for entity in entities:
        evidence_blocks = []
        evidence_source_roles = entity.metadata.get("evidence_source_roles") or {}

        for evidence in entity.evidences:
            quote = str(evidence.quote or "").strip()

            if len(quote) > 1000:
                quote = quote[:1000] + " [...]"

            context = ""

            chunk = chunk_by_id.get(evidence.chunk_id)

            if chunk is not None:
                context = str(chunk.text or "").strip()

                if len(context) > 1800:
                    context = context[:1800] + " [...]"

            evidence_blocks.append(
                "\n".join(
                    [
                        f"EVIDENCE_ID: {evidence.evidence_id}",
                        f"DOCUMENT: {evidence.document_name}",
                        f"SOURCE_ROLE: {evidence_source_roles.get(evidence.evidence_id, 'unknown')}",
                        f"PREUVE_EXACTE: {quote}",
                        f"CONTEXTE_LOCAL: {context}",
                    ]
                )
            )

        blocks.append(
            "\n".join(
                [
                    f"ENTITY_ID: {entity.entity_id}",
                    f"CANDIDAT: {entity.statement}",
                    f"SOURCE_ROLES: {entity.metadata.get('source_roles')}",
                    f"HAS_PROJECT_EVIDENCE: {entity.metadata.get('has_project_evidence')}",
                    "SOURCES:",
                    "\n\n".join(evidence_blocks),
                ]
            )
        )

    overview = "\n".join(
        f"- {entity.entity_id}: {entity.statement[:260]} "
        f"(provenance={','.join(entity.metadata.get('source_roles') or ['unknown'])})"
        for entity in (all_locks or entities)
    )
    batch_terms = set(re.findall(
        r"\w{4,}", " ".join(entity.statement for entity in entities).casefold()
    ))
    batch_documents = {doc_id for entity in entities for doc_id in entity.document_ids}
    unique_items = {}
    for item in project_items or []:
        evidence_id = item.evidence.evidence_id
        if evidence_id and evidence_id not in unique_items:
            unique_items[evidence_id] = item
    ranked = sorted(unique_items.values(), key=lambda item: (
        -int(item.evidence.document_id in batch_documents),
        -len(batch_terms & set(re.findall(r"\w{4,}", (
            item.statement + " " + item.evidence.quote
        ).casefold()))),
        item.evidence.evidence_id,
    ))[:60]
    project_evidence = "\n".join(
        f"- EVIDENCE_ID={item.evidence.evidence_id} TYPE={item.kind} "
        f"DOCUMENT={item.evidence.document_name} "
        f"PREUVE_EXACTE={item.evidence.quote[:450]}"
        for item in ranked
    )
    return (
        "VUE GLOBALE DES CANDIDATS DU DOSSIER :\n"
        + (overview or "Aucun")
        + "\n\nPREUVES PROPRES AU PROJET (sélection pertinente du dossier ; "
        "la littérature ne figure pas ici) :\n"
        + (project_evidence or "Aucune preuve projet classée")
        + "\n\nCANDIDATS À QUALIFIER DANS CE LOT :\n\n"
        + "\n\n====================\n\n".join(blocks)
    )


class LockQualifier:
    """Chaque verrou est qualifié seul : un appel par verrou, avec ses propres
    preuves et les faits du projet choisis d'après ses seuls termes et documents.

    Son statut ne dépend donc ni des autres verrous du dossier ni de leur ordre.
    Essai OVH du 12/11 (Filmogène, mêmes verrous, mêmes preuves) : avec des lots
    de 5 et la liste de tous les verrous dans chaque prompt, changer les voisins
    d'un verrou changeait le statut de 6 verrous sur 10, et inverser l'ordre de
    la liste en changeait 2 à 3 ; jugé seul, chaque verrou a gardé son statut.
    """

    def __init__(
        self,
        llm: Optional[LLMClientProtocol],
        *,
        retry_missing: bool = True,
    ):
        self.llm = llm
        self.retry_missing = bool(retry_missing)

    def _request_one(self, lock: CanonicalEntity, chunks, project_items, *, llm=None):
        raw = (llm or self.llm).extract_json(
            QUALIFICATION_SYSTEM,
            _build_prompt([lock], chunks=chunks, project_items=project_items),
        )
        data = parse_json_payload(raw)
        raw_qualifications = data.get("qualifications") or []
        allowed_ids = {lock.entity_id}
        output = {}

        if isinstance(raw_qualifications, list):
            for item in raw_qualifications:
                if not isinstance(item, dict):
                    continue
                entity_id = str(item.get("entity_id") or "").strip()
                if entity_id in allowed_ids:
                    output[entity_id] = item

        return output

    def qualify(
        self,
        entities: Iterable[CanonicalEntity],
        chunks: Optional[Iterable[Chunk]] = None,
        extractions: Optional[Iterable[ExtractionItem]] = None,
    ) -> List[CanonicalEntity]:
        entities = list(entities)
        locks = [entity for entity in entities if entity.kind == "lock"]
        project_items = [
            item for item in (extractions or [])
            if (item.metadata or {}).get("source_role") == "project_evidence"
            and item.evidence.quote_verified
        ]
        project_by_evidence_id = {
            item.evidence.evidence_id: item for item in project_items
        }

        if not locks or self.llm is None:
            return entities

        chunk_list = list(chunks) if chunks is not None else None
        qualification_by_id = {}
        failure_reason_by_id = {}

        # Un verrou par appel, clients isolés et assemblage dans l'ordre initial.
        batches = [[lock] for lock in locks]
        workers = min(diagnostic_workers(), len(batches))
        clients = []
        if workers > 1 and getattr(self.llm, "supports_fork", False):
            try:
                clients = [self.llm.fork() for _ in batches]
            except Exception:
                clients = []
        bases = [getattr(client, "base", client) for client in clients]
        original = getattr(self.llm, "base", self.llm)
        if (len(clients) != len(batches) or any(client is None for client in clients)
                or len({id(client) for client in bases}) != len(bases)
                or any(client is original for client in bases)):
            workers = 1
            clients = [self.llm] * len(batches)
        timings = StageTimings(getattr(self, "run_label", "diagnostic"))

        def request(index):
            try:
                return timings.call(
                    f"qualification_batch_{index + 1}", self._request_one,
                    batches[index][0], chunk_list, project_items, llm=clients[index],
                )
            except Exception as exc:
                return exc

        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="diagnostic-qualify") as pool:
                futures = [pool.submit(request, index) for index in range(len(batches))]
                results = [future.result() for future in futures]
        else:
            results = [request(index) for index in range(len(batches))]
        for batch, batch_results in zip(batches, results):
            if not isinstance(batch_results, Exception):
                qualification_by_id.update(batch_results)
                for entity in batch:
                    if entity.entity_id not in batch_results:
                        failure_reason_by_id[entity.entity_id] = "candidate_missing_from_qualification_batch"
            else:
                for entity in batch:
                    failure_reason_by_id[entity.entity_id] = (
                        "qualification_batch_error:" + type(batch_results).__name__
                    )

        # Retry individuel uniquement pour les candidats manquants.
        if self.retry_missing:
            missing_locks = [
                entity for entity in locks
                if entity.entity_id not in qualification_by_id
            ]
            for entity in missing_locks:
                try:
                    single_result = self._request_one(
                        entity, chunk_list, project_items
                    )
                    qualification = single_result.get(entity.entity_id)
                    if qualification:
                        qualification_by_id[entity.entity_id] = qualification
                        failure_reason_by_id.pop(entity.entity_id, None)
                    else:
                        failure_reason_by_id[entity.entity_id] = "candidate_missing_after_individual_retry"
                except Exception as exc:
                    failure_reason_by_id[entity.entity_id] = (
                        "qualification_retry_error:" + type(exc).__name__
                    )

        output = []

        for entity in entities:
            if entity.kind != "lock":
                output.append(entity)
                continue

            qualification = qualification_by_id.get(entity.entity_id)
            metadata = dict(entity.metadata)

            if not qualification:
                metadata.update({
                    "qualification_status": "NOT_EVALUATED",
                    "qualification_reason": failure_reason_by_id.get(
                        entity.entity_id,
                        "qualification_not_returned",
                    ),
                    "qualification_keeps_candidate": True,
                })
                output.append(replace(entity, metadata=metadata, needs_review=True))
                continue

            status = str(qualification.get("status") or "").strip().upper()
            if status not in ALLOWED_STATUSES:
                status = "INSUFFICIENT_EVIDENCE"

            valid_evidence_ids = (
                {evidence.evidence_id for evidence in entity.evidences}
                | set(project_by_evidence_id)
            )
            selected_evidence_ids = [
                str(value)
                for value in (qualification.get("evidence_ids") or [])
                if str(value) in valid_evidence_ids
            ]
            suggested_statement = str(
                qualification.get("suggested_statement") or ""
            ).strip()
            reason = str(qualification.get("reason") or "").strip()
            missing_evidence = [
                str(value).strip()
                for value in (qualification.get("missing_evidence") or [])
                if str(value).strip()
            ]

            entity_roles = metadata.get("evidence_source_roles") or {}
            verified_entity_ids = {
                evidence.evidence_id for evidence in entity.evidences
                if evidence.quote_verified
            }
            selected_project_ids = [
                evidence_id for evidence_id in selected_evidence_ids
                if evidence_id in project_by_evidence_id
                or (
                    evidence_id in verified_entity_ids
                    and entity_roles.get(evidence_id) == "project_evidence"
                )
            ]
            if status in {"VALID_LOCK", "NEEDS_REFORMULATION"} and not selected_project_ids:
                status = "INSUFFICIENT_EVIDENCE"
                reason = (
                    "Aucune preuve propre au projet n'a été citée pour soutenir "
                    "cette difficulté. " + reason
                ).strip()
                missing_evidence.append(
                    "Essai, observation ou difficulté technique concrète du projet liée à ce candidat."
                )

            linked_items = [
                project_by_evidence_id[evidence_id]
                for evidence_id in selected_project_ids
                if evidence_id in project_by_evidence_id
                and evidence_id not in {e.evidence_id for e in entity.evidences}
            ]
            evidence_roles = dict(entity_roles)
            for item in linked_items:
                evidence_roles[item.evidence.evidence_id] = "project_evidence"
            if linked_items:
                metadata["evidence_source_roles"] = evidence_roles
                metadata["source_roles"] = sorted(
                    set(metadata.get("source_roles") or []) | {"project_evidence"}
                )
                metadata["has_project_evidence"] = True
                metadata["qualification_linked_evidence_ids"] = [
                    item.evidence.evidence_id for item in linked_items
                ]

            metadata.update({
                "qualification_status": status,
                "qualification_reason": reason,
                "suggested_statement": suggested_statement,
                "qualification_evidence_ids": selected_evidence_ids,
                "missing_evidence": missing_evidence,
                "qualification_keeps_candidate": True,
            })

            output.append(
                replace(
                    entity,
                    metadata=metadata,
                    evidences=entity.evidences + [item.evidence for item in linked_items],
                    source_item_ids=entity.source_item_ids + [
                        item.item_id for item in linked_items
                    ],
                    document_ids=sorted(
                        set(entity.document_ids)
                        | {item.evidence.document_id for item in linked_items}
                    ),
                    needs_review=(entity.needs_review or status != "VALID_LOCK"),
                )
            )

        return output
