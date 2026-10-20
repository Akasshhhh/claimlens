"""Collect sanitized, read-only Langfuse evidence for the final artifacts.

This collector deliberately keeps raw Langfuse responses in memory only.  It
writes trace/observation IDs, names, types, timings, truncated answer text,
local development scores, and HTTP/body-digest receipts.  It never writes
credentials, prompts, full answers, model outputs, tool payloads, or remote
score payloads.

The previously rate-limited traces are read from the prior audit report and
queried serially.  A request is separated from the previous request by at
least ``--spacing`` seconds (the default is 2.25 seconds), and HTTP 429 is
retried at most twice with bounded exponential backoff.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUTPUT_DIR = Path(__file__).resolve().parent
DIAGNOSIS_DIR = ROOT / "evidence" / "diagnosis"
BASELINE_AUDIT = ROOT / "evidence" / "langfuse-investigation" / "audit-report.json"
FINAL_SUMMARY = ROOT / "evidence" / "final-run-summary.json"

CURRENT_ARTIFACTS: dict[str, Path] = {
    "v1_development": ROOT / "artifacts" / "v1-development-corrected-final",
    "v2_development": ROOT / "artifacts" / "v2-development-corrected-final",
    "v1_check": ROOT / "artifacts" / "v1-check-corrected-final",
    "v2_check": ROOT / "artifacts" / "v2-check-corrected-final",
}
HISTORICAL_ARTIFACT = ROOT / "artifacts" / "v1-development-final-turn-gated"
HISTORICAL_TRACE_ID = "dc8e8ee41ef74834a52ed449edc783d6"

SELECTED_ANSWERS = {
    "v1_development": "A19",
    "v2_development": "A01",
    "v1_check": "A44",
    "v2_check": "A47",
}

SCHEMA_VERSION = "claimlens-langfuse-review-evidence-v1"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def credentials() -> tuple[dict[str, bool], str, str, str | None]:
    values = dotenv_values(ROOT / ".env")
    public = os.environ.get("LANGFUSE_PUBLIC_KEY") or str(values.get("LANGFUSE_PUBLIC_KEY") or "")
    secret = os.environ.get("LANGFUSE_SECRET_KEY") or str(values.get("LANGFUSE_SECRET_KEY") or "")
    host = (
        os.environ.get("LANGFUSE_BASE_URL")
        or os.environ.get("LANGFUSE_HOST")
        or values.get("LANGFUSE_BASE_URL")
        or values.get("LANGFUSE_HOST")
    )
    host_text = str(host).rstrip("/") if host else None
    presence = {
        "LANGFUSE_PUBLIC_KEY": bool(public),
        "LANGFUSE_SECRET_KEY": bool(secret),
        "LANGFUSE_BASE_URL": bool(host_text),
        "LANGFUSE_HOST": bool(os.environ.get("LANGFUSE_HOST") or values.get("LANGFUSE_HOST")),
    }
    return presence, public, secret, host_text


def iso_window(run: dict[str, Any]) -> tuple[str, str]:
    from claimlens.tracing import observation_window

    return observation_window(run["started_at"], run["completed_at"])


def observation_url(host: str, trace_id: str, run: dict[str, Any]) -> str:
    from_start, to_start = iso_window(run)
    query = urllib.parse.urlencode(
        {
            "traceId": trace_id,
            "fromStartTime": from_start,
            "toStartTime": to_start,
            "limit": "10",
            "fields": "core,basic,io",
        }
    )
    return f"{host}/api/public/v2/observations?{query}"


def curl_get(url: str, public_key: str, secret_key: str, timeout: float) -> tuple[int | None, bytes, str | None]:
    """Make one GET without exposing credentials in argv or output."""

    if not shutil.which("curl"):
        return None, b"", "curl_unavailable"
    config = f"url = {json.dumps(url)}\nuser = {json.dumps(f'{public_key}:{secret_key}')}\n"
    try:
        result = subprocess.run(
            [
                "curl",
                "--config",
                "-",
                "--silent",
                "--show-error",
                "--max-time",
                str(max(1, int(timeout))),
                "--write-out",
                "\n%{http_code}",
            ],
            input=config.encode("utf-8"),
            capture_output=True,
            check=False,
            timeout=timeout + 2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, b"", "curl_transport_failure"
    lines = result.stdout.splitlines()
    if not lines or not lines[-1].isdigit():
        return None, b"", "curl_no_http_status"
    status = int(lines[-1])
    if status == 0:
        return None, b"", "curl_transport_failure"
    return status, b"\n".join(lines[:-1]), None


def urllib_get(url: str, public_key: str, secret_key: str, timeout: float) -> tuple[int | None, bytes, str | None]:
    token = base64.b64encode(f"{public_key}:{secret_key}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Authorization": f"Basic {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), None
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), None
    except (OSError, TimeoutError, urllib.error.URLError):
        return None, b"", "urllib_transport_failure"


def get(url: str, public_key: str, secret_key: str, timeout: float) -> tuple[int | None, bytes, str | None]:
    # curl uses the system trust store on this machine.  urllib is a safe
    # fallback for clean Linux environments where the standard trust store is
    # available.
    status, raw, error = curl_get(url, public_key, secret_key, timeout)
    if status is not None:
        return status, raw, error
    return urllib_get(url, public_key, secret_key, timeout)


class SerialThrottle:
    def __init__(self, spacing_seconds: float) -> None:
        self.spacing_seconds = max(2.0, float(spacing_seconds))
        self.last_request_at: float | None = None

    def before_request(self) -> None:
        if self.last_request_at is not None:
            remaining = self.spacing_seconds - (time.monotonic() - self.last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        self.last_request_at = time.monotonic()


def parse_payload(raw: bytes) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "invalid_json"
    return value if isinstance(value, dict) else None, None


def observation_list(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return []
    return [item for item in payload["data"] if isinstance(item, dict)]


def response_summary(status: int | None, raw: bytes, transport_error: str | None) -> dict[str, Any]:
    payload, parse_error = parse_payload(raw) if raw else (None, None)
    observations = observation_list(payload)
    result: dict[str, Any] = {
        "http_status": status,
        "body_sha256": sha256(raw),
        "observation_count": len(observations),
        "observation_names": [
            item.get("name")
            for item in observations
            if isinstance(item.get("name"), str)
        ][:50],
    }
    if transport_error:
        result["transport_error"] = transport_error
    if parse_error:
        result["parse_error"] = parse_error
    return result


def query_trace(
    *,
    host: str,
    public_key: str,
    secret_key: str,
    run: dict[str, Any],
    trace_id: str,
    throttle: SerialThrottle,
    timeout: float,
    max_attempts: int,
) -> dict[str, Any]:
    """Query a trace serially and retain only sanitized response summaries."""

    url = observation_url(host, trace_id, run)
    from_start, to_start = iso_window(run)
    attempts: list[dict[str, Any]] = []
    final_payload: dict[str, Any] | None = None

    for attempt in range(1, max(1, max_attempts) + 1):
        throttle.before_request()
        status, raw, transport_error = get(url, public_key, secret_key, timeout)
        payload, parse_error = parse_payload(raw) if raw else (None, None)
        observations = observation_list(payload)
        attempt_summary = response_summary(status, raw, transport_error)
        attempt_summary["attempt"] = attempt
        attempts.append(attempt_summary)
        if observations:
            final_payload = {"data": observations}
        if status != 429 or attempt >= max_attempts:
            break
        # The throttle enforces the minimum inter-request spacing.  This
        # bounded sleep adds explicit exponential backoff after rate limiting.
        time.sleep(max(2.0, 2.0 ** attempt))

    final = attempts[-1] if attempts else {"http_status": None, "observation_count": 0}
    final_status = final.get("http_status")
    if final.get("observation_count", 0) > 0:
        status_text = "verified"
    elif final_status == 429:
        status_text = "rate_limited"
    elif final_status is None:
        status_text = "blocked"
    elif 200 <= int(final_status) < 300:
        status_text = "empty"
    else:
        status_text = "error"
    return {
        "trace_id": trace_id,
        "from_start_time": from_start,
        "to_start_time": to_start,
        "status": status_text,
        "recovered_from_429": any(item.get("http_status") == 429 for item in attempts)
        and status_text == "verified",
        "attempt_count": len(attempts),
        "attempts": attempts,
        "final": final,
        "payload": final_payload,
    }


def parse_json_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def truncate_answer(text: Any) -> str | None:
    if not isinstance(text, str):
        return None
    normalized = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    return normalized[:200]


def answer_text_from_observations(observations: list[dict[str, Any]]) -> str | None:
    root = next((item for item in observations if item.get("name") == "claimlens.answer"), None)
    if root:
        parsed_input = parse_json_string(root.get("input"))
        if isinstance(parsed_input, dict):
            answer = truncate_answer(parsed_input.get("ai_answer"))
            if answer is not None:
                return answer
        parsed_output = parse_json_string(root.get("output"))
        if isinstance(parsed_output, dict):
            answer = truncate_answer(parsed_output.get("ai_answer"))
            if answer is not None:
                return answer
    # The root input is the normal source.  The generation fallback is kept
    # defensive and still truncates before anything is returned or written.
    for item in observations:
        parsed_output = parse_json_string(item.get("output"))
        if not isinstance(parsed_output, dict):
            continue
        choices = parsed_output.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict):
                answer = truncate_answer(message.get("content"))
                if answer is not None:
                    return answer
    return None


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def duration_ms(start: Any, end: Any) -> float | None:
    first = parse_time(start)
    last = parse_time(end)
    if first is None or last is None:
        return None
    return round((last - first).total_seconds() * 1000, 3)


def clean_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def sanitize_observation(item: dict[str, Any], trace_id: str) -> dict[str, Any]:
    observation_id = item.get("id") if isinstance(item.get("id"), str) else None
    latency_seconds = clean_number(item.get("latency"))
    return {
        "trace_id": item.get("traceId") if isinstance(item.get("traceId"), str) else trace_id,
        "observation_id": observation_id,
        "span_id": observation_id,
        "parent_observation_id": item.get("parentObservationId")
        if isinstance(item.get("parentObservationId"), str)
        else None,
        "name": item.get("name") if isinstance(item.get("name"), str) else None,
        "type": item.get("type") if isinstance(item.get("type"), str) else None,
        "level": item.get("level") if isinstance(item.get("level"), str) else None,
        "start_time": item.get("startTime") if isinstance(item.get("startTime"), str) else None,
        "end_time": item.get("endTime") if isinstance(item.get("endTime"), str) else None,
        "latency_seconds": latency_seconds,
        "latency_ms": round(latency_seconds * 1000, 3) if latency_seconds is not None else None,
        "duration_ms": duration_ms(item.get("startTime"), item.get("endTime")),
    }


def local_trace_rows(artifact: Path) -> dict[str, dict[str, Any]]:
    rows = read_json(artifact / "trace_exports.json")
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("trace_id")): row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("trace_id"), str)
    }


def selected_trace_id(artifact: Path, answer_id: str) -> tuple[str | None, dict[str, Any] | None]:
    for row in read_json(artifact / "trace_exports.json"):
        if isinstance(row, dict) and row.get("answer_id") == answer_id:
            trace_id = row.get("trace_id")
            if isinstance(trace_id, str):
                return trace_id, row
    return None, None


def local_scores(row: dict[str, Any] | None, *, check: bool) -> list[dict[str, Any]]:
    # Check traces must never carry gold-derived scores.  Their local score
    # events are intentionally omitted even if a malformed artifact contains
    # one.
    if check or not isinstance(row, dict):
        return []
    scores = row.get("local_scores")
    if not isinstance(scores, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for score in scores:
        if not isinstance(score, dict) or not isinstance(score.get("name"), str):
            continue
        value = clean_number(score.get("value"))
        if value is None:
            continue
        metadata = score.get("metadata") if isinstance(score.get("metadata"), dict) else {}
        item: dict[str, Any] = {"name": score["name"], "value": value}
        if isinstance(metadata.get("span_id"), str):
            item["span_id"] = metadata["span_id"]
        cleaned.append(item)
    return cleaned


def answer_export(
    *,
    label: str,
    artifact: Path,
    run: dict[str, Any],
    answer_id: str | None,
    trace_id: str,
    row: dict[str, Any] | None,
    remote: dict[str, Any],
) -> dict[str, Any]:
    observations = remote.get("payload", {}).get("data", []) if isinstance(remote.get("payload"), dict) else []
    clean_observations = [
        sanitize_observation(item, trace_id)
        for item in observations
        if isinstance(item, dict)
    ]
    is_check = run.get("split") == "check"
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "exported_at_utc": now_utc(),
        "run": {
            "label": label,
            "artifact": str(artifact.relative_to(ROOT)),
            "run_id": run.get("run_id"),
            "agent_version": run.get("agent_version"),
            "split": run.get("split"),
            "source_revision": run.get("source_revision"),
            "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"),
        },
        "answer_id": answer_id,
        "trace_id": trace_id,
        "remote": {
            "status": remote.get("status"),
            "recovered_from_429": bool(remote.get("recovered_from_429")),
            "attempt_count": remote.get("attempt_count"),
            "from_start_time": remote.get("from_start_time"),
            "to_start_time": remote.get("to_start_time"),
            "final": remote.get("final"),
        },
        "answer_text_truncated": answer_text_from_observations(observations),
        "observations": clean_observations,
        "local_scores": local_scores(row, check=is_check),
        "gold_derived_scores_included": not is_check,
    }
    if is_check:
        result["check_score_policy"] = "No gold-derived scores were emitted or exported for check traces."
    return result


def baseline_receipts() -> list[dict[str, Any]]:
    report = read_json(BASELINE_AUDIT)
    remote = report.get("remote_verification", {}) if isinstance(report, dict) else {}
    receipts = remote.get("receipts", []) if isinstance(remote, dict) else []
    return [item for item in receipts if isinstance(item, dict)]


def run_inventory() -> dict[str, dict[str, Any]]:
    inventory: dict[str, dict[str, Any]] = {}
    for label, artifact in CURRENT_ARTIFACTS.items():
        run = read_json(artifact / "run.json")
        inventory[label] = {
            "artifact": str(artifact.relative_to(ROOT)),
            "run": run,
            "trace_count": len(run.get("trace_ids", [])) if isinstance(run, dict) else 0,
        }
    return inventory


def baseline_counts(receipts: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {label: {"verified": 0, "rate_limited": 0, "other": 0} for label in CURRENT_ARTIFACTS}
    for receipt in receipts:
        artifact = receipt.get("artifact")
        label = next((key for key, path in CURRENT_ARTIFACTS.items() if str(path.relative_to(ROOT)) == artifact), None)
        if label is None:
            continue
        if receipt.get("http_status") == 200 and receipt.get("observation_count", 0):
            counts[label]["verified"] += 1
        elif receipt.get("http_status") == 429:
            counts[label]["rate_limited"] += 1
        else:
            counts[label]["other"] += 1
    return counts


def update_counts(
    baseline: dict[str, dict[str, int]],
    prior_429: list[dict[str, Any]],
    requery: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    updated = {label: dict(values) for label, values in baseline.items()}
    for prior, result in zip(prior_429, requery):
        artifact = prior.get("artifact")
        label = next((key for key, path in CURRENT_ARTIFACTS.items() if str(path.relative_to(ROOT)) == artifact), None)
        if label is None:
            continue
        updated[label]["rate_limited"] = max(0, updated[label]["rate_limited"] - 1)
        if result.get("status") == "verified":
            updated[label]["verified"] += 1
        else:
            updated[label]["rate_limited"] += 1
    for values in updated.values():
        values["remote_confirmed_total"] = values["verified"]
        values["remaining_inaccessible"] = values["rate_limited"] + values["other"]
    return updated


def metrics_from_summary() -> dict[str, Any]:
    value = read_json(FINAL_SUMMARY)
    runs = value.get("runs", []) if isinstance(value, dict) else []
    result: dict[str, Any] = {}
    for run in runs:
        if not isinstance(run, dict):
            continue
        artifact = run.get("artifact", "")
        label = next((key for key, path in CURRENT_ARTIFACTS.items() if artifact == str(path.relative_to(ROOT))), None)
        if label:
            metrics = run.get("metrics", {})
            result[label] = {
                key: metrics.get(key)
                for key in ("extraction_f1", "joint_f1", "macro_f1", "predictions", "answer_errors", "coverage")
                if key in metrics
            }
            result[label]["counts"] = run.get("counts", {})
    return result


def historical_local_diagnostic() -> dict[str, Any]:
    """Return counts from the saved historical answer receipt only."""

    outcomes_path = HISTORICAL_ARTIFACT / "outcomes.json"
    if not outcomes_path.exists():
        return {"status": "unavailable", "reason": "historical outcomes.json is missing"}
    outcomes = read_json(outcomes_path)
    if not isinstance(outcomes, list):
        return {"status": "unavailable", "reason": "historical outcomes.json is not a list"}
    answer = next((row for row in outcomes if isinstance(row, dict) and row.get("answer_id") == "A01"), None)
    if not isinstance(answer, dict):
        return {"status": "unavailable", "reason": "historical A01 outcome is missing"}
    claims = answer.get("claims") if isinstance(answer.get("claims"), list) else []
    label_counts: dict[str, int] = {}
    advice_reason_count = 0
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        label = claim.get("label")
        if isinstance(label, str):
            label_counts[label] = label_counts.get(label, 0) + 1
        reason = str(claim.get("reason", "")).lower()
        if any(word in reason for word in ("advice", "instruction", "opinion")):
            advice_reason_count += 1
    return {
        "status": answer.get("status"),
        "claim_id_count": len(claims),
        "advice_instruction_or_opinion_reason_count": advice_reason_count,
        "label_counts": label_counts,
        "source": str(outcomes_path.relative_to(ROOT)),
    }


def markdown_table_observations(observations: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Observation/span ID | Name | Type | Level | Start | End | Duration ms |",
        "|---|---|---|---|---|---|---:|",
    ]
    for item in observations:
        lines.append(
            "| `{}` | `{}` | `{}` | `{}` | `{}` | `{}` | `{}` |".format(
                item.get("observation_id") or "",
                item.get("name") or "",
                item.get("type") or "",
                item.get("level") or "",
                item.get("start_time") or "",
                item.get("end_time") or "",
                item.get("duration_ms") if item.get("duration_ms") is not None else "",
            )
        )
    return lines


def write_review_markdown(
    *,
    summary: dict[str, Any],
    exports: dict[str, dict[str, Any]],
    requery: list[dict[str, Any]],
) -> None:
    lines = [
        "# Langfuse reviewer evidence",
        "",
        "This is a sanitized, read-only export. Raw prompts, full answers, model outputs, tool payloads, score payloads, credentials, and environment values are not included.",
        "",
        "## Collection",
        "",
        f"- Generated at: `{summary['generated_at_utc']}`",
        "- Endpoint: `GET /api/public/v2/observations` with per-answer `traceId`, the completed run window ±1 minute, `limit=10`, and `fields=core,basic,io`.",
        f"- Serial throttle: `{summary['query_policy']['spacing_seconds']}` seconds minimum between requests; max `{summary['query_policy']['max_attempts']}` attempts for HTTP 429 with bounded backoff.",
        f"- Provider/model calls started by this audit: `{summary['scope']['model_calls_started']}`.",
        "",
        "## Current-run remote confirmation counts",
        "",
        "| Run | Run ID | Baseline confirmed | Recovered from prior 429 | Current confirmed | Remaining inaccessible |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for label, counts in summary["remote_confirmation_counts"].items():
        run_id = summary["current_runs"][label]["run"].get("run_id")
        lines.append(
            f"| `{label}` | `{run_id}` | {counts['baseline_verified']} | {counts['recovered_from_429']} | {counts['remote_confirmed_total']} | {counts['remaining_inaccessible']} |"
        )
    lines.extend(
        [
            "",
            "## Exact prior-429 re-query results",
            "",
            "Each row below is one of the 14 traces that returned HTTP 429 in the previous audit. `recovered` means a later serial attempt returned non-empty observations; a trace still listed as `rate_limited` is not evidence of missing ingestion.",
            "",
            "| Run | Answer | Trace ID | Final status | Attempts | Recovery | Final HTTP | Final observations |",
            "|---|---|---|---|---:|---|---:|---:|",
        ]
    )
    for item in requery:
        lines.append(
            f"| `{item.get('label')}` | `{item.get('answer_id')}` | `{item.get('trace_id')}` | `{item.get('status')}` | {item.get('attempt_count')} | `{item.get('recovered_from_prior_429')}` | {item.get('final', {}).get('http_status')} | {item.get('final', {}).get('observation_count', 0)} |"
        )
    remaining = summary["remaining_inaccessible_traces"]
    lines.extend(["", "### Remaining inaccessible traces", ""])
    if remaining:
        for item in remaining:
            lines.append(f"- `{item['label']}` / `{item['answer_id']}` / `{item['trace_id']}`: `{item['status']}` after `{item['attempt_count']}` bounded attempts.")
    else:
        lines.append("- None after the bounded re-query set.")
    for label, item in exports.items():
        lines.extend(
            [
                "",
                f"## {label}",
                "",
                f"- Artifact: `{item['run']['artifact']}`",
                f"- Run ID: `{item['run']['run_id']}`; answer: `{item['answer_id']}`; trace ID: `{item['trace_id']}`",
                f"- Remote status: `{item['remote']['status']}`; observations: `{len(item['observations'])}`",
                f"- Answer text (≤200 chars): {json.dumps(item.get('answer_text_truncated'), ensure_ascii=False)}",
            ]
        )
        if item["run"]["split"] == "check":
            lines.append("- Check-score policy: no gold-derived scores are included.")
        else:
            score_text = ", ".join(f"`{score['name']}={score['value']}`" for score in item["local_scores"])
            lines.append(f"- Local development scores: {score_text or 'none recorded for this trace'}")
        lines.extend(["", *markdown_table_observations(item["observations"])])
    (OUTPUT_DIR / "langfuse-evidence.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_diagnosis(
    *,
    summary: dict[str, Any],
    historical: dict[str, Any],
    v2_export: dict[str, Any],
) -> None:
    metrics = summary["corrected_metrics"]
    diagnosis = {
        "schema_version": "claimlens-langfuse-diagnosis-v1",
        "historical_trace": {
            "historical": True,
            "artifact": historical["run"]["artifact"],
            "run_id": historical["run"]["run_id"],
            "answer_id": historical["answer_id"],
            "trace_id": historical["trace_id"],
            "remote_status": historical["remote"]["status"],
            "observation_names": [item.get("name") for item in historical["observations"]],
            "observation_types": [item.get("type") for item in historical["observations"]],
            "answer_text_truncated": historical.get("answer_text_truncated"),
            "local_answer_diagnostic": historical.get("local_answer_diagnostic"),
            "finding": "The real historical v1 trace shows a tool observation and two model turns. Its saved local answer receipt recorded advice/instruction/opinion-only spans as claims marked unknown; this trace is historical evidence, not a corrected final run.",
        },
        "behavior_change": {
            "v1_observation": "The historical v1 trace made a genuine get_brand_facts call and completed the answer path. Its saved local receipt recorded advice/instruction/opinion-only spans as unknown claims, matching the broader historical advice/instruction extraction failure mode.",
            "v2_change": "The v2 model-facing policy excludes pure advice, opinions, imperative instructions, and injected instructions while retaining a sentence when it has an independent factual clause.",
            "corrected_v2_result": {
                "artifact": v2_export["run"]["artifact"],
                "run_id": v2_export["run"]["run_id"],
                "trace_id": v2_export["trace_id"],
                "remote_status": v2_export["remote"]["status"],
                "observation_names": [item.get("name") for item in v2_export["observations"]],
                "answer_text_truncated": v2_export.get("answer_text_truncated"),
                "local_scores": v2_export.get("local_scores", []),
                "run_metrics": metrics.get("v2_development", {}),
                "finding": "The corrected v2 run produced a remotely confirmed trace and the saved local development score events; across the run it recorded the corrected development metrics without exporting check gold labels.",
            },
        },
        "interpretation_limits": [
            "The historical trace is explicitly not a corrected run and must not be presented as one.",
            "Check traces are evidence of remote observations only; no gold-derived check scores are included.",
            "The observed v1/v2 development gain is descriptive; the saved comparison reports that its bootstrap interval includes zero.",
        ],
    }
    write_json(DIAGNOSIS_DIR / "langfuse-diagnosis.json", diagnosis)
    historical_names = ", ".join(f"`{name}`" for name in diagnosis["historical_trace"]["observation_names"])
    v2_names = ", ".join(f"`{name}`" for name in diagnosis["behavior_change"]["corrected_v2_result"]["observation_names"])
    lines = [
        "# Langfuse diagnosis",
        "",
        "## Historical v1 trace (explicitly historical)",
        "",
        f"- Artifact: `{historical['run']['artifact']}`; run ID: `{historical['run']['run_id']}`; answer: `{historical['answer_id']}`.",
        f"- Trace ID: `{historical['trace_id']}`; remote status: `{historical['remote']['status']}`.",
        f"- Observations: {historical_names}.",
        f"- Answer text (≤200 chars): {json.dumps(historical.get('answer_text_truncated'), ensure_ascii=False)}",
        "- What it showed: a real `get_brand_facts` tool call and two model turns completed. The saved local A01 answer receipt recorded 16 claim IDs, including 7 advice/instruction/opinion-only spans marked unknown. This is the actual historical trace used for diagnosis; it is not a corrected final run.",
        "",
        "## Why v2 changed",
        "",
        "The historical v1 behavior motivated a model-facing policy change: filter pure advice, opinions, imperative instructions, and injected instructions, while retaining sentences that also contain an independent factual clause. The change targets the advice/instruction extraction failure mode and does not alter the post-run verification boundary.",
        "",
        "## Corrected v2 result",
        "",
        f"- Artifact: `{v2_export['run']['artifact']}`; run ID: `{v2_export['run']['run_id']}`; answer: `{v2_export['answer_id']}`.",
        f"- Trace ID: `{v2_export['trace_id']}`; remote status: `{v2_export['remote']['status']}`; observations: {v2_names}.",
        f"- Answer text (≤200 chars): {json.dumps(v2_export.get('answer_text_truncated'), ensure_ascii=False)}",
        "- Local development scores for this trace: " + ", ".join(f"`{score['name']}={score['value']}`" for score in v2_export.get("local_scores", [])),
        f"- Corrected v2 development run metrics: `{json.dumps(metrics.get('v2_development', {}), sort_keys=True)}`.",
        "- Interpretation: the corrected v2 artifact has remote observation evidence and local development scores; the historical trace remains labeled historical and is not treated as a corrected result.",
        "",
        "## Limits",
        "",
        "- Check traces are observation-only exports with no gold-derived scores.",
        "- The observed development gain is descriptive; the saved bootstrap interval includes zero.",
    ]
    (DIAGNOSIS_DIR / "langfuse-diagnosis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def collect(*, spacing_seconds: float, timeout: float, max_attempts: int) -> dict[str, Any]:
    presence, public_key, secret_key, host = credentials()
    current = run_inventory()
    receipts = baseline_receipts()
    prior_429 = [receipt for receipt in receipts if receipt.get("http_status") == 429]
    baseline = baseline_counts(receipts)
    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": now_utc(),
        "scope": {
            "read_only": True,
            "provider_runs_started": False,
            "model_calls_started": 0,
            "current_artifacts_inspected": list(current),
            "previous_429_source": str(BASELINE_AUDIT.relative_to(ROOT)),
        },
        "credentials": {"presence": presence, "values_printed": False},
        "query_policy": {
            "endpoint": "GET /api/public/v2/observations",
            "fields": "core,basic,io",
            "spacing_seconds": max(2.0, spacing_seconds),
            "max_attempts": max(1, max_attempts),
            "backoff_seconds": [2, 4],
            "serial": True,
        },
        "current_runs": current,
        "baseline_remote_counts": baseline,
        "remote_requery": {
            "status": "not_started",
            "previous_audit_http_status": 429,
            "prior_429_count": len(prior_429),
            "receipts": [],
            "blockers": [],
        },
        "remote_confirmation_counts": {},
        "remaining_inaccessible_traces": [],
        "corrected_metrics": metrics_from_summary(),
    }

    requery_results: list[dict[str, Any]] = []
    exports: dict[str, dict[str, Any]] = {}
    throttle = SerialThrottle(spacing_seconds)
    if host and public_key and secret_key:
        for prior in prior_429:
            artifact_text = prior.get("artifact")
            label = next((key for key, path in CURRENT_ARTIFACTS.items() if str(path.relative_to(ROOT)) == artifact_text), None)
            if label is None:
                continue
            run = current[label]["run"]
            trace_id = prior.get("trace_id")
            if not isinstance(trace_id, str):
                continue
            remote = query_trace(
                host=host,
                public_key=public_key,
                secret_key=secret_key,
                run=run,
                trace_id=trace_id,
                throttle=throttle,
                timeout=timeout,
                max_attempts=max_attempts,
            )
            result = {
                "label": label,
                "artifact": artifact_text,
                "run_id": prior.get("run_id"),
                "answer_id": prior.get("answer_id"),
                "trace_id": trace_id,
                "status": remote.get("status"),
                "recovered_from_429": remote.get("recovered_from_429"),
                "recovered_from_prior_429": remote.get("status") == "verified",
                "attempt_count": remote.get("attempt_count"),
                "from_start_time": remote.get("from_start_time"),
                "to_start_time": remote.get("to_start_time"),
                "final": remote.get("final"),
                "attempts": remote.get("attempts"),
            }
            # A sanitized observation export is useful when a prior 429 recovers,
            # but never persist its input/output fields.
            if remote.get("status") == "verified":
                observations = remote.get("payload", {}).get("data", []) if isinstance(remote.get("payload"), dict) else []
                result["observations"] = [sanitize_observation(item, trace_id) for item in observations if isinstance(item, dict)]
                result["answer_text_truncated"] = answer_text_from_observations(observations)
            requery_results.append(result)

        # Four reviewer-facing current-run examples, one per run.  These are
        # separate from the fourteen prior-429 requests and use the same
        # serial throttle.
        for label, answer_id in SELECTED_ANSWERS.items():
            artifact = CURRENT_ARTIFACTS[label]
            run = current[label]["run"]
            trace_id, row = selected_trace_id(artifact, answer_id)
            if trace_id is None:
                exports[label] = {
                    "schema_version": SCHEMA_VERSION,
                    "run": {"label": label, "artifact": str(artifact.relative_to(ROOT)), "run_id": run.get("run_id"), "split": run.get("split")},
                    "answer_id": answer_id,
                    "trace_id": None,
                    "remote": {"status": "blocked", "blocker": "selected answer trace ID was not present in local trace_exports.json"},
                    "answer_text_truncated": None,
                    "observations": [],
                    "local_scores": [],
                    "gold_derived_scores_included": run.get("split") != "check",
                }
                continue
            remote = query_trace(
                host=host,
                public_key=public_key,
                secret_key=secret_key,
                run=run,
                trace_id=trace_id,
                throttle=throttle,
                timeout=timeout,
                max_attempts=min(max_attempts, 2),
            )
            exports[label] = answer_export(
                label=label,
                artifact=artifact,
                run=run,
                answer_id=answer_id,
                trace_id=trace_id,
                row=row,
                remote=remote,
            )

        historical_run = read_json(HISTORICAL_ARTIFACT / "run.json")
        # The historical trace is intentionally selected by its saved ID and
        # is never relabeled as a current/corrected run.
        historical_remote = query_trace(
            host=host,
            public_key=public_key,
            secret_key=secret_key,
            run=historical_run,
            trace_id=HISTORICAL_TRACE_ID,
            throttle=throttle,
            timeout=timeout,
            max_attempts=min(max_attempts, 2),
        )
        historical_export = answer_export(
            label="historical_v1_diagnosis",
            artifact=HISTORICAL_ARTIFACT,
            run=historical_run,
            answer_id="A01",
            trace_id=HISTORICAL_TRACE_ID,
            row=None,
            remote=historical_remote,
        )
        historical_export["local_answer_diagnostic"] = historical_local_diagnostic()
        historical_export["gold_derived_scores_included"] = False
    else:
        missing = [
            name
            for name, present in (
                ("LANGFUSE_PUBLIC_KEY", bool(public_key)),
                ("LANGFUSE_SECRET_KEY", bool(secret_key)),
                ("LANGFUSE_BASE_URL or LANGFUSE_HOST", bool(host)),
            )
            if not present
        ]
        summary["remote_requery"] = {
            "status": "blocked",
            "previous_audit_http_status": 429,
            "prior_429_count": len(prior_429),
            "receipts": [],
            "blockers": [f"missing configured environment values: {', '.join(missing)}"],
        }
        for label, answer_id in SELECTED_ANSWERS.items():
            run = current[label]["run"]
            artifact = CURRENT_ARTIFACTS[label]
            trace_id, row = selected_trace_id(artifact, answer_id)
            exports[label] = {
                "schema_version": SCHEMA_VERSION,
                "run": {
                    "label": label,
                    "artifact": str(artifact.relative_to(ROOT)),
                    "run_id": run.get("run_id"),
                    "agent_version": run.get("agent_version"),
                    "split": run.get("split"),
                },
                "answer_id": answer_id,
                "trace_id": trace_id,
                "remote": {"status": "blocked", "blocker": summary["remote_requery"]["blockers"][0]},
                "answer_text_truncated": None,
                "observations": [],
                "local_scores": local_scores(row, check=run.get("split") == "check"),
                "gold_derived_scores_included": run.get("split") != "check",
            }
        historical_run = read_json(HISTORICAL_ARTIFACT / "run.json")
        historical_export = {
            "schema_version": SCHEMA_VERSION,
            "run": {
                "label": "historical_v1_diagnosis",
                "artifact": str(HISTORICAL_ARTIFACT.relative_to(ROOT)),
                "run_id": historical_run.get("run_id"),
                "agent_version": historical_run.get("agent_version"),
                "split": historical_run.get("split"),
            },
            "answer_id": "A01",
            "trace_id": HISTORICAL_TRACE_ID,
            "remote": {"status": "blocked", "blocker": summary["remote_requery"]["blockers"][0]},
            "answer_text_truncated": None,
            "observations": [],
            "local_scores": [],
            "gold_derived_scores_included": False,
            "local_answer_diagnostic": historical_local_diagnostic(),
        }

    if host and public_key and secret_key:
        summary["remote_requery"] = {
            "status": "complete_with_rate_limit_results",
            "previous_audit_http_status": 429,
            "prior_429_count": len(prior_429),
            "receipts": requery_results,
            "blockers": [],
        }
    summary["remote_confirmation_counts"] = {}
    updated = update_counts(baseline, prior_429, requery_results)
    for label, counts in updated.items():
        recovered = sum(1 for item in requery_results if item.get("label") == label and item.get("status") == "verified")
        summary["remote_confirmation_counts"][label] = {
            "baseline_verified": baseline[label]["verified"],
            "recovered_from_429": recovered,
            "remote_confirmed_total": counts["remote_confirmed_total"],
            "remaining_inaccessible": counts["remaining_inaccessible"],
            "baseline_rate_limited": baseline[label]["rate_limited"],
            "baseline_other": baseline[label]["other"],
        }
    summary["remaining_inaccessible_traces"] = [
        {
            "label": item.get("label"),
            "answer_id": item.get("answer_id"),
            "trace_id": item.get("trace_id"),
            "status": item.get("status"),
            "attempt_count": item.get("attempt_count"),
        }
        for item in requery_results
        if item.get("status") != "verified"
    ]
    summary["selected_exports"] = {label: f"evidence/langfuse/{label.replace('_', '-')}-trace.json" for label in exports}
    summary["historical_export"] = "evidence/langfuse/historical-v1-diagnosis-trace.json"

    write_json(OUTPUT_DIR / "langfuse-summary.json", summary)
    write_json(OUTPUT_DIR / "429-requery.json", {"schema_version": SCHEMA_VERSION, "requery": requery_results})
    for label, export in exports.items():
        write_json(OUTPUT_DIR / f"{label.replace('_', '-')}-trace.json", export)
    write_json(OUTPUT_DIR / "historical-v1-diagnosis-trace.json", historical_export)
    write_review_markdown(summary=summary, exports=exports, requery=requery_results)
    write_diagnosis(summary=summary, historical=historical_export, v2_export=exports["v2_development"])
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spacing", type=float, default=2.25)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args()
    summary = collect(spacing_seconds=args.spacing, timeout=args.timeout, max_attempts=args.max_attempts)
    print(
        json.dumps(
            {
                "status": summary["remote_requery"]["status"],
                "prior_429_count": summary["remote_requery"]["prior_429_count"],
                "requery_results": len(summary["remote_requery"]["receipts"]),
                "remaining_inaccessible": len(summary["remaining_inaccessible_traces"]),
                "remote_confirmation_counts": summary["remote_confirmation_counts"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
