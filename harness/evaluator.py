"""Deterministic scoring for ClaimLens answer outcomes.

The inference path never imports this module.  The harness receives saved
``AnswerOutcome`` objects and joins them with the scheduled answers and (for a
labeled split) the development ``GoldClaim`` rows.  Matching is deliberately
boring: a quote must agree before an exact source span can match.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from claimlens.contracts import (
    AnswerOutcome,
    Claim,
    GoldClaim,
    Metrics,
    SUPPORTED_LABELS,
)


CONFUSION_ROWS = (*SUPPORTED_LABELS, "no_gold")
CONFUSION_COLUMNS = (*SUPPORTED_LABELS, "missing_or_error")
class EvaluationInputError(ValueError):
    """Raised when saved evaluation inputs cannot describe one scheduled run."""


class OutcomeValidationError(EvaluationInputError):
    """Raised when outcomes are missing, duplicated, or belong to another run."""


def _as_model_dump(value: Any) -> Any:
    """Convert a pydantic model or normal value into JSON-compatible data."""

    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _as_model_dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_model_dump(item) for item in value]
    return value


def _scheduled_id(value: Any) -> str:
    if isinstance(value, str):
        return value
    answer_id = getattr(value, "answer_id", None)
    if isinstance(answer_id, str) and answer_id:
        return answer_id
    if isinstance(value, Mapping) and isinstance(value.get("answer_id"), str):
        return value["answer_id"]
    raise EvaluationInputError("scheduled answers must contain string answer_id values")


def _scheduled_answers(
    scheduled: Sequence[Any] | Mapping[str, Any] | Iterable[Any],
) -> tuple[tuple[str, ...], dict[str, Any]]:
    """Return deterministic IDs and optional source-answer objects.

    A mapping is accepted for callers that already keyed answer records by ID;
    its keys are checked against each value so a mislabeled record cannot be
    silently scored under the wrong answer.
    """

    if isinstance(scheduled, Mapping):
        values: list[Any] = []
        for key, value in scheduled.items():
            if not isinstance(key, str):
                raise EvaluationInputError("scheduled answer mapping keys must be strings")
            value_id = _scheduled_id(value)
            if value_id != key:
                raise EvaluationInputError(
                    f"scheduled answer key {key!r} does not match answer_id {value_id!r}"
                )
            values.append(value)
    else:
        values = list(scheduled)

    ids: list[str] = []
    sources: dict[str, Any] = {}
    for value in values:
        answer_id = _scheduled_id(value)
        if answer_id in sources or answer_id in ids:
            raise EvaluationInputError(f"duplicate scheduled answer: {answer_id}")
        ids.append(answer_id)
        if hasattr(value, "ai_answer"):
            sources[answer_id] = value
        elif isinstance(value, Mapping) and "ai_answer" in value:
            sources[answer_id] = value
    return tuple(sorted(ids)), sources


def validate_outcomes(
    scheduled: Sequence[Any] | Mapping[str, Any] | Iterable[Any],
    outcomes: Sequence[AnswerOutcome] | Mapping[str, AnswerOutcome] | Iterable[AnswerOutcome],
) -> tuple[AnswerOutcome, ...]:
    """Validate one terminal outcome for every scheduled answer.

    Missing, duplicate, and extra outcomes are all rejected.  Returning a
    sorted tuple makes report and metric output independent of completion order.
    """

    scheduled_ids, _ = _scheduled_answers(scheduled)
    expected = set(scheduled_ids)
    if isinstance(outcomes, Mapping):
        values = []
        for key, outcome in outcomes.items():
            if not isinstance(key, str):
                raise OutcomeValidationError("outcome mapping keys must be strings")
            if not isinstance(outcome, AnswerOutcome):
                try:
                    outcome = AnswerOutcome.model_validate(outcome)
                except Exception as exc:  # pragma: no cover - defensive boundary
                    raise OutcomeValidationError(f"invalid outcome for {key!r}: {exc}") from exc
            if outcome.answer_id != key:
                raise OutcomeValidationError(
                    f"outcome mapping key {key!r} does not match answer_id {outcome.answer_id!r}"
                )
            values.append(outcome)
    else:
        values = list(outcomes)

    seen: dict[str, AnswerOutcome] = {}
    duplicates: list[str] = []
    for value in values:
        if not isinstance(value, AnswerOutcome):
            try:
                value = AnswerOutcome.model_validate(value)
            except Exception as exc:
                raise OutcomeValidationError(f"invalid answer outcome: {exc}") from exc
        answer_id = value.answer_id
        if answer_id in seen:
            duplicates.append(answer_id)
        else:
            seen[answer_id] = value

    actual = set(seen)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if duplicates or missing or extra:
        parts: list[str] = []
        if missing:
            parts.append(f"missing outcomes: {', '.join(missing)}")
        if duplicates:
            parts.append(f"duplicate outcomes: {', '.join(sorted(set(duplicates)))}")
        if extra:
            parts.append(f"unexpected outcomes: {', '.join(extra)}")
        raise OutcomeValidationError("; ".join(parts))

    return tuple(seen[answer_id] for answer_id in sorted(seen))


def _source_text(value: Any) -> str | None:
    if isinstance(value, Mapping):
        text = value.get("ai_answer")
    else:
        text = getattr(value, "ai_answer", None)
    return text if isinstance(text, str) else None


def _safe_ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _f1(numerator: int, denominator_sum: int) -> float:
    return (2 * numerator / denominator_sum) if denominator_sum else 0.0


@dataclass(frozen=True)
class MatchedClaim:
    """One exact span match, retained for reports and version comparison."""

    answer_id: str
    gold_claim_id: str
    prediction_claim_id: str
    start_char: int
    end_char: int
    expected_label: str
    predicted_label: str
    label_correct: bool
    quote: str
    difficulty_tags: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer_id": self.answer_id,
            "gold_claim_id": self.gold_claim_id,
            "prediction_claim_id": self.prediction_claim_id,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "expected_label": self.expected_label,
            "predicted_label": self.predicted_label,
            "label_correct": self.label_correct,
            "quote": self.quote,
            "difficulty_tags": list(self.difficulty_tags),
        }


@dataclass(frozen=True)
class AnswerScore:
    """Per-answer accounting used by comparison and paired bootstrap helpers."""

    answer_id: str
    status: str
    error_type: str | None
    gold_claims: int
    predictions: int
    matched_spans: int
    correct_labels: int
    extraction_precision: float
    extraction_recall: float
    extraction_f1: float
    joint_precision: float
    joint_recall: float
    joint_f1: float
    missed_ids: tuple[str, ...] = ()
    unmatched_prediction_ids: tuple[str, ...] = ()
    wrong_label_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer_id": self.answer_id,
            "status": self.status,
            "error_type": self.error_type,
            "gold_claims": self.gold_claims,
            "predictions": self.predictions,
            "matched_spans": self.matched_spans,
            "correct_labels": self.correct_labels,
            "extraction_precision": self.extraction_precision,
            "extraction_recall": self.extraction_recall,
            "extraction_f1": self.extraction_f1,
            "joint_precision": self.joint_precision,
            "joint_recall": self.joint_recall,
            "joint_f1": self.joint_f1,
            "missed_ids": list(self.missed_ids),
            "unmatched_prediction_ids": list(self.unmatched_prediction_ids),
            "wrong_label_ids": list(self.wrong_label_ids),
        }


@dataclass(frozen=True)
class EvaluationResult:
    """Full deterministic score plus the records needed for audit reports."""

    split: str
    metrics: Metrics
    scheduled_answer_ids: tuple[str, ...]
    outcomes: tuple[AnswerOutcome, ...]
    gold_claims: tuple[GoldClaim, ...]
    matches: tuple[MatchedClaim, ...] = ()
    answer_scores: tuple[AnswerScore, ...] = ()
    labeled: bool = True
    quality_available: bool = True

    def __getattr__(self, name: str) -> Any:
        # A small compatibility convenience: callers that only need metrics
        # can use result.joint_f1 as well as result.metrics.joint_f1.
        try:
            return getattr(self.metrics, name)
        except AttributeError as exc:  # pragma: no cover - normal Python path
            raise AttributeError(name) from exc

    @property
    def accepted_predictions(self) -> tuple[dict[str, Any], ...]:
        rows: list[dict[str, Any]] = []
        for outcome in self.outcomes:
            if outcome.status != "ok":
                continue
            for claim in outcome.claims:
                rows.append(
                    {
                        "answer_id": outcome.answer_id,
                        **_as_model_dump(claim),
                    }
                )
        return tuple(rows)

    @property
    def matched_gold_ids(self) -> tuple[str, ...]:
        return tuple(match.gold_claim_id for match in self.matches)

    @property
    def matched_prediction_ids(self) -> tuple[str, ...]:
        return tuple(match.prediction_claim_id for match in self.matches)

    def as_dict(self, *, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Return a JSON-ready report payload."""

        outcomes = [_as_model_dump(outcome) for outcome in self.outcomes]
        gold = [_as_model_dump(claim) for claim in self.gold_claims]
        metrics = self.metrics.model_dump(mode="json")
        answer_scores = [score.as_dict() for score in self.answer_scores]
        if not self.labeled:
            # The public Metrics contract keeps count fields typed as ints for
            # easy local arithmetic.  Check reports intentionally redact every
            # gold-derived count/quality field as null while retaining P,
            # answer errors, predictions, timing, and versions.
            for key in (
                "gold_claims",
                "matched_spans",
                "correct_labels",
                "extraction_precision",
                "extraction_recall",
                "extraction_f1",
                "joint_precision",
                "joint_recall",
                "joint_f1",
                "coverage",
                "per_label",
                "macro_f1",
                "confusion_matrix",
                "missed_ids",
                "unmatched_prediction_ids",
                "wrong_label_ids",
            ):
                metrics[key] = None
            for score in answer_scores:
                for key in (
                    "gold_claims",
                    "matched_spans",
                    "correct_labels",
                    "extraction_precision",
                    "extraction_recall",
                    "extraction_f1",
                    "joint_precision",
                    "joint_recall",
                    "joint_f1",
                    "missed_ids",
                    "unmatched_prediction_ids",
                    "wrong_label_ids",
                ):
                    score[key] = None
        return {
            "schema_version": "claimlens-evaluation-report-v1",
            "split": self.split,
            "labeled": self.labeled,
            "quality_available": self.quality_available,
            "metadata": dict(metadata or {}),
            "metrics": metrics,
            "answer_scores": answer_scores,
            "answer_outcomes": outcomes,
            "predictions": list(self.accepted_predictions),
            "gold_claims": gold if self.labeled else [],
            "matches": [match.as_dict() for match in self.matches] if self.labeled else [],
            "matched_gold_ids": list(self.matched_gold_ids) if self.labeled else None,
            "matched_prediction_ids": list(self.matched_prediction_ids) if self.labeled else None,
        }


def _coerce_gold(values: Sequence[GoldClaim] | Iterable[GoldClaim] | None) -> tuple[GoldClaim, ...]:
    if values is None:
        return ()
    result: list[GoldClaim] = []
    for value in values:
        if isinstance(value, GoldClaim):
            result.append(value)
        else:
            try:
                result.append(GoldClaim.model_validate(value))
            except Exception as exc:
                raise EvaluationInputError(f"invalid gold claim: {exc}") from exc
    return tuple(result)


def _coerce_claim(value: Claim | Mapping[str, Any]) -> Claim:
    if isinstance(value, Claim):
        return value
    try:
        return Claim.model_validate(value)
    except Exception as exc:
        raise EvaluationInputError(f"invalid prediction claim: {exc}") from exc


def _quality_available(*, labeled: bool, scheduled_ids: tuple[str, ...]) -> bool:
    # A scheduled run with no references can still be a labeled zero-claim
    # development split and receives zero metrics.  A truly empty run has no
    # population on which to define quality, so its quality fields are null.
    return labeled and bool(scheduled_ids)


def _per_answer_metric(numerator: int, denominator: int, quality_available: bool) -> float:
    return _safe_ratio(numerator, denominator) if quality_available else 0.0


def evaluate_split(
    split: str,
    scheduled_answers: Sequence[Any] | Mapping[str, Any] | Iterable[Any],
    outcomes: Sequence[AnswerOutcome] | Mapping[str, AnswerOutcome] | Iterable[AnswerOutcome],
    gold_claims: Sequence[GoldClaim] | Iterable[GoldClaim] | None = None,
    *,
    labeled: bool | None = None,
) -> EvaluationResult:
    """Score one complete split using exact deterministic span matching.

    ``gold_claims=None`` marks an unlabeled/check split.  For a development
    split with an explicitly empty gold list, pass ``labeled=True`` (the
    default for non-check split names); quality is zero for a non-empty
    schedule and null only for an empty scheduled run.
    """

    if not isinstance(split, str) or not split:
        raise EvaluationInputError("split must be a non-empty string")

    scheduled_ids, source_records = _scheduled_answers(scheduled_answers)
    validated_outcomes = validate_outcomes(scheduled_ids, outcomes)
    # validate_outcomes accepts a sequence of IDs, which intentionally avoids
    # asking it to re-read a source record after we have normalized the input.

    gold = _coerce_gold(gold_claims)
    split_lower = split.lower()
    if labeled is None:
        labeled = gold_claims is not None and split_lower not in {
            "check",
            "final",
            "final-check",
            "final_check",
        }
    quality = _quality_available(labeled=labeled, scheduled_ids=scheduled_ids)

    # Group gold by answer while preserving source order.  Matching only uses
    # exact offsets and quote equality; no fuzzy or overlap matching occurs.
    gold_by_answer: dict[str, list[GoldClaim]] = defaultdict(list)
    for claim in gold:
        gold_by_answer[claim.answer_id].append(claim)

    accepted: list[tuple[str, Claim, int]] = []
    for outcome in validated_outcomes:
        if outcome.status != "ok":
            # An error is terminal at answer level.  Even if a malformed caller
            # attaches claims to it, those claims are never accepted.
            continue
        for index, raw_claim in enumerate(outcome.claims):
            accepted.append((outcome.answer_id, _coerce_claim(raw_claim), index))
    accepted.sort(key=lambda item: (item[0], item[1].start_char, item[1].end_char, item[1].claim_id, item[2]))

    matched: list[MatchedClaim] = []
    matched_gold_indexes: set[tuple[str, int]] = set()
    matched_prediction_indexes: set[int] = set()
    for prediction_index, (answer_id, prediction, _original_index) in enumerate(accepted):
        candidates = gold_by_answer.get(answer_id, ())
        found: tuple[int, GoldClaim] | None = None
        # Quote equality is deliberately the first comparison.  If source text
        # is available, it is also checked before offsets so an invalid saved
        # quote cannot become a match merely because coordinates agree.
        for gold_index, gold_claim in enumerate(candidates):
            if (answer_id, gold_index) in matched_gold_indexes:
                continue
            if prediction.quote != gold_claim.quote:
                continue
            source = _source_text(source_records.get(answer_id))
            if source is not None:
                start = gold_claim.start_char
                end = gold_claim.end_char
                if start < 0 or end <= start or end > len(source):
                    continue
                if source[start:end] != gold_claim.quote:
                    continue
            if prediction.start_char != gold_claim.start_char:
                continue
            if prediction.end_char != gold_claim.end_char:
                continue
            found = (gold_index, gold_claim)
            break
        if found is None:
            continue
        gold_index, gold_claim = found
        matched_gold_indexes.add((answer_id, gold_index))
        matched_prediction_indexes.add(prediction_index)
        matched.append(
            MatchedClaim(
                answer_id=answer_id,
                gold_claim_id=gold_claim.claim_id,
                prediction_claim_id=prediction.claim_id,
                start_char=prediction.start_char,
                end_char=prediction.end_char,
                expected_label=gold_claim.expected_label,
                predicted_label=prediction.label,
                label_correct=prediction.label == gold_claim.expected_label,
                quote=prediction.quote,
                difficulty_tags=tuple(gold_claim.difficulty_tags),
            )
        )

    missed_claims = [
        claim
        for answer_id, claims in gold_by_answer.items()
        for index, claim in enumerate(claims)
        if (answer_id, index) not in matched_gold_indexes
    ]
    unmatched_predictions = [
        (answer_id, prediction)
        for index, (answer_id, prediction, _original_index) in enumerate(accepted)
        if index not in matched_prediction_indexes
    ]
    wrong_matches = [match for match in matched if not match.label_correct]

    g = len(gold)
    p = len(accepted)
    m = len(matched)
    c = sum(match.label_correct for match in matched)
    answer_errors = sum(outcome.status == "error" for outcome in validated_outcomes)

    extraction_precision = _safe_ratio(m, p)
    extraction_recall = _safe_ratio(m, g)
    extraction_f1 = _f1(m, p + g)
    joint_precision = _safe_ratio(c, p)
    joint_recall = _safe_ratio(c, g)
    joint_f1 = _f1(c, p + g)
    coverage = _safe_ratio(c, g)

    confusion = {row: {column: 0 for column in CONFUSION_COLUMNS} for row in CONFUSION_ROWS}
    if quality:
        for match in matched:
            confusion[match.expected_label][match.predicted_label] += 1
        for claim in missed_claims:
            confusion[claim.expected_label]["missing_or_error"] += 1
        for _answer_id, prediction in unmatched_predictions:
            confusion["no_gold"][prediction.label] += 1

    per_label: dict[str, dict[str, int | float]] = {}
    if quality:
        actual_counts = Counter(claim.expected_label for claim in gold)
        prediction_counts = Counter(prediction.label for _answer_id, prediction, _ in accepted)
        true_positive = Counter(
            match.expected_label for match in matched if match.label_correct
        )
        for label in SUPPORTED_LABELS:
            actual = actual_counts[label]
            predicted = prediction_counts[label]
            tp = true_positive[label]
            fp = predicted - tp
            fn = actual - tp
            per_label[label] = {
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "actual": actual,
                "predicted": predicted,
                "precision": _safe_ratio(tp, predicted),
                "recall": _safe_ratio(tp, actual),
                "f1": _f1(tp, predicted + actual),
            }

    macro_f1 = (
        sum(float(per_label[label]["f1"]) for label in SUPPORTED_LABELS) / len(SUPPORTED_LABELS)
        if quality
        else None
    )

    if quality:
        metric_values: dict[str, Any] = {
            "extraction_precision": extraction_precision,
            "extraction_recall": extraction_recall,
            "extraction_f1": extraction_f1,
            "joint_precision": joint_precision,
            "joint_recall": joint_recall,
            "joint_f1": joint_f1,
            "coverage": coverage,
            "per_label": per_label,
            "macro_f1": macro_f1,
            "confusion_matrix": confusion,
            "missed_ids": [claim.claim_id for claim in missed_claims],
            "unmatched_prediction_ids": [prediction.claim_id for _answer_id, prediction in unmatched_predictions],
            # Wrong-label IDs are gold IDs: they are stable across candidate
            # predictions and therefore useful as regression keys.
            "wrong_label_ids": [match.gold_claim_id for match in wrong_matches],
        }
    else:
        metric_values = {
            "extraction_precision": None,
            "extraction_recall": None,
            "extraction_f1": None,
            "joint_precision": None,
            "joint_recall": None,
            "joint_f1": None,
            "coverage": None,
            "per_label": {},
            "macro_f1": None,
            "confusion_matrix": {},
            "missed_ids": [],
            "unmatched_prediction_ids": [],
            "wrong_label_ids": [],
        }

    metrics = Metrics(
        split=split,
        gold_claims=g,
        predictions=p,
        matched_spans=m,
        correct_labels=c,
        answer_errors=answer_errors,
        **metric_values,
    )

    missed_by_gold_id = {claim.claim_id for claim in missed_claims}
    predictions_by_answer: dict[str, list[Claim]] = defaultdict(list)
    for answer_id, prediction, _ in accepted:
        predictions_by_answer[answer_id].append(prediction)
    status_by_answer = {outcome.answer_id: outcome for outcome in validated_outcomes}

    answer_scores: list[AnswerScore] = []
    for answer_id in scheduled_ids:
        answer_gold = gold_by_answer.get(answer_id, [])
        answer_predictions = predictions_by_answer.get(answer_id, [])
        answer_matches = [match for match in matched if match.answer_id == answer_id]
        answer_missed = [claim.claim_id for claim in answer_gold if claim.claim_id in missed_by_gold_id]
        answer_wrong = [match.gold_claim_id for match in answer_matches if not match.label_correct]
        matched_prediction_ids = {match.prediction_claim_id for match in answer_matches}
        answer_extra = [
            claim.claim_id for claim in answer_predictions if claim.claim_id not in matched_prediction_ids
        ]
        answer_outcome = status_by_answer[answer_id]
        answer_g = len(answer_gold)
        answer_p = len(answer_predictions)
        answer_m = len(answer_matches)
        answer_c = sum(match.label_correct for match in answer_matches)
        answer_scores.append(
            AnswerScore(
                answer_id=answer_id,
                status=answer_outcome.status,
                error_type=answer_outcome.error_type,
                gold_claims=answer_g,
                predictions=answer_p,
                matched_spans=answer_m,
                correct_labels=answer_c,
                extraction_precision=_per_answer_metric(answer_m, answer_p, quality),
                extraction_recall=_per_answer_metric(answer_m, answer_g, quality),
                extraction_f1=_f1(answer_m, answer_p + answer_g) if quality else 0.0,
                joint_precision=_per_answer_metric(answer_c, answer_p, quality),
                joint_recall=_per_answer_metric(answer_c, answer_g, quality),
                joint_f1=_f1(answer_c, answer_p + answer_g) if quality else 0.0,
                missed_ids=tuple(answer_missed),
                unmatched_prediction_ids=tuple(answer_extra),
                wrong_label_ids=tuple(answer_wrong),
            )
        )

    return EvaluationResult(
        split=split,
        metrics=metrics,
        scheduled_answer_ids=scheduled_ids,
        outcomes=validated_outcomes,
        gold_claims=gold,
        matches=tuple(matched),
        answer_scores=tuple(answer_scores),
        labeled=bool(labeled),
        quality_available=quality,
    )


def score_split(
    split: str,
    scheduled_answers: Sequence[Any] | Mapping[str, Any] | Iterable[Any],
    outcomes: Sequence[AnswerOutcome] | Mapping[str, AnswerOutcome] | Iterable[AnswerOutcome],
    gold_claims: Sequence[GoldClaim] | Iterable[GoldClaim] | None = None,
    *,
    labeled: bool | None = None,
) -> Metrics:
    """Convenience API returning only the contract ``Metrics`` model."""

    return evaluate_split(
        split,
        scheduled_answers,
        outcomes,
        gold_claims,
        labeled=labeled,
    ).metrics


# Names used by a few integrations; keeping them aliases avoids coupling the
# runner to one spelling while retaining one implementation of the rules.
evaluate = evaluate_split
score = score_split


__all__ = [
    "AnswerScore",
    "CONFUSION_COLUMNS",
    "CONFUSION_ROWS",
    "EvaluationInputError",
    "EvaluationResult",
    "MatchedClaim",
    "OutcomeValidationError",
    "evaluate",
    "evaluate_split",
    "score",
    "score_split",
    "validate_outcomes",
]
