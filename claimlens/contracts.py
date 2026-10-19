"""Shared contracts.  Modules may depend on these, not on the harness."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

Label = Literal["supported", "contradicted", "unknown"]
AnswerStatus = Literal["ok", "error"]


class AgentInput(BaseModel):
    """The only data exposed to the inference agent."""

    model_config = ConfigDict(extra="forbid")

    answer_id: str
    brand_id: str
    question: str
    ai_answer: str


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    quote: str
    label: Label
    fact_ids: list[str] = Field(default_factory=list)
    reason: str = ""

    @field_validator("fact_ids")
    @classmethod
    def unique_fact_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("fact_ids must be unique")
        return value


class AgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_id: str
    claims: list[Claim] = Field(default_factory=list)


class AnswerOutcome(BaseModel):
    """Terminal answer record; errors are never converted into predictions."""

    model_config = ConfigDict(extra="forbid")

    answer_id: str
    status: AnswerStatus
    latency_ms: float = Field(ge=0)
    trace_id: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    claims: list[Claim] = Field(default_factory=list)

    @field_validator("error_type", "error_message")
    @classmethod
    def sanitize_error_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.replace("\n", " ").replace("\r", " ")[:500]


class Metrics(BaseModel):
    """All deterministic scorer outputs; quality is null for an unlabeled split."""

    model_config = ConfigDict(extra="forbid")

    split: str
    gold_claims: int
    predictions: int
    matched_spans: int
    correct_labels: int
    answer_errors: int
    extraction_precision: float | None = None
    extraction_recall: float | None = None
    extraction_f1: float | None = None
    joint_precision: float | None = None
    joint_recall: float | None = None
    joint_f1: float | None = None
    coverage: float | None = None
    per_label: dict[str, dict[str, float | int]] = Field(default_factory=dict)
    macro_f1: float | None = None
    confusion_matrix: dict[str, dict[str, int]] = Field(default_factory=dict)
    missed_ids: list[str] = Field(default_factory=list)
    unmatched_prediction_ids: list[str] = Field(default_factory=list)
    wrong_label_ids: list[str] = Field(default_factory=list)


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str
    brand_id: str
    model_id: str
    field: str
    value: str | int | float | bool
    value_type: Literal["string", "number", "boolean"]
    unit: str | None = None
    condition: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None


class FactBundle(BaseModel):
    brand_id: str
    fact_version: str = "speaker-catalogue-v4"
    facts: list[Fact]


class AnswerRecord(AgentInput):
    split: Literal["development", "check"]


class GoldClaim(BaseModel):
    answer_id: str
    claim_id: str
    start_char: int
    end_char: int
    quote: str
    expected_label: Label
    evidence_fact_ids: list[str] = Field(default_factory=list)
    explanation: str
    difficulty_tags: list[str] = Field(default_factory=list)


class Provider(Protocol):
    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Any:
        """Return one provider response for one model turn."""


class FactStore(Protocol):
    def get_brand_facts(self, brand_id: str) -> FactBundle:
        """Return only rows for one known brand or raise UnknownBrandError."""


class Tracer(Protocol):
    async def start_answer(self, metadata: dict[str, Any], input_data: AgentInput) -> str | None:
        ...

    async def generation(
        self,
        trace_id: str | None,
        *,
        name: str,
        input_data: Any,
        output_data: Any,
        metadata: dict[str, Any],
    ) -> None:
        ...

    async def tool_observation(
        self,
        trace_id: str | None,
        *,
        name: str,
        input_data: Any,
        output_data: Any,
        metadata: dict[str, Any],
    ) -> None:
        ...

    async def end_answer(
        self, trace_id: str | None, *, output_data: Any, metadata: dict[str, Any]
    ) -> None:
        ...

    async def score(
        self, trace_id: str | None, *, name: str, value: float, metadata: dict[str, Any]
    ) -> None:
        ...

    async def flush(self) -> None:
        ...


DATASET_VERSION = "long-brand-answers-50-v4"
FACT_VERSION = "speaker-catalogue-v4"
EVALUATOR_VERSION = "sentence-spans-and-labels-v4"
QUESTION_DATE = date(2026, 10, 1)
SUPPORTED_LABELS = ("supported", "contradicted", "unknown")


class UnknownBrandError(ValueError):
    """A requested brand is not present in the fact catalogue."""


class ProviderTimeoutError(TimeoutError):
    """A provider request exceeded its bounded timeout."""


class ProviderTransientError(RuntimeError):
    """A provider failure eligible for one bounded retry."""


class ProviderAuthError(RuntimeError):
    """An authentication failure that must never be retried."""


def typed_fact_value(value: str, value_type: str) -> str | int | float | bool:
    """Parse CSV values without Python's unsafe bool('false') behavior."""

    if value_type == "string":
        return value
    if value_type == "boolean":
        if value == "true":
            return True
        if value == "false":
            return False
        raise ValueError(f"invalid boolean value: {value!r}")
    if value_type == "number":
        number = Decimal(value)
        return int(number) if number == number.to_integral_value() else float(number)
    raise ValueError(f"unsupported value_type: {value_type!r}")
