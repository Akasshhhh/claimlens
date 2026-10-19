"""JSON report writing for deterministic evaluation results."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .evaluator import EvaluationResult


def build_report(
    result: EvaluationResult,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable, JSON-ready report containing outcomes and predictions."""

    return result.as_dict(metadata=metadata)


def write_report(
    path: str | Path,
    result: EvaluationResult,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    """Write one complete evaluation report and return its resolved path.

    The parent directory must already exist.  Failing rather than implicitly
    creating a typo'd artifact directory keeps experiment paths explicit.
    """

    destination = Path(path)
    if not destination.parent.exists():
        raise FileNotFoundError(f"report directory does not exist: {destination.parent}")
    payload = build_report(result, metadata=metadata)
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return destination


def read_report(path: str | Path) -> dict[str, Any]:
    """Read a previously written JSON report without mutating it."""

    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("evaluation report must contain a JSON object")
    return payload


def build_comparison_report(
    comparison: Any,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a JSON-ready v1/v2 comparison payload."""

    as_dict = getattr(comparison, "as_dict", None)
    if not callable(as_dict):
        raise TypeError("comparison must expose as_dict()")
    payload = dict(as_dict())
    payload["schema_version"] = "claimlens-comparison-report-v1"
    payload["metadata"] = dict(metadata or {})
    return payload


def write_comparison_report(
    path: str | Path,
    comparison: Any,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    """Write a v1/v2 comparison report using the same stable JSON settings."""

    destination = Path(path)
    if not destination.parent.exists():
        raise FileNotFoundError(f"report directory does not exist: {destination.parent}")
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(
            build_comparison_report(comparison, metadata=metadata),
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")
    return destination


# Explicit aliases make report integration discoverable without duplicating
# serialization behavior.
save_report = write_report
load_report = read_report


__all__ = [
    "build_comparison_report",
    "build_report",
    "load_report",
    "read_report",
    "save_report",
    "write_comparison_report",
    "write_report",
]
