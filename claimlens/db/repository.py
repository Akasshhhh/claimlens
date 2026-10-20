"""Parameterized PostgreSQL repository for runs and prediction rows."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .schema import create_schema


def _jsonable(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (datetime, UUID)):
        return str(value)
    return str(value)


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list, tuple, str, int, float, bool)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _text(value: Any, *, limit: int = 500) -> str | None:
    if value is None:
        return None
    return str(value).replace("\n", " ").replace("\r", " ")[:limit]


def _uuid(value: str | UUID) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


class PostgresRunRepository:
    """A deliberately small repository backed by a real PostgreSQL server.

    Each operation uses a short-lived connection.  This keeps the class safe
    for FastAPI's sync and async test clients and avoids sharing a psycopg
    connection across event loops.  A caller can inject ``connection_factory``
    in tests without changing SQL or switching to SQLite.
    """

    def __init__(
        self,
        database_url: str | None = None,
        *,
        dsn: str | None = None,
        connection_factory: Any | None = None,
        initialize: bool = False,
        **connect_kwargs: Any,
    ) -> None:
        self.database_url = database_url or dsn
        if not self.database_url:
            raise ValueError("DATABASE_URL is required")
        self._connect = connection_factory or psycopg.connect
        self._connect_kwargs = dict(connect_kwargs)
        self._schema_ready = False
        self._schema_lock = threading.Lock()
        if initialize:
            self.ensure_schema()

    @classmethod
    def from_url(cls, database_url: str, **kwargs: Any) -> "PostgresRunRepository":
        return cls(database_url, **kwargs)

    from_dsn = from_url
    from_database_url = from_url

    @contextmanager
    def connection(self) -> Iterator[Any]:
        """Yield a psycopg connection configured to return dict rows."""

        try:
            connection = self._connect(self.database_url, row_factory=dict_row, **self._connect_kwargs)
        except TypeError:
            # Small fake factories often accept only the DSN; real psycopg
            # always supports row_factory.
            connection = self._connect(self.database_url)
        try:
            yield connection
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with self.connection() as connection:
                create_schema(connection)
                commit = getattr(connection, "commit", None)
                if callable(commit):
                    commit()
            self._schema_ready = True

    create_schema = ensure_schema

    @staticmethod
    def _run_metadata(meta: Mapping[str, Any]) -> dict[str, Any]:
        settings = _jsonable(meta.get("settings", {}))
        config = _jsonable(meta.get("config", settings if isinstance(settings, dict) else {}))
        file_hashes = _jsonable(meta.get("file_hashes", meta.get("hashes", {})))
        counts = _jsonable(meta.get("counts", {}))
        available = _jsonable(meta.get("available_metrics", []))
        return {
            "run_id": meta.get("run_id", meta.get("id")),
            "status": str(meta.get("status", "running")),
            "split": str(meta.get("split", "unknown")),
            "agent_version": str(meta.get("agent_version", "unknown")),
            "prompt_version": meta.get("prompt_version"),
            "dataset_version": meta.get("dataset_version"),
            "evaluator_version": meta.get("evaluator_version"),
            "fact_version": meta.get("fact_version"),
            "provider": meta.get("provider"),
            "model": meta.get("model"),
            "settings": settings if isinstance(settings, dict) else {},
            "config": config if isinstance(config, dict) else {},
            "source_revision": meta.get("source_revision"),
            "file_hashes": file_hashes if isinstance(file_hashes, dict) else {},
            "counts": counts if isinstance(counts, dict) else {},
            "scores": _jsonable(meta.get("scores", meta.get("metrics"))),
            "available_metrics": available,
            "metadata": _jsonable(meta.get("metadata", {})),
        }

    def create_run(self, meta: Mapping[str, Any] | None = None, **kwargs: Any) -> str:
        """Insert one run metadata row and return its UUID string."""

        self.ensure_schema()
        values = self._run_metadata({**dict(meta or {}), **kwargs})
        run_id = _uuid(values["run_id"]) if values["run_id"] else uuid4()
        now = datetime.now(timezone.utc)
        started_at = now if values["status"] in {"running", "started"} else None
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO runs (
                        run_id, status, split, agent_version, prompt_version,
                        dataset_version, evaluator_version, fact_version,
                        provider, model, settings, config, source_revision,
                        file_hashes, counts, scores, available_metrics,
                        metadata, started_at
                    ) VALUES (
                        %(run_id)s, %(status)s, %(split)s, %(agent_version)s,
                        %(prompt_version)s, %(dataset_version)s,
                        %(evaluator_version)s, %(fact_version)s, %(provider)s,
                        %(model)s, %(settings)s, %(config)s,
                        %(source_revision)s, %(file_hashes)s, %(counts)s,
                        %(scores)s, %(available_metrics)s, %(metadata)s,
                        %(started_at)s
                    )
                    ON CONFLICT (run_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        split = EXCLUDED.split,
                        agent_version = EXCLUDED.agent_version,
                        prompt_version = EXCLUDED.prompt_version,
                        dataset_version = EXCLUDED.dataset_version,
                        evaluator_version = EXCLUDED.evaluator_version,
                        fact_version = EXCLUDED.fact_version,
                        provider = EXCLUDED.provider,
                        model = EXCLUDED.model,
                        settings = EXCLUDED.settings,
                        config = EXCLUDED.config,
                        source_revision = EXCLUDED.source_revision,
                        file_hashes = EXCLUDED.file_hashes,
                        counts = EXCLUDED.counts,
                        scores = EXCLUDED.scores,
                        available_metrics = EXCLUDED.available_metrics,
                        metadata = EXCLUDED.metadata
                    """,
                    {
                        "run_id": run_id,
                        "status": values["status"],
                        "split": values["split"],
                        "agent_version": values["agent_version"],
                        "prompt_version": values["prompt_version"],
                        "dataset_version": values["dataset_version"],
                        "evaluator_version": values["evaluator_version"],
                        "fact_version": values["fact_version"],
                        "provider": values["provider"],
                        "model": values["model"],
                        "settings": Jsonb(values["settings"]),
                        "config": Jsonb(values["config"]),
                        "source_revision": values["source_revision"],
                        "file_hashes": Jsonb(values["file_hashes"]),
                        "counts": Jsonb(values["counts"]),
                        "scores": Jsonb(values["scores"]) if values["scores"] is not None else None,
                        "available_metrics": Jsonb(values["available_metrics"]),
                        "metadata": Jsonb(values["metadata"]),
                        "started_at": started_at,
                    },
                )
            connection.commit()
        return str(run_id)

    @staticmethod
    def _claim_values(claim: Any) -> dict[str, Any]:
        raw = _jsonable(claim)
        if not isinstance(raw, Mapping):
            raise TypeError("claim must be a mapping or Pydantic model")
        row = dict(raw)
        match = row.get("match")
        match_fields = match if isinstance(match, Mapping) else {}
        return {
            "claim_id": str(row["claim_id"]),
            "start_char": int(row["start_char"]),
            "end_char": int(row["end_char"]),
            "quote": str(row["quote"]),
            "label": str(row["label"]),
            "fact_ids": list(row.get("fact_ids") or row.get("evidence_fact_ids") or []),
            "reason": str(row.get("reason", row.get("explanation", ""))),
            "gold_claim_id": row.get("gold_claim_id", match_fields.get("gold_claim_id")),
            "expected_label": row.get("expected_label", match_fields.get("expected_label")),
            "label_correct": row.get("label_correct", match_fields.get("label_correct")),
            "match": _jsonable(match) if match is not None else None,
        }

    def save_answer_result(
        self,
        run_id: str | UUID,
        answer_id: str,
        status: str,
        error: Mapping[str, Any] | str | None = None,
        latency_ms: float | None = None,
        trace_ref: str | None = None,
        claims: list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Upsert one terminal answer row and its accepted claims."""

        self.ensure_schema()
        run_uuid = _uuid(run_id)
        if status not in {"ok", "error"}:
            raise ValueError("answer status must be ok or error")
        if error is None and (kwargs.get("error_type") is not None or kwargs.get("error_message") is not None):
            error = {
                "type": kwargs.get("error_type"),
                "message": kwargs.get("error_message"),
            }
        if trace_ref is None:
            trace_ref = kwargs.get("trace_id")
        error_payload = _jsonable(error) if error is not None else None
        if isinstance(error_payload, str):
            error_payload = {"message": _text(error_payload)}
        error_type = error_payload.get("type", error_payload.get("error_type")) if isinstance(error_payload, Mapping) else None
        error_message = error_payload.get("message", error_payload.get("error_message")) if isinstance(error_payload, Mapping) else None
        if claims is None:
            claims = kwargs.get("claim_results", kwargs.get("predictions", []))
        values = [self._claim_values(claim) for claim in (claims or [])] if status == "ok" else []
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO answer_results (
                        run_id, answer_id, status, error_type, error_message,
                        error, latency_ms, trace_ref
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, answer_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        error_type = EXCLUDED.error_type,
                        error_message = EXCLUDED.error_message,
                        error = EXCLUDED.error,
                        latency_ms = EXCLUDED.latency_ms,
                        trace_ref = EXCLUDED.trace_ref
                    """,
                    (
                        run_uuid,
                        answer_id,
                        status,
                        _text(error_type),
                        _text(error_message),
                        Jsonb(error_payload) if error_payload is not None else None,
                        latency_ms,
                        trace_ref,
                    ),
                )
                cursor.execute(
                    "DELETE FROM claim_results WHERE run_id = %s AND answer_id = %s",
                    (run_uuid, answer_id),
                )
                for claim in values:
                    self._insert_claim(cursor, run_uuid, answer_id, claim)
            connection.commit()

    def _insert_claim(self, cursor: Any, run_id: UUID, answer_id: str, claim: Mapping[str, Any]) -> None:
        cursor.execute(
            """
            INSERT INTO claim_results (
                run_id, answer_id, claim_id, start_char, end_char, quote,
                label, fact_ids, reason, gold_claim_id, expected_label,
                label_correct, match
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (run_id, answer_id, start_char, end_char) DO UPDATE SET
                claim_id = EXCLUDED.claim_id,
                quote = EXCLUDED.quote,
                label = EXCLUDED.label,
                fact_ids = EXCLUDED.fact_ids,
                reason = EXCLUDED.reason,
                gold_claim_id = EXCLUDED.gold_claim_id,
                expected_label = EXCLUDED.expected_label,
                label_correct = EXCLUDED.label_correct,
                match = EXCLUDED.match
            """,
            (
                run_id,
                answer_id,
                claim["claim_id"],
                claim["start_char"],
                claim["end_char"],
                claim["quote"],
                claim["label"],
                Jsonb(claim["fact_ids"]),
                claim["reason"],
                claim["gold_claim_id"],
                claim["expected_label"],
                claim["label_correct"],
                Jsonb(claim["match"]) if claim["match"] is not None else None,
            ),
        )

    def save_claim_result(self, run_id: str | UUID, answer_id: str, claim: Any) -> None:
        """Persist one accepted claim; its terminal answer row must exist."""

        self.ensure_schema()
        values = self._claim_values(claim)
        with self.connection() as connection:
            with connection.cursor() as cursor:
                self._insert_claim(cursor, _uuid(run_id), answer_id, values)
            connection.commit()

    def finish_run(
        self,
        run_id: str | UUID,
        status: str,
        counts: Mapping[str, Any] | None = None,
        metrics: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.ensure_schema()
        if not status:
            raise ValueError("run status is required")
        run_uuid = _uuid(run_id)
        if metrics is None:
            metrics = kwargs.get("scores")
        metrics_value = _jsonable(metrics) if metrics is not None else None
        available = kwargs.get("available_metrics", [])
        if isinstance(metrics_value, Mapping):
            available = [key for key, value in metrics_value.items() if value is not None]
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE runs
                    SET status = %s,
                        counts = %s,
                        scores = %s,
                        available_metrics = %s,
                        completed_at = CURRENT_TIMESTAMP
                    WHERE run_id = %s
                    """,
                    (
                        status,
                        Jsonb(_jsonable(counts or {})),
                        Jsonb(metrics_value) if metrics_value is not None else None,
                        Jsonb(available),
                        run_uuid,
                    ),
                )
            connection.commit()

    def save_outcome(self, run_id: str | UUID, outcome: Any) -> None:
        raw = _jsonable(outcome)
        if not isinstance(raw, Mapping):
            raise TypeError("outcome must be a mapping or Pydantic model")
        error = None
        if raw.get("error_type") or raw.get("error_message"):
            error = {"type": raw.get("error_type"), "message": raw.get("error_message")}
        self.save_answer_result(
            run_id,
            str(raw["answer_id"]),
            str(raw.get("status", "error")),
            error,
            raw.get("latency_ms"),
            raw.get("trace_id", raw.get("trace_ref")),
            list(raw.get("claims") or []),
        )

    def get_run(self, run_id: str | UUID) -> dict[str, Any] | None:
        self.ensure_schema()
        run_uuid = _uuid(run_id)
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM runs WHERE run_id = %s", (run_uuid,))
                run = cursor.fetchone()
                if run is None:
                    return None
                cursor.execute(
                    """
                    SELECT * FROM answer_results
                    WHERE run_id = %s
                    ORDER BY answer_id ASC, answer_result_id ASC
                    """,
                    (run_uuid,),
                )
                answers = list(cursor.fetchall())
                cursor.execute(
                    """
                    SELECT * FROM claim_results
                    WHERE run_id = %s
                    ORDER BY answer_id ASC, start_char ASC, end_char ASC,
                             claim_id ASC, claim_result_id ASC
                    """,
                    (run_uuid,),
                )
                claims = list(cursor.fetchall())
        by_answer: dict[str, list[dict[str, Any]]] = {}
        joined_claims: list[dict[str, Any]] = []
        for row in claims:
            claim_value = self._claim_dict(row)
            by_answer.setdefault(row["answer_id"], []).append(claim_value)
            joined_claims.append({"answer_id": row["answer_id"], **claim_value})
        answer_values = [self._answer_dict(row, by_answer.get(row["answer_id"], [])) for row in answers]
        result = self._run_dict(run)
        result["answer_results"] = answer_values
        result["answers"] = answer_values
        result["answer_outcomes"] = answer_values
        result["claim_results"] = joined_claims
        return result

    @staticmethod
    def _claim_dict(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "claim_id": row["claim_id"],
            "start_char": row["start_char"],
            "end_char": row["end_char"],
            "quote": row["quote"],
            "label": row["label"],
            "fact_ids": _json_value(row.get("fact_ids"), []),
            "reason": row.get("reason", ""),
            "gold_claim_id": row.get("gold_claim_id"),
            "expected_label": row.get("expected_label"),
            "label_correct": row.get("label_correct"),
            "match": _json_value(row.get("match"), None),
        }

    @staticmethod
    def _answer_dict(row: Mapping[str, Any], claims: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "answer_id": row["answer_id"],
            "status": row["status"],
            "error_type": row.get("error_type"),
            "error_message": row.get("error_message"),
            "error": _json_value(row.get("error"), None),
            "latency_ms": row.get("latency_ms"),
            "trace_ref": row.get("trace_ref"),
            "trace_id": row.get("trace_ref"),
            "claims": claims,
        }

    @staticmethod
    def _run_dict(row: Mapping[str, Any]) -> dict[str, Any]:
        created = row.get("created_at")
        started = row.get("started_at")
        completed = row.get("completed_at")
        return {
            "run_id": str(row["run_id"]),
            "status": row["status"],
            "split": row["split"],
            "agent_version": row["agent_version"],
            "prompt_version": row.get("prompt_version"),
            "dataset_version": row.get("dataset_version"),
            "evaluator_version": row.get("evaluator_version"),
            "fact_version": row.get("fact_version"),
            "provider": row.get("provider"),
            "model": row.get("model"),
            "settings": _json_value(row.get("settings"), {}),
            "config": _json_value(row.get("config"), {}),
            "source_revision": row.get("source_revision"),
            "file_hashes": _json_value(row.get("file_hashes"), {}),
            "counts": _json_value(row.get("counts"), {}),
            "scores": _json_value(row.get("scores"), None),
            "metrics": _json_value(row.get("scores"), None),
            "available_metrics": _json_value(row.get("available_metrics"), []),
            "metadata": _json_value(row.get("metadata"), {}),
            "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
            "started_at": started.isoformat() if hasattr(started, "isoformat") else started,
            "completed_at": completed.isoformat() if hasattr(completed, "isoformat") else completed,
        }

    def close(self) -> None:
        """Compatibility hook; connections are intentionally per operation."""

    def __enter__(self) -> "PostgresRunRepository":
        self.ensure_schema()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


RunRepository = PostgresRunRepository

# Descriptive aliases used by small integrations and importers.
PostgresRunRepository.insert_run = PostgresRunRepository.create_run
PostgresRunRepository.insert_answer_result = PostgresRunRepository.save_answer_result
PostgresRunRepository.insert_claim_result = PostgresRunRepository.save_claim_result
PostgresRunRepository.retrieve_run = PostgresRunRepository.get_run


__all__ = ["PostgresRunRepository", "RunRepository"]
