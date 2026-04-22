-- Migration 002: Create lex.claim_outcomes table
-- Per-claim outcome record. No PHI — claim_id is hashed, no diagnosis/activity data.

CREATE TABLE lex.claim_outcomes (
    run_id              UUID        NOT NULL REFERENCES lex.validation_runs(run_id),
    claim_id_hash       TEXT        NOT NULL,
    drg_code            TEXT        NOT NULL DEFAULT '',
    encounter_type      TEXT        NOT NULL DEFAULT '',
    status              TEXT        NOT NULL CHECK (status IN ('BLOCKED', 'REVIEW', 'READY')),
    rule_ids_triggered  TEXT[]      NOT NULL DEFAULT '{}',

    PRIMARY KEY (run_id, claim_id_hash)
);

COMMENT ON TABLE lex.claim_outcomes IS
    'Per-claim validation outcome — hashed ID, status, and triggered rules only.';
