"""Re-score saved development artifacts without provider calls."""

from __future__ import annotations

import json
from pathlib import Path

from claimlens.contracts import AnswerOutcome
from claimlens.loaders import load_answers, load_gold
from harness.evaluator import evaluate_split


ROOT = Path(__file__).resolve().parents[2]


def verify_artifact(directory: Path) -> dict[str, object]:
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    split = str(report["split"])
    answers = load_answers(ROOT / "answers.csv", split=split)
    outcomes = [
        AnswerOutcome.model_validate(row)
        for row in json.loads((directory / "outcomes.json").read_text(encoding="utf-8"))
    ]
    gold = load_gold(ROOT / "development_gold.csv") if split == "development" else None
    scheduled = {answer.answer_id for answer in answers}
    if split == "development":
        gold = [claim for claim in gold or [] if claim.answer_id in scheduled]
    result = evaluate_split(split, answers, outcomes, gold, labeled=split == "development")
    actual = result.metrics.model_dump(mode="json")
    stored = report["metrics"]
    if actual != stored:
        raise AssertionError(f"stored metrics differ for {directory}: {actual} != {stored}")
    return {"artifact": str(directory), "status": "verified", "metrics": actual}


def main() -> int:
    directories = [ROOT / "artifacts" / "v1-development-rerun", ROOT / "artifacts" / "v2-development"]
    results = [verify_artifact(directory) for directory in directories]
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
