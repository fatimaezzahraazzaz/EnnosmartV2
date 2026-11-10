-- Additive migration. Existing documents, projects and Chroma are untouched.
BEGIN;
CREATE TABLE IF NOT EXISTS cir_history_cache (
    cache_key VARCHAR(64) PRIMARY KEY,
    organisme_id VARCHAR(255) NOT NULL,
    kind VARCHAR(30) NOT NULL,
    document_id VARCHAR(700),
    payload JSON NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_cir_history_cache_organisme_id ON cir_history_cache (organisme_id);
CREATE INDEX IF NOT EXISTS ix_cir_history_cache_kind ON cir_history_cache (kind);
CREATE INDEX IF NOT EXISTS ix_cir_history_cache_document_id ON cir_history_cache (document_id);
CREATE TABLE IF NOT EXISTS cir_organism_aliases (
    alias_key VARCHAR(255) PRIMARY KEY,
    organisme_id VARCHAR(255) NOT NULL,
    validated BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_cir_organism_aliases_organisme_id ON cir_organism_aliases (organisme_id);
COMMIT;
