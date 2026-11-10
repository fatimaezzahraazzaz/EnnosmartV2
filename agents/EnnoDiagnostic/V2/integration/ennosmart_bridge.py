import hashlib
from ennodiagnostic_v2.schemas import DocumentInput

def _doc_id(name, index):
    d = hashlib.sha1(f"{index}|{name}".encode()).hexdigest()[:10]
    return f"doc_{d}"

def from_ennosmart_documents(documents):
    out = []
    for i, doc in enumerate(documents or []):
        name = str(doc.get("document") or doc.get("file_name") or f"document_{i}")
        mode = doc.get("declared_mode") or doc.get("document_mode")
        dtype = str(doc.get("document_type") or "").lower()
        if not mode:
            if "pre_cir" in dtype:
                mode = "pre_cir"
            elif "cir" in dtype:
                mode = "cir"

        out.append(DocumentInput(
            document_id=str(doc.get("document_id") or _doc_id(name, i)),
            name=name,
            text=str(doc.get("text") or ""),
            declared_mode=mode,
            metadata={
                "source_path": doc.get("source_path"),
                "document_type": doc.get("document_type"),
                "content_origin": doc.get("content_origin"),
                "source_policy": doc.get("source_policy"),
                "page": doc.get("page"),
            },
        ))
    return out

KIND_TO_PACK = {
    "objective": "objectifs_locaux",
    "lock": "verrous_rnd_locaux",
    "method": "methodes_locales",
    "parameter": "parametres_locaux",
    "result": "resultats_locaux",
}

def to_legacy_evidence_pack(result):
    pack = {
        "objectifs_locaux": [],
        "verrous_rnd_locaux": [],
        "methodes_locales": [],
        "parametres_locaux": [],
        "resultats_locaux": [],
        "limites_locales": [],
        "contributions_locales": [],
        "etat_art_local": [],
        "evidence_catalog": [],
    }

    for entity in result.entities:
        key = KIND_TO_PACK.get(entity.kind)
        if not key:
            continue
        pack[key].append({
            "passage_id": entity.entity_id,
            "role": entity.kind,
            "text": entity.statement,
            "documents": entity.document_ids,
            "needs_human_validation": entity.needs_review or entity.kind == "lock",
            "source_item_ids": entity.source_item_ids,
            "evidence_ids": [e.evidence_id for e in entity.evidences],
            "v2_entity": True,
        })
        for ev in entity.evidences:
            pack["evidence_catalog"].append({
                "evidence_id": ev.evidence_id,
                "entity_id": entity.entity_id,
                "document": ev.document_name,
                "chunk_id": ev.chunk_id,
                "section_title": ev.section_title,
                "page": ev.page,
                "text": ev.quote,
                "quote_verified": ev.quote_verified,
            })
    return pack
