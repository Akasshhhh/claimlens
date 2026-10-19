"""The small PostgreSQL schema used by the API.

The schema is deliberately kept as ordinary SQL so a clean database can be
bootstrapped without a migration tool.  Values supplied by callers are always
bound parameters in the repository; this module only contains fixed DDL.
"""

from __future__ import annotations

from typing import Any


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id UUID PRIMARY KEY,
    status TEXT NOT NULL,
    split TEXT NOT NULL,
    agent_version TEXT NOT NULL,
    prompt_version TEXT,
    dataset_version TEXT,
    evaluator_version TEXT,
    fact_version TEXT,
    provider TEXT,
    model TEXT,
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_revision TEXT,
    file_hashes JSONB NOT NULL DEFAULT '{}'::jsonb,
    counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    scores JSONB,
    available_metrics JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS answer_results (
    answer_result_id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    answer_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'error')),
    error_type TEXT,
    error_message TEXT,
    error JSONB,
    latency_ms DOUBLE PRECISION,
    trace_ref TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, answer_id)
);

CREATE TABLE IF NOT EXISTS claim_results (
    claim_result_id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    answer_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    start_char INTEGER NOT NULL CHECK (start_char >= 0),
    end_char INTEGER NOT NULL CHECK (end_char > start_char),
    quote TEXT NOT NULL,
    label TEXT NOT NULL CHECK (label IN ('supported', 'contradicted', 'unknown')),
    fact_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    reason TEXT NOT NULL DEFAULT '',
    gold_claim_id TEXT,
    expected_label TEXT,
    label_correct BOOLEAN,
    match JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id, answer_id)
        REFERENCES answer_results(run_id, answer_id)
        ON DELETE CASCADE,
    UNIQUE (run_id, answer_id, start_char, end_char)
);

CREATE INDEX IF NOT EXISTS answer_results_run_answer_idx
    ON answer_results (run_id, answer_id);
CREATE INDEX IF NOT EXISTS claim_results_run_answer_span_idx
    ON claim_results (run_id, answer_id, start_char, end_char, claim_id);
"""


def create_schema(connection: Any) -> None:
    """Create all persistence tables on an open psycopg connection."""

    with connection.cursor() as cursor:
        cursor.execute(SCHEMA_SQL)


initialize_schema = create_schema
init_schema = create_schema


__all__ = ["SCHEMA_SQL", "create_schema", "initialize_schema", "init_schema"]
