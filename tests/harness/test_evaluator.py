from __future__ import annotations

import math

import pytest

from claimlens.contracts import AnswerOutcome, AnswerRecord, Claim, GoldClaim
from harness import OutcomeValidationError, evaluate_split


def answer(answer_id: str, text: str) -> AnswerRecord:
    return AnswerRecord(
        answer_id=answer_id,
        split="development",
        brand_id="brand-a",
        question="Which facts are stated?",
        ai_answer=text,
    )


def claim(
    answer_id: str,
    text: str,
    start: int,
    end: int,
    *,
    label: str = "supported",
    claim_id: str | None = None,
) -> Claim:
    return Claim(
        claim_id=claim_id or f"{answer_id}:{start}:{end}",
        start_char=start,
        end_char=end,
        quote=text[start:end],
        label=label,
        fact_ids=[],
        reason="synthetic test claim",
    )


def gold(
    answer_id: str,
    claim_id: str,
    text: str,
    start: int,
    end: int,
    *,
    label: str = "supported",
    tags: list[str] | None = None,
) -> GoldClaim:
    return GoldClaim(
        answer_id=answer_id,
        claim_id=claim_id,
        start_char=start,
        end_char=end,
        quote=text[start:end],
        expected_label=label,
        explanation="synthetic test gold",
        difficulty_tags=tags or [],
    )


def outcome(answer_id: str, claims: list[Claim], *, status: str = "ok") -> AnswerOutcome:
    return AnswerOutcome(
        answer_id=answer_id,
        status=status,
        latency_ms=1,
        error_type="provider_failure" if status == "error" else None,
        error_message="synthetic provider error" if status == "error" else None,
        claims=claims,
    )


def test_perfect_output_has_one_exact_match_per_gold_span() -> None:
    text = "Alpha is true. Beta is true."
    first_end = text.index(".") + 1
    second_start = first_end + 1
    second_end = len(text)
    answers = [answer("A01", text)]
    gold_rows = [
        gold("A01", "g1", text, 0, first_end),
        gold("A01", "g2", text, second_start, second_end),
    ]
    predictions = [
        claim("A01", text, 0, first_end),
        claim("A01", text, second_start, second_end),
    ]

    result = evaluate_split("development", answers, [outcome("A01", predictions)], gold_rows)

    assert result.metrics.model_dump(mode="python")["gold_claims"] == 2
    assert result.metrics.predictions == 2
    assert result.metrics.matched_spans == 2
    assert result.metrics.correct_labels == 2
    assert result.metrics.extraction_f1 == 1
    assert result.metrics.joint_f1 == 1
    assert result.metrics.coverage == 1
    assert result.metrics.missed_ids == []
    assert result.metrics.unmatched_prediction_ids == []


def test_missed_claims_and_valid_empty_predictions_keep_gold_in_denominator() -> None:
    text = "Alpha is true."
    result = evaluate_split(
        "development",
        [answer("A01", text)],
        [outcome("A01", [])],
        [gold("A01", "g1", text, 0, len(text))],
    )

    assert result.metrics.gold_claims == 1
    assert result.metrics.predictions == 0
    assert result.metrics.matched_spans == 0
    assert result.metrics.answer_errors == 0
    assert result.metrics.extraction_precision == 0
    assert result.metrics.extraction_recall == 0
    assert result.metrics.joint_f1 == 0
    assert result.metrics.missed_ids == ["g1"]


def test_extra_advice_prediction_reduces_extraction_precision() -> None:
    text = "Alpha is true. Buy it today."
    factual_end = text.index(".") + 1
    advice_start = factual_end + 1
    result = evaluate_split(
        "development",
        [answer("A01", text)],
        [
            outcome(
                "A01",
                [
                    claim("A01", text, 0, factual_end),
                    claim("A01", text, advice_start, len(text), claim_id="extra-advice"),
                ],
            )
        ],
        [gold("A01", "g1", text, 0, factual_end)],
    )

    assert result.metrics.predictions == 2
    assert result.metrics.matched_spans == 1
    assert result.metrics.extraction_precision == 0.5
    assert result.metrics.extraction_recall == 1
    assert result.metrics.unmatched_prediction_ids == ["extra-advice"]


def test_wrong_label_is_matched_but_not_jointly_correct() -> None:
    text = "Alpha is true."
    result = evaluate_split(
        "development",
        [answer("A01", text)],
        [outcome("A01", [claim("A01", text, 0, len(text), label="contradicted")])],
        [gold("A01", "g1", text, 0, len(text), label="supported")],
    )

    assert result.metrics.matched_spans == 1
    assert result.metrics.correct_labels == 0
    assert result.metrics.extraction_f1 == 1
    assert result.metrics.joint_f1 == 0
    assert result.metrics.wrong_label_ids == ["g1"]
    assert result.metrics.per_label["supported"]["fn"] == 1
    assert result.metrics.per_label["contradicted"]["fp"] == 1
    assert result.metrics.confusion_matrix["supported"]["contradicted"] == 1


def test_provider_error_has_no_predictions_but_its_gold_is_missed() -> None:
    text_ok = "Alpha is true."
    text_error = "Beta is true."
    result = evaluate_split(
        "development",
        [answer("A01", text_ok), answer("A02", text_error)],
        [
            outcome("A01", [claim("A01", text_ok, 0, len(text_ok))]),
            outcome("A02", [], status="error"),
        ],
        [
            gold("A01", "g1", text_ok, 0, len(text_ok)),
            gold("A02", "g2", text_error, 0, len(text_error)),
        ],
    )

    assert result.metrics.answer_errors == 1
    assert result.metrics.gold_claims == 2
    assert result.metrics.predictions == 1
    assert result.metrics.matched_spans == 1
    assert result.metrics.missed_ids == ["g2"]
    assert result.metrics.joint_recall == 0.5


def test_quote_equality_is_checked_before_offsets() -> None:
    text = "Alpha is true."
    prediction = Claim(
        claim_id="p1",
        start_char=0,
        end_char=len(text),
        quote="not the source span",
        label="supported",
        reason="wrong quote",
    )
    result = evaluate_split(
        "development",
        [answer("A01", text)],
        [outcome("A01", [prediction])],
        [gold("A01", "g1", text, 0, len(text))],
    )
    assert result.metrics.matched_spans == 0
    assert result.metrics.unmatched_prediction_ids == ["p1"]
    assert result.metrics.missed_ids == ["g1"]


def test_g10_p10_m8_c7_arithmetic_and_identity_invariants() -> None:
    sentences = [f"Fact {index} is true." for index in range(10)]
    text = " ".join(sentences) + " Extra advice one. Extra advice two."
    starts: list[tuple[int, int]] = []
    cursor = 0
    for sentence in sentences:
        starts.append((cursor, cursor + len(sentence)))
        cursor += len(sentence) + 1
    gold_rows = [gold("A01", f"g{index}", text, start, end) for index, (start, end) in enumerate(starts)]
    predictions = [
        claim("A01", text, start, end, label="contradicted" if index == 0 else "supported")
        for index, (start, end) in enumerate(starts[:8])
    ]
    advice_one = text.index("Extra advice one")
    advice_two = text.index("Extra advice two")
    predictions.extend(
        [
            claim("A01", text, advice_one, advice_one + len("Extra advice one."), claim_id="extra-1"),
            claim("A01", text, advice_two, advice_two + len("Extra advice two."), claim_id="extra-2"),
        ]
    )
    result = evaluate_split(
        "development", [answer("A01", text)], [outcome("A01", predictions)], gold_rows
    )
    metrics = result.metrics

    assert (metrics.gold_claims, metrics.predictions, metrics.matched_spans, metrics.correct_labels) == (
        10,
        10,
        8,
        7,
    )
    assert metrics.extraction_precision == pytest.approx(0.8)
    assert metrics.extraction_recall == pytest.approx(0.8)
    assert metrics.extraction_f1 == pytest.approx(0.8)
    assert metrics.joint_precision == pytest.approx(0.7)
    assert metrics.joint_recall == pytest.approx(0.7)
    assert metrics.joint_f1 == pytest.approx(0.7)
    assert metrics.coverage == pytest.approx(0.7)
    matrix_total = sum(sum(row.values()) for row in metrics.confusion_matrix.values())
    assert matrix_total == metrics.gold_claims + len(metrics.unmatched_prediction_ids)
    assert metrics.matched_spans + len(metrics.unmatched_prediction_ids) == metrics.predictions
    assert metrics.per_label["supported"]["tp"] == 7
    assert metrics.per_label["contradicted"]["fp"] == 1
    assert math.isclose(metrics.macro_f1 or 0, sum(metrics.per_label[label]["f1"] for label in metrics.per_label) / 3)


def test_confusion_matrix_has_required_rows_columns_and_zero_no_gold_missing_cell() -> None:
    text = "Alpha is true. Advice is optional."
    alpha_end = text.index(".") + 1
    advice_start = alpha_end + 1
    result = evaluate_split(
        "development",
        [answer("A01", text)],
        [
            outcome(
                "A01",
                [
                    claim("A01", text, 0, alpha_end),
                    claim("A01", text, advice_start, len(text), claim_id="extra"),
                ],
            )
        ],
        [gold("A01", "g1", text, 0, alpha_end), gold("A01", "g2", text, 0, alpha_end, label="unknown")],
    )
    matrix = result.metrics.confusion_matrix
    assert list(matrix) == ["supported", "contradicted", "unknown", "no_gold"]
    assert list(matrix["supported"]) == ["supported", "contradicted", "unknown", "missing_or_error"]
    assert matrix["no_gold"]["missing_or_error"] == 0
    assert sum(sum(row.values()) for row in matrix.values()) == 3  # G=2 plus one unmatched prediction


def test_empty_scheduled_split_has_null_quality_metrics() -> None:
    result = evaluate_split("development", [], [], [])
    metrics = result.metrics
    assert metrics.gold_claims == 0
    assert metrics.predictions == 0
    assert metrics.answer_errors == 0
    assert metrics.extraction_f1 is None
    assert metrics.joint_f1 is None
    assert metrics.coverage is None
    assert metrics.per_label == {}
    assert metrics.confusion_matrix == {}


def test_missing_duplicate_and_extra_outcomes_are_rejected() -> None:
    text = "Alpha is true."
    scheduled = [answer("A01", text), answer("A02", text)]
    one = outcome("A01", [])
    with pytest.raises(OutcomeValidationError, match="missing outcomes"):
        evaluate_split("development", scheduled, [one], [])
    with pytest.raises(OutcomeValidationError, match="duplicate outcomes"):
        evaluate_split("development", scheduled, [one, one, outcome("A02", [])], [])
    with pytest.raises(OutcomeValidationError, match="unexpected outcomes"):
        evaluate_split("development", [answer("A01", text)], [one, outcome("A99", [])], [])
