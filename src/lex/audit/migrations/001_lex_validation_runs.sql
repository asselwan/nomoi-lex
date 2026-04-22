-- Migration 001: Create lex.validation_runs table
-- Stores per-run aggregate metrics. No PHI — only hashed file IDs and counts.

CREATE SCHEMA IF NOT EXISTS lex;

CREATE TABLE lex.validation_runs (
    run_id              UUID        PRIMARY KEY,
    run_timestamp       TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id             TEXT        NOT NULL DEFAULT '',
    file_hash           TEXT        NOT NULL,
    claim_count         INTEGER     NOT NULL,
    error_count_total   INTEGER     NOT NULL DEFAULT 0,
    warning_count_total INTEGER     NOT NULL DEFAULT 0,
    info_count_total    INTEGER     NOT NULL DEFAULT 0,
    estimated_impact_aed_total NUMERIC(14,2) NOT NULL DEFAULT 0,
    engine_version      TEXT        NOT NULL
);

COMMENT ON TABLE lex.validation_runs IS
    'Audit log of validation runs — aggregate metrics only, no claim content.';
