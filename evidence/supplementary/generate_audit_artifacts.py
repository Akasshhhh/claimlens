"""Derive reviewer-friendly evidence from the saved corrected run artifacts.

This script never starts the API, imports a provider, or calls a model.  It
only reads the four existing corrected artifacts and writes owned evidence:
payload-free exchange summaries and an inventory that points to the complete
source predictions, outcomes, reports, and settings.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = ROOT / "artifacts"
EXCHANGE_ROOT = ROOT / "evidence" / "exchanges"
INVENTORY_PATH = ROOT / "evidence" / "supplementary" / "current-four-run-inventory.json"

RUNS = (
    ("v1-development", "v1-development-corrected-final"),
    ("v2-development", "v2-development-corrected-final"),
    ("v1-check", "v1-check-corrected-final"),
    ("v2-check", "v2-check-corrected-final"),
)

RAW_KEYS = {
    "content",
    "input",
    "input_data",
    "messages",
    "output",
    "output_data",
    "prompt",
    "request",
    "response",
    "secret_key",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sanitized_event(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitized_event(item)
            for key, item in value.items()
            if key not in RAW_KEYS and key != "file_hashes"
        }
    if isinstance(value, list):
        return [_sanitized_event(item) for item in value]
    return value


def _sanitize_exchanges(source: Path, destination: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    with source.open(encoding="utf-8") as source_handle, destination.open("w", encoding="utf-8") as output:
        for line in source_handle:
            if not line.strip():
                continue
            event = _sanitized_event(json.loads(line))
            kind = event.get("kind") if isinstance(event, dict) else None
            # Scores are report evidence, not model/tool exchanges.  Keep them in
            # the source report and inventory metrics, but do not copy gold-derived
            # score events into the exchange-only evidence files.
            if kind == "score":
                continue
            if isinstance(kind, str):
                counts[kind] += 1
                output.write(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
                output.write("\n")
    return counts


def _file_receipt(path: Path) -> dict[str, Any]:
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": _sha256(path)}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_inventory() -> dict[str, Any]:
    inventory: list[dict[str, Any]] = []
    for name, artifact_name in RUNS:
        artifact = ARTIFACT_ROOT / artifact_name
        config = _load(artifact / "config.json")
        run = _load(artifact / "run.json")
        report = _load(artifact / "report.json")
        outcomes = _load(artifact / "outcomes.json")
        predictions = _load(artifact / "predictions.json")
        metadata = config.get("metadata", {})
        settings = metadata.get("settings", config.get("provider_settings", {}))
        sanitized_exchange = EXCHANGE_ROOT / f"{name}.jsonl"
        exchange_counts = _sanitize_exchanges(artifact / "exchanges.jsonl", sanitized_exchange)
        metrics = report.get("metrics")
        if not isinstance(metrics, dict):
            metrics = None
        status_counts = Counter(
            outcome.get("status")
            for outcome in outcomes
            if isinstance(outcome, dict) and isinstance(outcome.get("status"), str)
        )
        inventory.append(
            {
                "name": name,
                "artifact": artifact.relative_to(ROOT).as_posix(),
                "run_id": run.get("run_id", metadata.get("run_id")),
                "split": run.get("split", metadata.get("split")),
                "agent_version": run.get("agent_version", metadata.get("agent_version")),
                "prompt_version": run.get("prompt_version", metadata.get("prompt_version")),
                "source_revision": run.get("source_revision", metadata.get("source_revision")),
                "settings": settings,
                "counts": run.get("counts", metadata.get("counts")),
                "source_files": {
                    key: _file_receipt(artifact / filename)
                    for key, filename in {
                        "config": "config.json",
                        "outcomes": "outcomes.json",
                        "predictions": "predictions.json",
                        "report": "report.json",
                        "exchanges": "exchanges.jsonl",
                    }.items()
                },
                "sanitized_exchanges": _file_receipt(sanitized_exchange),
                "report": {
                    "schema_version": report.get("schema_version"),
                    "split": report.get("split"),
                    "quality_available": report.get("quality_available"),
                    "prediction_count": len(predictions) if isinstance(predictions, list) else None,
                    "outcome_count": len(outcomes) if isinstance(outcomes, list) else None,
                    "metrics": metrics,
                },
                "outcome_status_counts": dict(sorted(status_counts.items())),
                "exchange_event_counts": dict(sorted(exchange_counts.items())),
            }
        )
    return {"runs": inventory}


def main() -> None:
    EXCHANGE_ROOT.mkdir(parents=True, exist_ok=True)
    INVENTORY_PATH.write_text(
        json.dumps(build_inventory(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(INVENTORY_PATH.relative_to(ROOT))
    for name, _artifact_name in RUNS:
        print(EXCHANGE_ROOT.joinpath(f"{name}.jsonl").relative_to(ROOT))


if __name__ == "__main__":
    main()
