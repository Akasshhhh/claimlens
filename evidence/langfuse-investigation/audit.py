"""Read-only Langfuse audit for the four saved final run artifacts.

This script never calls a provider or a model.  It reads local run receipts and,
when credentials and connectivity are available, performs one authenticated
GET per saved per-answer trace ID using that run's fixed start-time window.
Only counts, observation names, status codes, and body digests are written.
Raw Langfuse responses, prompts, outputs, score payloads, and credentials are
never exported.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
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

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUTPUT = Path(__file__).resolve().parent / "audit-report.json"
FINAL_ARTIFACTS = {
    "v1_development": ROOT / "artifacts" / "v1-development-corrected-final",
    "v2_development": ROOT / "artifacts" / "v2-development-corrected-final",
    "v1_check": ROOT / "artifacts" / "v1-check-corrected-final",
    "v2_check": ROOT / "artifacts" / "v2-check-corrected-final",
}
_SENSITIVE_KEYS = {
    "ai_answer",
    "answer_text",
    "authorization",
    "api_key",
    "password",
    "secret",
    "secret_key",
    "token",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sensitive_keys(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in _SENSITIVE_KEYS:
                found.append(f"{prefix}{key_text}")
            found.extend(_sensitive_keys(item, f"{prefix}{key_text}."))
    elif isinstance(value, list):
        for item in value:
            found.extend(_sensitive_keys(item, prefix))
    return sorted(set(found))


def _trace_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({item for item in value if isinstance(item, str) and item})


def _read_events(path: Path) -> tuple[dict[str, int], dict[str, str], list[str]]:
    kinds: dict[str, int] = {}
    answer_by_trace: dict[str, str] = {}
    sensitive: list[str] = []
    if not path.exists():
        return kinds, answer_by_trace, sensitive
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind", ""))
        kinds[kind] = kinds.get(kind, 0) + 1
        trace_id = event.get("trace_id")
        answer_id = event.get("answer_id")
        if kind == "trace_start" and isinstance(trace_id, str) and isinstance(answer_id, str):
            answer_by_trace[trace_id] = answer_id
        sensitive.extend(_sensitive_keys(event))
    return kinds, answer_by_trace, sorted(set(sensitive))


def _local_receipt(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "row_count": 0, "trace_ids": [], "statuses": {}}
    value = _json(path)
    rows = value if isinstance(value, list) else []
    statuses: dict[str, int] = {}
    ids: list[str] = []
    sensitive: list[str] = _sensitive_keys(value)
    for row in rows:
        if not isinstance(row, dict):
            continue
        trace_id = row.get("trace_id")
        if isinstance(trace_id, str) and trace_id:
            ids.append(trace_id)
        status = str(row.get("status", "recorded"))
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "exists": True,
        "row_count": len(rows),
        "trace_ids": sorted(set(ids)),
        "statuses": statuses,
        "sensitive_keys_detected": sorted(set(sensitive)),
    }


def _local_run(label: str, directory: Path) -> dict[str, Any]:
    run_path = directory / "run.json"
    if not run_path.exists():
        return {
            "label": label,
            "artifact": str(directory.relative_to(ROOT)),
            "status": "blocked",
            "blocker": "missing run.json",
        }
    run = _json(run_path)
    verification = _local_receipt(directory / "trace_verification.json")
    exports = _local_receipt(directory / "trace_exports.json")
    event_kinds, answer_by_trace, event_sensitive = _read_events(directory / "exchanges.jsonl")
    run_trace_ids = _trace_ids(run.get("trace_ids")) if isinstance(run, dict) else []
    local_ids = sorted(set(answer_by_trace))
    check = run.get("split") == "check" if isinstance(run, dict) else False
    return {
        "label": label,
        "artifact": str(directory.relative_to(ROOT)),
        "run_id": run.get("run_id"),
        "agent_version": run.get("agent_version"),
        "split": run.get("split"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "trace_ids": run_trace_ids,
        "trace_id_count": len(run_trace_ids),
        "local_answer_trace_ids": len(local_ids),
        "local_receipts": verification,
        "local_exports": exports,
        "local_event_kinds": event_kinds,
        "check_score_events": event_kinds.get("score", 0) if check else None,
        "sensitive_keys_detected": sorted(
            set(event_sensitive)
            | set(verification.get("sensitive_keys_detected", []))
            | set(exports.get("sensitive_keys_detected", []))
        ),
    }


def _credentials() -> tuple[dict[str, bool], str, str, str | None]:
    try:
        from dotenv import dotenv_values, load_dotenv

        load_dotenv(ROOT / ".env")
        values = dotenv_values(ROOT / ".env")
    except Exception:
        values = {}
    names = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL", "LANGFUSE_HOST")
    presence = {name: bool(values.get(name) or os.environ.get(name)) for name in names}
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY") or str(values.get("LANGFUSE_PUBLIC_KEY") or "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY") or str(values.get("LANGFUSE_SECRET_KEY") or "")
    host = (
        os.environ.get("LANGFUSE_BASE_URL")
        or os.environ.get("LANGFUSE_HOST")
        or values.get("LANGFUSE_BASE_URL")
        or values.get("LANGFUSE_HOST")
    )
    return presence, public_key, secret_key, str(host).rstrip("/") if host else None


def _urllib_get(url: str, public_key: str, secret_key: str, timeout: float) -> tuple[int, bytes, str]:
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Authorization": f"Basic {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), "urllib"
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), "urllib"


def _curl_get(url: str, public_key: str, secret_key: str, timeout: float) -> tuple[int, bytes, str]:
    if not shutil.which("curl"):
        raise RuntimeError("urllib failed and curl is unavailable")
    config = f"url = {json.dumps(url)}\nuser = {json.dumps(f'{public_key}:{secret_key}')}\n"
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
        input=config.encode(),
        capture_output=True,
        check=False,
        timeout=timeout + 2,
    )
    lines = result.stdout.splitlines()
    if not lines or not lines[-1].isdigit():
        detail = result.stderr.decode("utf-8", errors="replace").replace("\n", " ")[:200]
        raise RuntimeError(f"curl did not return an HTTP status: {detail}")
    status = int(lines[-1])
    if status == 0:
        detail = result.stderr.decode("utf-8", errors="replace").replace("\n", " ")[:200]
        raise RuntimeError(f"curl transport failed: {detail or 'timeout or connection failure'}")
    return status, b"\n".join(lines[:-1]), "curl"


def _get(url: str, public_key: str, secret_key: str, timeout: float) -> tuple[int, bytes, str]:
    # Prefer curl when available: this machine's Python trust store may reject
    # the host certificate even though the system curl trust store succeeds.
    if shutil.which("curl"):
        try:
            return _curl_get(url, public_key, secret_key, timeout)
        except (OSError, TimeoutError, RuntimeError):
            pass
    try:
        return _urllib_get(url, public_key, secret_key, timeout)
    except (OSError, TimeoutError, urllib.error.URLError):
        return _curl_get(url, public_key, secret_key, timeout)


def _body_observation_summary(status: int, raw: bytes) -> dict[str, Any]:
    result: dict[str, Any] = {
        "http_status": status,
        "body_sha256": _sha256(raw),
        "observation_count": 0,
        "observation_names": [],
    }
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        result["parse_error"] = "invalid_json"
        return result
    observations = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(observations, list):
        result["observation_count"] = len(observations)
        result["observation_names"] = [
            item.get("name")
            for item in observations
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ][:50]
    return result


def _remote_receipt(
    run: dict[str, Any],
    trace_id: str,
    *,
    host: str,
    public_key: str,
    secret_key: str,
    timeout: float,
) -> dict[str, Any]:
    from claimlens.tracing import observation_query, observation_window

    from_time, to_time = observation_window(run["started_at"], run["completed_at"])
    path = observation_query(trace_id, from_start_time=from_time, to_start_time=to_time)
    try:
        status, raw, transport = _get(f"{host}{path}", public_key, secret_key, timeout)
    except Exception as exc:
        return {
            "trace_id": trace_id,
            "answer_id": None,
            "status": "blocked",
            "error_type": type(exc).__name__,
            "error_message": str(exc).replace("\n", " ").replace("\r", " ")[:300],
            "from_start_time": from_time,
            "to_start_time": to_time,
        }
    summary = _body_observation_summary(status, raw)
    summary.update(
        {
            "trace_id": trace_id,
            "status": "verified" if 200 <= status < 300 and summary["observation_count"] else "empty"
            if 200 <= status < 300
            else "error",
            "from_start_time": from_time,
            "to_start_time": to_time,
            "transport": transport,
        }
    )
    return summary


def audit(
    *,
    output: Path = OUTPUT,
    spacing_seconds: float = 0.0,
    timeout: float = 15.0,
    max_traces: int | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    local_runs = [_local_run(label, directory) for label, directory in FINAL_ARTIFACTS.items()]
    report: dict[str, Any] = {
        "schema_version": "claimlens-langfuse-audit-v1",
        "generated_at_utc": _now(),
        "scope": {
            "artifacts": [run.get("artifact") for run in local_runs],
            "provider_runs_started": False,
            "model_calls_started": 0,
            "read_only": True,
        },
        "local_recorded_receipts": local_runs,
        "remote_verification": {"status": "not_started", "receipts": [], "blockers": []},
        "full_exports": {
            "status": "not_collected",
            "paths": [],
            "reason": "Only sanitized counts, names, and digests are retained; raw observations are not exported.",
        },
        "credentials": {"presence": {}},
    }
    presence, public_key, secret_key, host = _credentials()
    report["credentials"] = {
        "presence": presence,
        "values_printed": False,
    }
    if offline:
        report["remote_verification"] = {
            "status": "blocked",
            "receipts": [],
            "blockers": ["offline mode requested; no network request was made"],
        }
    else:
        missing = [name for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY") if not presence[name]]
        if not host:
            missing.append("LANGFUSE_BASE_URL or LANGFUSE_HOST")
        if missing:
            report["remote_verification"] = {
                "status": "blocked",
                "receipts": [],
                "blockers": [f"missing configured environment values: {', '.join(missing)}"],
            }
        else:
            try:
                health_status, health_raw, health_transport = _get(
                    f"{host}/api/public/health", public_key, secret_key, timeout
                )
                health_payload = json.loads(health_raw)
                health = {
                    "http_status": health_status,
                    "transport": health_transport,
                    "status": health_payload.get("status") if isinstance(health_payload, dict) else None,
                    "version": health_payload.get("version") if isinstance(health_payload, dict) else None,
                }
            except Exception as exc:
                health = {
                    "http_status": None,
                    "status": "blocked",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc).replace("\n", " ").replace("\r", " ")[:300],
                }
            report["health"] = health
            if health.get("http_status") != 200:
                report["remote_verification"] = {
                    "status": "blocked",
                    "receipts": [],
                    "blockers": ["authenticated Langfuse health request did not return HTTP 200"],
                }
            else:
                receipts: list[dict[str, Any]] = []
                remaining = max_traces
                for local in local_runs:
                    if remaining is not None and remaining <= 0:
                        break
                    artifact = FINAL_ARTIFACTS[local["label"]]
                    run = _json(artifact / "run.json")
                    ids = _trace_ids(run.get("trace_ids"))
                    if remaining is not None:
                        ids = ids[:remaining]
                    for trace_id in ids:
                        receipt = _remote_receipt(
                            run,
                            trace_id,
                            host=host,
                            public_key=public_key,
                            secret_key=secret_key,
                            timeout=timeout,
                        )
                        receipt.update(
                            {
                                "artifact": local["artifact"],
                                "run_id": run.get("run_id"),
                                "answer_id": next(
                                    (
                                        event_answer
                                        for event_trace, event_answer in _read_events(
                                            artifact / "exchanges.jsonl"
                                        )[1].items()
                                        if event_trace == trace_id
                                    ),
                                    None,
                                ),
                            }
                        )
                        receipts.append(receipt)
                        if remaining is not None:
                            remaining -= 1
                        if spacing_seconds:
                            time.sleep(spacing_seconds)
                verified = sum(receipt.get("status") == "verified" for receipt in receipts)
                errors = sum(receipt.get("status") in {"error", "blocked"} for receipt in receipts)
                report["remote_verification"] = {
                    "status": "complete" if not errors else "complete_with_errors",
                    "requested_trace_count": len(receipts),
                    "verified_trace_count": verified,
                    "error_trace_count": errors,
                    "receipts": receipts,
                    "blockers": sorted(
                        {
                            *(
                                [
                                    "Langfuse observations endpoint returned HTTP 429 for some trace queries"
                                ]
                                if any(item.get("http_status") == 429 for item in receipts)
                                else []
                            ),
                            *(
                                [
                                    "some trace queries timed out or had transport failures"
                                ]
                                if any(item.get("status") == "blocked" for item in receipts)
                                else []
                            ),
                        }
                    ),
                }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--spacing", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--max-traces", type=int, default=None)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    report = audit(
        output=args.output,
        spacing_seconds=max(0.0, args.spacing),
        timeout=max(1.0, args.timeout),
        max_traces=args.max_traces,
        offline=args.offline,
    )
    remote = report["remote_verification"]
    print(
        json.dumps(
            {
                "output": str(args.output),
                "remote_status": remote.get("status"),
                "requested_trace_count": remote.get("requested_trace_count", 0),
                "verified_trace_count": remote.get("verified_trace_count", 0),
                "provider_runs_started": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
