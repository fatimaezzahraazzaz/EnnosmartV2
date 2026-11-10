from __future__ import annotations

from dataclasses import asdict, dataclass
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .cir_scoring import CIRDiagnosticScore, CIRScorer
from .config import GlobalFastPathConfig, GlobalReviewerConfig, PipelineConfig
from .global_fast_path import GlobalFastExtractor
from .completeness_auditor import CompletenessAuditor
from .global_project_reviewer import GlobalProjectReviewer, reviewed_fact_view
from .lock_family_synthesis import LockFamilySynthesis, LockFamilySynthesizer
from .pipeline import EnnoDiagnosticV2
from .project_frascati import ProjectFrascatiAssessment, ProjectFrascatiEvaluator
from .project_synthesis import ProjectSynthesis, ProjectSynthesizer
from .rnd_approach import RnDApproachAnalyzer, RnDApproachAssessment
from .runtime_adapters import EnnoSmartLLMAdapter, GlobalFastLLMAdapter, GlobalReasonerLLMAdapter
from .schemas import DocumentInput, PipelineResult
from .semantic_extractor import LLMClientProtocol
from .execution import StageTimings, diagnostic_workers

VERSION = "ennodiagnostic_v2_full_runtime_1"


def parse_domain_tags(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = value.split("|")
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = [value]

    out, seen = [], set()
    for raw in values:
        tag = str(raw or "").strip()
        key = tag.casefold()
        if not tag or key in seen:
            continue
        seen.add(key)
        out.append(tag)
    return out


def _domain_context(tags: Sequence[str]) -> str:
    tags = parse_domain_tags(tags)
    if not tags:
        return ""
    bullets = "\n".join(f"- {tag}" for tag in tags)
    return (
        "CONTEXTE DOMAINE DU PROJET\n"
        f"{bullets}\n\n"
        "Ces domaines servent uniquement à interpréter le vocabulaire et les difficultés.\n"
        "Ils ne constituent jamais une preuve.\n"
        "Ils ne doivent supprimer aucun candidat.\n"
        "Ils ne donnent aucun point supplémentaire au score CIR.\n"
    )


class ContextualLLMAdapter:
    def __init__(self, base: LLMClientProtocol, domain_tags: Sequence[str]):
        self.base = base
        self.domain_tags = parse_domain_tags(domain_tags)
        self.supports_fork = bool(getattr(base, "supports_fork", False))

    def fork(self) -> Optional["ContextualLLMAdapter"]:
        if not self.supports_fork:
            return None
        forked = self.base.fork()
        return ContextualLLMAdapter(forked, self.domain_tags) if forked else None

    def extract_json(self, system_prompt: str, user_prompt: str) -> str:
        context = _domain_context(self.domain_tags)
        if context:
            user_prompt = f"{context}\n\n{str(user_prompt or '').strip()}"
        return self.base.extract_json(system_prompt, user_prompt)


@dataclass
class FullRuntimeResult:
    version: str
    domain_tags: List[str]
    core: PipelineResult
    lock_families: LockFamilySynthesis
    project_synthesis: ProjectSynthesis
    rnd_approach: RnDApproachAssessment
    project_frascati: ProjectFrascatiAssessment
    scoring: CIRDiagnosticScore

    def to_dict(self) -> Dict[str, Any]:
        def dump(value):
            if hasattr(value, "to_dict"):
                return value.to_dict()
            return asdict(value)

        return {
            "version": self.version,
            "domain_tags": list(self.domain_tags),
            "core": self.core.to_dict(),
            "lock_families": dump(self.lock_families),
            "project_synthesis": dump(self.project_synthesis),
            "rnd_approach": dump(self.rnd_approach),
            "project_frascati": dump(self.project_frascati),
            "scoring": self.scoring.to_dict(),
        }


class EnnoDiagnosticV2FullRuntime:
    def __init__(
        self,
        *,
        domain_tags: Optional[Sequence[str]] = None,
        semantic_llm: Optional[LLMClientProtocol] = None,
        consolidation_llm: Optional[LLMClientProtocol] = None,
        qualification_llm: Optional[LLMClientProtocol] = None,
        family_llm: Optional[LLMClientProtocol] = None,
        synthesis_llm: Optional[LLMClientProtocol] = None,
        rnd_llm: Optional[LLMClientProtocol] = None,
        frascati_llm: Optional[LLMClientProtocol] = None,
        global_reviewer_llm: Optional[LLMClientProtocol] = None,
        global_reviewer_config: Optional[GlobalReviewerConfig] = None,
        fast_llm: Optional[LLMClientProtocol] = None,
        completeness_llm: Optional[LLMClientProtocol] = None,
        fast_config: Optional[GlobalFastPathConfig] = None,
        fast_cache_dir: Optional[str] = None,
        run_label: str = "",
    ):
        self.run_label = f"{run_label or 'diagnostic'} run={uuid4().hex[:10]}"
        self.domain_tags = parse_domain_tags(domain_tags)
        self.global_reviewer_config = global_reviewer_config or GlobalReviewerConfig()
        self.fast_config = fast_config or GlobalFastPathConfig()
        fast_extractor = None
        # Les runtimes construits avec des doubles LLM de test gardent leur
        # comportement historique, sauf injection explicite d'un fast_llm.
        injected_legacy = any(value is not None for value in (
            semantic_llm, consolidation_llm, qualification_llm, family_llm,
            synthesis_llm, rnd_llm, frascati_llm, global_reviewer_llm,
        ))
        if self.fast_config.enabled and (fast_llm is not None or not injected_legacy):
            try:
                fast_extractor = GlobalFastExtractor(
                    fast_llm or GlobalFastLLMAdapter(self.fast_config),
                    self.fast_config, fast_cache_dir,
                )
            except Exception:
                fast_extractor = None
            if fast_extractor is not None:
                try:
                    fast_extractor.completeness_auditor = CompletenessAuditor(
                        completeness_llm or fast_llm or GlobalFastLLMAdapter(
                            self.fast_config,
                            request_name="ennodiagnostic:v2:completeness_audit",
                        ),
                        self.fast_config, fast_cache_dir,
                    )
                except Exception as exc:
                    print(f"[EnnoDiagnostic][CompletenessAudit] indisponible: {type(exc).__name__}:{exc}; "
                          "Fast Path conservé", flush=True)
        global_reviewer = None
        if self.global_reviewer_config.enabled:
            try:
                global_reviewer = GlobalProjectReviewer(
                    global_reviewer_llm or GlobalReasonerLLMAdapter(self.global_reviewer_config),
                    self.global_reviewer_config,
                )
            except Exception:
                # La construction du modèle dédié ne bloque pas le pipeline legacy.
                global_reviewer = None

        semantic = ContextualLLMAdapter(
            semantic_llm or EnnoSmartLLMAdapter(
                request_name="ennodiagnostic:v2:semantic_extract",
                temperature=0.0,
                max_output_tokens=2600,
            ),
            self.domain_tags,
        )

        consolidation = consolidation_llm or EnnoSmartLLMAdapter(
            request_name="ennodiagnostic:v2:global_lock_consolidation",
            temperature=0.0,
            max_output_tokens=16384,
            # Budget du dossier explicite et sans coupure silencieuse : le
            # consolidateur découpe lui-même en lots qui tiennent dans ce budget.
            # Sans cela, le plafond générique (30 000 car.) coupait le milieu du
            # prompt et le modèle ne voyait qu'une partie des candidats.
            prompt_char_limit=self.global_reviewer_config.max_prompt_chars,
            fail_on_truncation=True,
        )

        qualification = ContextualLLMAdapter(
            qualification_llm or EnnoSmartLLMAdapter(
                request_name="ennodiagnostic:v2:lock_qualification",
                temperature=0.0,
                max_output_tokens=3200,
            ),
            self.domain_tags,
        )

        family = family_llm or EnnoSmartLLMAdapter(
            request_name="ennodiagnostic:v2:lock_family_synthesis",
            temperature=0.0,
            max_output_tokens=2600,
            # Même règle : tous les verrous doivent être vus par le modèle.
            prompt_char_limit=self.global_reviewer_config.max_prompt_chars,
            fail_on_truncation=True,
        )

        synthesis = ContextualLLMAdapter(
            synthesis_llm or EnnoSmartLLMAdapter(
                request_name="ennodiagnostic:v2:project_synthesis",
                temperature=0.0,
                max_output_tokens=4200,
            ),
            self.domain_tags,
        )

        rnd = rnd_llm or EnnoSmartLLMAdapter(
            request_name="ennodiagnostic:v2:rnd_approach",
            temperature=0.0,
            max_output_tokens=3200,
        )

        frascati = frascati_llm or EnnoSmartLLMAdapter(
            request_name="ennodiagnostic:v2:project_frascati",
            temperature=0.0,
            max_output_tokens=2600,
        )

        self.core = EnnoDiagnosticV2(
            semantic_llm=semantic,
            consolidation_llm=consolidation,
            qualification_llm=qualification,
            frascati_llm=None,
            config=PipelineConfig(
                run_frascati=False,
                require_human_review_for_locks=True,
            ),
            global_reviewer=global_reviewer,
            fast_extractor=fast_extractor,
        )
        self.families = LockFamilySynthesizer(family)
        reviewed_synthesis = (
            synthesis if synthesis_llm is not None else ContextualLLMAdapter(
                EnnoSmartLLMAdapter(
                    request_name="ennodiagnostic:v2:project_synthesis_reviewed",
                    temperature=0.0,
                    max_output_tokens=4200,
                    prompt_char_limit=self.global_reviewer_config.max_prompt_chars,
                    fail_on_truncation=True,
                ),
                self.domain_tags,
            )
        )
        self.synthesis = ProjectSynthesizer(synthesis, reviewed_llm=reviewed_synthesis)
        self.rnd = RnDApproachAnalyzer(rnd)
        self.frascati = ProjectFrascatiEvaluator(frascati)
        self.scorer = CIRScorer()

    def run(self, documents: Iterable[DocumentInput]) -> FullRuntimeResult:
        timings = StageTimings(self.run_label)
        self.core.qualifier.run_label = self.run_label
        core = timings.call("core", self.core.run, list(documents))
        synthesis_extractions = core.extractions
        synthesis_entities = core.entities
        globally_reviewed = False
        if (core.global_review or {}).get("status") == "complete":
            try:
                synthesis_extractions, synthesis_entities = reviewed_fact_view(
                    core.extractions, core.entities, core.global_review
                )
                globally_reviewed = True
            except Exception as exc:
                core.global_review = {
                    **core.global_review, "status": "fallback",
                    "error": f"reviewed_view_error:{type(exc).__name__}:{exc}",
                }
                core.audit["global_review_status"] = "fallback"
        # Chaque tâche conserve ses entrées et son client. Aucun partage d'un
        # double/client injecté non isolé entre deux appels simultanés.
        reuse_reviewed_synthesis = globally_reviewed and core.global_review.get("mode") == "global_fast_path"
        clients = [self.families.llm, self.rnd.llm, self.frascati.llm]
        if not reuse_reviewed_synthesis:
            clients.append(self.synthesis.llm)
        if globally_reviewed:
            clients.append(self.synthesis.reviewed_llm)
        clients = [getattr(client, "base", client) for client in clients if client is not None]
        workers = diagnostic_workers() if len({id(c) for c in clients}) == len(clients) else 1
        if workers == 1:
            families = timings.call("families", self.families.synthesize, core.entities)
            legacy_synthesis_for_scoring = timings.call(
                "synthesis_scoring", self.synthesis.synthesize, core.extractions, core.entities,
            ) if not reuse_reviewed_synthesis else None
            synthesis = timings.call(
                "synthesis_reviewed", self.synthesis.synthesize,
                synthesis_extractions, synthesis_entities, globally_reviewed=True,
            ) if globally_reviewed else legacy_synthesis_for_scoring
            rnd = timings.call("rnd", self.rnd.assess, core.extractions, chunks=core.chunks)
            frascati = timings.call("frascati", self.frascati.assess,
                                   core.entities, core.extractions, rnd, chunks=core.chunks)
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="diagnostic-post") as pool:
                families_future = pool.submit(timings.call, "families", self.families.synthesize,
                                              deepcopy(core.entities))
                legacy_future = pool.submit(timings.call, "synthesis_scoring", self.synthesis.synthesize,
                                            deepcopy(core.extractions), deepcopy(core.entities)) if not reuse_reviewed_synthesis else None
                rnd_future = pool.submit(timings.call, "rnd", self.rnd.assess,
                                         deepcopy(core.extractions), chunks=deepcopy(core.chunks))
                reviewed_future = pool.submit(
                    timings.call, "synthesis_reviewed", self.synthesis.synthesize,
                    deepcopy(synthesis_extractions), deepcopy(synthesis_entities), globally_reviewed=True,
                ) if globally_reviewed else None
                # Frascati dépend de R&D, mais pas des synthèses ni des familles.
                rnd = rnd_future.result()
                frascati_future = pool.submit(timings.call, "frascati", self.frascati.assess,
                                               deepcopy(core.entities), deepcopy(core.extractions),
                                               deepcopy(rnd), chunks=deepcopy(core.chunks))
                families = families_future.result()
                legacy_synthesis_for_scoring = legacy_future.result() if legacy_future else None
                synthesis = reviewed_future.result() if reviewed_future else legacy_synthesis_for_scoring
                frascati = frascati_future.result()
        scoring = self.scorer.score(
            entities=core.entities,
            extractions=core.extractions,
            rnd=rnd,
            frascati=frascati,
            synthesis=synthesis if reuse_reviewed_synthesis else legacy_synthesis_for_scoring,
        )
        core.audit["execution"] = {"post_workers": workers, "stage_seconds": timings.seconds}

        return FullRuntimeResult(
            version=VERSION,
            domain_tags=list(self.domain_tags),
            core=core,
            lock_families=families,
            project_synthesis=synthesis,
            rnd_approach=rnd,
            project_frascati=frascati,
            scoring=scoring,
        )


def build_full_runtime(*, domain_tags: Any = None, fast_cache_dir: Optional[str] = None,
                       run_label: str = "") -> EnnoDiagnosticV2FullRuntime:
    return EnnoDiagnosticV2FullRuntime(
        domain_tags=parse_domain_tags(domain_tags), fast_cache_dir=fast_cache_dir, run_label=run_label,
    )
