"""Deterministic evaluation and comparison helpers.

Only the harness imports development labels.  Agent/provider modules should
depend on ``claimlens.contracts`` and never import this package.
"""

from .comparison import (
    CANARY_ANSWER_IDS,
    BootstrapCI,
    VersionComparison,
    build_injection_canary_report,
    compare_runs,
    compare_v1_v2,
    compare_versions,
    claim_flip_table,
    injection_canary_report,
    per_answer_deltas,
    per_difficulty_tag_recall,
    per_label_regressions,
    paired_bootstrap_ci,
)
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
from .reports import (
    build_comparison_report,
    build_report,
    load_report,
    read_report,
    save_report,
    write_comparison_report,
    write_report,
)

__all__ = [
    "AnswerScore",
    "BootstrapCI",
    "CANARY_ANSWER_IDS",
    "CONFUSION_COLUMNS",
    "CONFUSION_ROWS",
    "EvaluationInputError",
    "EvaluationResult",
    "MatchedClaim",
    "OutcomeValidationError",
    "VersionComparison",
    "build_comparison_report",
    "build_injection_canary_report",
    "build_report",
    "compare_runs",
    "compare_v1_v2",
    "compare_versions",
    "claim_flip_table",
    "evaluate",
    "evaluate_split",
    "injection_canary_report",
    "load_report",
    "paired_bootstrap_ci",
    "per_answer_deltas",
    "per_difficulty_tag_recall",
    "per_label_regressions",
    "read_report",
    "save_report",
    "score",
    "score_split",
    "validate_outcomes",
    "write_report",
    "write_comparison_report",
]
