"""Compatibility entry point for the deterministic split scorer."""

from .evaluator import (
    AnswerScore,
    CONFUSION_COLUMNS,
    CONFUSION_ROWS,
    EvaluationInputError,
    EvaluationResult,
    MatchedClaim,
    OutcomeValidationError,
    evaluate,
    evaluate_split,
    score,
    score_split,
    validate_outcomes,
)

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
