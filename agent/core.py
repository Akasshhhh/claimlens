"""Provider/tool loop and strict validation for the ClaimLens agent.

This module intentionally knows only the shared contracts.  It never loads
answers, labels, or evaluation data; the model sees one answer and facts only
after it requests them through the read-only tool.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import weakref
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from claimlens.contracts import (
    AgentInput,
    AgentOutput,
    AnswerOutcome,
    Claim,
    DATASET_VERSION,
    EVALUATOR_VERSION,
    FactBundle,
    FactStore,
    Provider,
    ProviderAuthError,
    ProviderTimeoutError,
    ProviderTransientError,
    SUPPORTED_LABELS,
    Tracer,
    UnknownBrandError,
    FACT_VERSION,
)
from claimlens.tracing import NoOpTracer
from prompts import get_prompt


TOOL_NAME = "get_brand_facts"
MAX_TURNS = 3
MAX_ATTEMPTS_PER_TURN = 2
DEFAULT_REQUEST_TIMEOUT = 15.0
DEFAULT_RETRY_DELAY = 0.05


_IMPERATIVE_START = re.compile(
    r"^(?:please\s+)?(?:buy|check|compare|consider|choose|keep|read|start|ask|look|use|make|ensure|avoid|remember|tell|ignore|label|follow|do|don't|don’t|always|never|return|output|state(?=\s+(?:that|whether|the|this|it|all|every)\b)|say|report|classify|select|pick|prefer|try|verify|separate|retain|begin|provide)\b",
    re.IGNORECASE,
)
_ADVICE_START = re.compile(
    r"^(?:you\s+(?:should|must|can|could|may\s+want\s+to|might\s+want\s+to)|"
    r"i(?:'d| would)?\s+recommend|my\s+recommendation\s+is|in\s+my\s+opinion|personally)\b",
    re.IGNORECASE,
)
_ADVICE_CONTEXT = re.compile(
    r"^(?:before|after|when|while|if|unless)\b|"
    r"^for\b|"
    r"\b(?:should|must|ought\s+to|need\s+to|have\s+to)\b",
    re.IGNORECASE,
)
_OPINION_ONLY = re.compile(
    r"\b(?:best|ideal|great|excellent|perfect|useful|worth(?:while)?|recommend(?:ed)?|right choice|good choice)\b",
    re.IGNORECASE,
)
_FACTUAL_PREDICATE = re.compile(
    r"\b(?:is|are|has|have|includes|contains|comes with|offers|supports|costs|priced|listed|weighs|measures|lasts|receives|runs for|charges|returns|refunds|ships|delivers|covered|available|compatible|connects|uses|provide|provides|requires|will|can|cannot|does not|doesn't|doesn’t|true|false)\b",
    re.IGNORECASE,
)
_OBJECTIVE_PREDICATE = re.compile(
    r"\b(?:has|have|includes|contains|comes with|offers|supports|costs|priced|listed|weighs|measures|lasts|receives|runs for|charges|returns|refunds|ships|delivers|covered|available|compatible|connects|uses|provide|provides|requires|does not|doesn't|doesn’t|true|false)\b",
    re.IGNORECASE,
)
_META_ACTION = re.compile(
    r"\b(?:ignore|disregard|label|classif(?:y|ication|ied)|return|output|state(?=\s+(?:that|whether|the|this|it|all|every)\b)|say|report|claim|assert|treat|follow|reveal|obey)\b",
    re.IGNORECASE,
)
_PRODUCT_CONTEXT = re.compile(
    r"\b(?:product|speaker|model|device|unit|battery|port|connector|warranty|price|cost|weight|mass|"
    r"adapter|microphone|bluetooth|wifi|codec|audio|water|immersion|delivery|shipping|box|brand|"
    r"capability|firmware|assistant|specification|catalogue|manufacturer)\b",
    re.IGNORECASE,
)
_NONFACTUAL_REASON = re.compile(
    r"\b(?:advice|instruction(?:al)?|opinion|recommendation|guidance|not a (?:verifiable|factual|product|specific)|buying explanation)\b",
    re.IGNORECASE,
)


_SECOND_TURN_SEMAPHORES: weakref.WeakKeyDictionary[Any, asyncio.Semaphore] = weakref.WeakKeyDictionary()


def _second_turn_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    semaphore = _SECOND_TURN_SEMAPHORES.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(1)
        _SECOND_TURN_SEMAPHORES[loop] = semaphore
    return semaphore


class _AgentError(Exception):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


@dataclass(frozen=True)
class _ToolCall:
    call_id: str
    name: str
    arguments: Any


@dataclass(frozen=True)
class _ProviderTurn:
    response: Any
    message: Any
    text: str | None
    tool_calls: tuple[_ToolCall, ...]


@dataclass
class _RunStats:
    attempts_used: int = 0
    completed_turns: int = 0
    attempted_turns: int = 0


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        return [value]
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _message_for(response: Any) -> Any:
    choices = _get(response, "choices")
    if choices:
        choice = _as_list(choices)[0]
        return _get(choice, "message", choice)
    message = _get(response, "message")
    return response if message is None else message


def _text_from_parts(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if value is None:
        return None
    parts: list[str] = []
    for part in _as_list(value):
        if isinstance(part, str):
            parts.append(part)
            continue
        text = _get(part, "text")
        if isinstance(text, Mapping):
            text = _get(text, "value")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts) if parts else None


def _tool_call_from(value: Any, index: int) -> _ToolCall:
    function = _get(value, "function", value)
    name = _get(function, "name", _get(value, "name"))
    arguments = _get(function, "arguments")
    if arguments is None:
        arguments = _get(value, "input", _get(value, "arguments"))
    call_id = _get(value, "id", _get(value, "call_id"))
    return _ToolCall(
        call_id=str(call_id) if call_id is not None else f"tool-call-{index + 1}",
        name=name if isinstance(name, str) else "",
        arguments=arguments,
    )


def _provider_turn(response: Any) -> _ProviderTurn:
    message = _message_for(response)
    calls = _get(message, "tool_calls")
    if calls is None and message is not response:
        calls = _get(response, "tool_calls")
    call_values = [_tool_call_from(item, index) for index, item in enumerate(_as_list(calls))]

    # Anthropic-style content blocks and OpenAI Responses function_call items
    # are normalized here so the rest of the loop has one tool-call shape.
    content = _get(message, "content")
    for item in _as_list(content):
        if _get(item, "type") == "tool_use":
            call = _tool_call_from(item, len(call_values))
            if not any(existing.call_id == call.call_id for existing in call_values):
                call_values.append(call)
    output = _get(response, "output")
    if output is not None:
        for item in _as_list(output):
            if _get(item, "type") in {"function_call", "tool_use"}:
                call = _tool_call_from(item, len(call_values))
                if not any(existing.call_id == call.call_id for existing in call_values):
                    call_values.append(call)

    text = _text_from_parts(_get(message, "content"))
    if text is None:
        text = _text_from_parts(_get(response, "output_text"))
    return _ProviderTurn(response, message, text, tuple(call_values))


def _json_for_tool_arguments(arguments: Any) -> str:
    if isinstance(arguments, str):
        return arguments
    try:
        return json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise _AgentError("invalid_output", f"tool arguments are not JSON: {exc}") from exc


def _parse_tool_arguments(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise _AgentError("invalid_output", f"invalid tool arguments: {exc.msg}") from exc
    if not isinstance(arguments, Mapping):
        raise _AgentError("invalid_output", "tool arguments must be a JSON object")
    result = dict(arguments)
    if set(result) != {"brand_id"} or not isinstance(result["brand_id"], str):
        raise _AgentError("invalid_output", "get_brand_facts requires only a string brand_id")
    if not result["brand_id"]:
        raise _AgentError("invalid_output", "brand_id must not be empty")
    return result


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _jsonable(model_dump(mode="json"))
    return str(value)


def _bundle_payload(bundle: FactBundle) -> dict[str, Any]:
    # Keep the tool response lossless while omitting repeated nulls and the
    # per-row brand field already fixed by the bundle.  This reduces prompt
    # latency for long answers without changing the catalogue facts.
    facts: list[dict[str, Any]] = []
    for fact in bundle.facts:
        row = fact.model_dump(mode="json")
        row.pop("brand_id", None)
        facts.append({key: value for key, value in row.items() if value is not None})
    return {"brand_id": bundle.brand_id, "fact_version": bundle.fact_version, "facts": facts}


def _response_payload(response: Any, text: str | None) -> Any:
    if text is not None:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise _AgentError("invalid_output", f"model output is not JSON: {exc.msg}") from exc
    if isinstance(response, (str, bytes, bytearray)):
        try:
            return json.loads(response)
        except json.JSONDecodeError as exc:
            raise _AgentError("invalid_output", f"model output is not JSON: {exc.msg}") from exc
    if isinstance(response, Mapping) and "claims" in response:
        return response
    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="python")
        if isinstance(payload, Mapping) and "claims" in payload:
            return payload
    raise _AgentError("invalid_output", "model returned neither tool calls nor a JSON result")


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _validate_reason(reason: str) -> None:
    # Count terminal punctuation groups rather than every period, so a
    # decimal such as 1.4 does not look like two sentences.
    endings = re.findall(r"[.!?]+(?=\s|$)", reason.strip())
    if len(endings) > 2:
        raise _AgentError("invalid_output", "claim reason must contain at most two sentences")


def _normalize_reason(reason: str) -> str:
    """Trim and cap a model explanation at two complete sentences."""

    reason = reason.strip()
    endings = list(re.finditer(r"[.!?]+(?=\s|$)", reason))
    if len(endings) >= 2:
        # A third sentence may omit terminal punctuation.  The text after the
        # second boundary is still excess explanation and is removed.
        cutoff = endings[1].end()
        if reason[cutoff:].strip():
            reason = reason[:cutoff]
    return reason


def _strip_instruction_lead(text: str) -> str:
    for pattern in (_IMPERATIVE_START, _ADVICE_START):
        match = pattern.match(text)
        if match:
            return text[match.end() :].lstrip()
    return text


def _has_independent_factual_clause(text: str) -> bool:
    """Find a factual clause outside a pure instruction/meta-instruction."""

    # A hard clause boundary is enough to separate advice from a factual
    # assertion (for example, ``Compare prices: Mini costs INR 1,299.``).
    clauses = re.split(r"(?<=[;:])\s+", text)
    for index, clause in enumerate(clauses):
        # Check the original clause before stripping a leading verb.  This
        # prevents ``Return JSON ... is supported`` from treating the copula
        # inside the meta-instruction as an independent product assertion.
        if _META_ACTION.search(clause):
            continue
        candidate = _strip_instruction_lead(clause) if index == 0 else clause
        if _has_product_fact(candidate):
            return True
    return False


def _has_product_fact(text: str) -> bool:
    if not _FACTUAL_PREDICATE.search(text):
        return False
    # A named model in a later clause is enough context for a factual
    # assertion; ignore the sentence-initial capitalized verb/article.
    named_or_number = any(
        match.start() > 0 and match.group() not in {"The", "A", "An", "I", "You"}
        for match in re.finditer(r"\b[A-Z][A-Za-z0-9-]*\b", text)
    ) or bool(re.search(r"\d", text))
    # Generic advice often contains a copula in a subordinate clause (for
    # example, ``the explanation does not resolve...``).  Require product
    # context or a named/quantified target before treating it as a fact.
    if _OBJECTIVE_PREDICATE.search(text):
        return bool(_PRODUCT_CONTEXT.search(text) or named_or_number)
    if _PRODUCT_CONTEXT.search(text) and not re.search(r"\b(?:you|i|we|they)\s+(?:is|are)\b", text, re.I):
        return True
    return named_or_number


def _is_nonfactual_sentence(quote: str, reason: str) -> bool:
    """Reject pure advice/instructions while retaining factual clauses.

    This is deliberately conservative: an imperative or opinion sentence is
    filtered only when it has no ordinary factual predicate. A sentence such
    as ``Compare the prices: Mini costs INR 1,299`` remains an evaluation unit.
    """

    text = quote.strip()
    instruction_lead = bool(_IMPERATIVE_START.match(text) or _ADVICE_START.match(text))
    if instruction_lead:
        # Meta-actions such as "say", "label", and "return" turn a sentence
        # containing a predicate into an instruction rather than a product
        # assertion.  Keep only a separate factual clause after it.
        if _META_ACTION.search(text):
            return not _has_independent_factual_clause(text)
        if not _has_independent_factual_clause(text):
            return True
    if _ADVICE_CONTEXT.search(text) and not _has_product_fact(text):
        return True
    if _OPINION_ONLY.search(text) and not _OBJECTIVE_PREDICATE.search(text):
        return True
    return bool(_NONFACTUAL_REASON.search(reason) and not _FACTUAL_PREDICATE.search(text))


def validate_output(
    payload: Any,
    input_data: AgentInput,
    known_fact_ids: set[str] | frozenset[str],
) -> AgentOutput:
    """Validate model JSON against the exact source answer and tool facts."""

    if isinstance(payload, AgentOutput):
        raw = payload.model_dump(mode="python")
    elif isinstance(payload, Mapping):
        raw = dict(payload)
    else:
        raise _AgentError("invalid_output", "model result must be a JSON object")
    if set(raw) != {"answer_id", "claims"}:
        raise _AgentError("invalid_output", "result must contain only answer_id and claims")
    if not isinstance(raw["answer_id"], str) or raw["answer_id"] != input_data.answer_id:
        raise _AgentError("invalid_output", "result answer_id does not match input")
    if not isinstance(raw["claims"], list):
        raise _AgentError("invalid_output", "claims must be a JSON list")

    claims: list[Claim] = []
    seen_ids: set[str] = set()
    seen_raw_ids: set[str] = set()
    previous_end = -1
    for item in raw["claims"]:
        if not isinstance(item, Mapping):
            raise _AgentError("invalid_output", "each claim must be a JSON object")
        claim_raw = dict(item)
        required = {"claim_id", "start_char", "end_char", "quote", "label", "fact_ids", "reason"}
        if set(claim_raw) != required:
            raise _AgentError("invalid_output", "claim fields are incomplete or unknown")
        if not isinstance(claim_raw["claim_id"], str):
            raise _AgentError("invalid_output", "claim_id must be a string")
        if claim_raw["claim_id"] in seen_raw_ids:
            raise _AgentError("invalid_output", "claim IDs must be unique")
        seen_raw_ids.add(claim_raw["claim_id"])
        for field in ("start_char", "end_char"):
            if _is_bool(claim_raw[field]) or not isinstance(claim_raw[field], int):
                raise _AgentError("invalid_output", f"{field} must be an integer")
        if not isinstance(claim_raw["quote"], str) or not isinstance(claim_raw["reason"], str):
            raise _AgentError("invalid_output", "quote and reason must be strings")
        if claim_raw["label"] not in SUPPORTED_LABELS:
            raise _AgentError("invalid_output", "label is not supported")
        if not isinstance(claim_raw["fact_ids"], list) or not all(
            isinstance(fact_id, str) for fact_id in claim_raw["fact_ids"]
        ):
            raise _AgentError("invalid_output", "fact_ids must be a list of strings")
        if len(claim_raw["fact_ids"]) != len(set(claim_raw["fact_ids"])):
            raise _AgentError("invalid_output", "fact_ids must be unique")
        if any(fact_id not in known_fact_ids for fact_id in claim_raw["fact_ids"]):
            raise _AgentError("invalid_output", "claim cites a fact ID not returned by the tool")

        raw_start = claim_raw["start_char"]
        raw_end = claim_raw["end_char"]
        quote = claim_raw["quote"]
        if raw_start < 0 or raw_end <= raw_start or raw_end > len(input_data.ai_answer):
            raise _AgentError("invalid_output", "claim offsets are out of bounds")
        if quote != quote.strip() or not quote:
            raise _AgentError("invalid_output", "quote must be non-empty and trimmed")

        # The model is not a reliable Unicode offset counter.  Use its exact
        # quote as the source of truth and resolve the occurrence in Python,
        # preferring the model's proposed location when it is one of several
        # repeated occurrences.  This is the contract's permitted offset
        # localization step, not fuzzy matching or gold-assisted correction.
        candidates: list[int] = []
        cursor = input_data.ai_answer.find(quote, max(previous_end, 0))
        while cursor >= 0:
            candidates.append(cursor)
            cursor = input_data.ai_answer.find(quote, cursor + 1)
        if not candidates:
            raise _AgentError("invalid_output", "quote does not occur exactly in source answer")
        start = min(candidates, key=lambda candidate: abs(candidate - raw_start))
        end = start + len(quote)
        if start < previous_end:
            raise _AgentError("invalid_output", "claims overlap or are not sorted")
        claim_raw["start_char"] = start
        claim_raw["end_char"] = end
        canonical_id = f"{input_data.answer_id}:{start}:{end}"
        if canonical_id in seen_ids:
            raise _AgentError("invalid_output", "claim IDs must be unique")
        claim_raw["claim_id"] = canonical_id
        exact_quote = input_data.ai_answer[start:end]
        if quote != exact_quote:
            raise _AgentError("invalid_output", "quote does not equal its exact source span")
        if start == previous_end and claims and start <= claims[-1].start_char:
            raise _AgentError("invalid_output", "claims must increase by start offset")
        seen_ids.add(canonical_id)
        # Reasons are explanatory metadata, not scored text.  Keep the first
        # two complete sentences when a model adds a trailing third sentence;
        # this preserves the contract without discarding an otherwise valid
        # extraction solely for verbosity in its explanation.
        claim_raw["reason"] = _normalize_reason(claim_raw["reason"])
        _validate_reason(claim_raw["reason"])
        if _is_nonfactual_sentence(quote, claim_raw["reason"]):
            continue
        try:
            claim = Claim.model_validate(claim_raw)
        except Exception as exc:
            raise _AgentError("invalid_output", f"claim validation failed: {exc}") from exc
        claims.append(claim)
        previous_end = end

    try:
        return AgentOutput(answer_id=input_data.answer_id, claims=claims)
    except Exception as exc:
        raise _AgentError("invalid_output", f"agent output validation failed: {exc}") from exc


def _tool_schema() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "description": "Read the complete supplied fact catalogue for one known brand.",
                "parameters": {
                    "type": "object",
                    "properties": {"brand_id": {"type": "string"}},
                    "required": ["brand_id"],
                    "additionalProperties": False,
                },
            },
        }
    ]


def _initial_messages(input_data: AgentInput, prompt: str) -> list[dict[str, Any]]:
    # The tags are data boundaries, not executable syntax.  Keep the answer
    # untouched so the model's offsets refer to the original input string.
    user_content = (
        f"answer_id: {input_data.answer_id}\n"
        f"primary_brand_id: {input_data.brand_id}\n"
        f"<question>\n{input_data.question}\n</question>\n"
        "<untrusted_answer>\n"
        f"{input_data.ai_answer}\n"
        "</untrusted_answer>"
    )
    return [{"role": "system", "content": prompt}, {"role": "user", "content": user_content}]


def _assistant_tool_message(turn: _ProviderTurn) -> dict[str, Any]:
    calls = []
    for call in turn.tool_calls:
        calls.append(
            {
                "id": call.call_id,
                "type": "function",
                "function": {"name": call.name, "arguments": _json_for_tool_arguments(call.arguments)},
            }
        )
    return {"role": "assistant", "content": turn.text, "tool_calls": calls}


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, (ProviderTimeoutError, asyncio.TimeoutError, TimeoutError, ProviderTransientError)):
        return True
    status = getattr(exc, "status_code", getattr(exc, "status", None))
    if status == 429:
        return True
    return "rate" in type(exc).__name__.lower() and "limit" in type(exc).__name__.lower()


class ClaimAgent:
    """A bounded provider loop that returns one terminal answer outcome."""

    def __init__(
        self,
        provider: Provider,
        fact_store: FactStore,
        tracer: Tracer | None = None,
        *,
        agent_version: str = "v1",
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        max_turns: int = MAX_TURNS,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        run_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if agent_version not in {"v1", "v2"}:
            raise ValueError(f"unsupported agent version: {agent_version!r}")
        if max_turns < 1 or max_turns > MAX_TURNS:
            raise ValueError(f"max_turns must be between 1 and {MAX_TURNS}")
        if request_timeout <= 0:
            raise ValueError("request_timeout must be positive")
        self.provider = provider
        self.fact_store = fact_store
        self.tracer = tracer or NoOpTracer()
        self.agent_version = agent_version
        self.request_timeout = request_timeout
        self.max_turns = max_turns
        self.retry_delay = max(0.0, retry_delay)
        self.run_metadata = dict(run_metadata or {})

    async def _trace(self, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return await getattr(self.tracer, method)(*args, **kwargs)
        except Exception:
            # Observability must not turn a model answer into a prediction or
            # hide the original provider/validation outcome.
            return None

    async def _complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        turn_number: int,
        trace_id: str | None,
        metadata: dict[str, Any],
        stats: _RunStats,
    ) -> Any:
        # The first tool-request turn remains concurrent.  Serializing only
        # post-tool completion turns avoids a provider-side burst when a full
        # split reaches the same large catalogue context simultaneously, while
        # the executor's provider-attempt semaphore remains the hard ceiling.
        if turn_number <= 1:
            return await self._complete_attempts(
                messages,
                tools,
                turn_number=turn_number,
                trace_id=trace_id,
                metadata=metadata,
                stats=stats,
            )
        semaphore = _second_turn_semaphore()
        await semaphore.acquire()
        try:
            return await self._complete_attempts(
                messages,
                tools,
                turn_number=turn_number,
                trace_id=trace_id,
                metadata=metadata,
                stats=stats,
            )
        finally:
            semaphore.release()

    async def _complete_attempts(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        turn_number: int,
        trace_id: str | None,
        metadata: dict[str, Any],
        stats: _RunStats,
    ) -> Any:
        last: BaseException | None = None
        stats.attempted_turns = max(stats.attempted_turns, turn_number)
        for attempt in range(MAX_ATTEMPTS_PER_TURN):
            stats.attempts_used += 1
            await self._trace(
                "provider_attempt",
                trace_id,
                phase="start",
                turn=turn_number,
                attempt=attempt + 1,
                metadata=metadata,
            )
            try:
                response = await asyncio.wait_for(
                    self.provider.complete(messages, tools), timeout=self.request_timeout
                )
                await self._trace(
                    "provider_attempt",
                    trace_id,
                    phase="success",
                    turn=turn_number,
                    attempt=attempt + 1,
                    metadata=metadata,
                )
                return response
            except (ProviderTimeoutError, asyncio.TimeoutError, TimeoutError, ProviderTransientError) as exc:
                last = exc
                await self._trace(
                    "provider_attempt",
                    trace_id,
                    phase="error",
                    turn=turn_number,
                    attempt=attempt + 1,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    metadata=metadata,
                )
                if attempt + 1 < MAX_ATTEMPTS_PER_TURN:
                    if self.retry_delay:
                        await asyncio.sleep(self.retry_delay)
                    continue
                if isinstance(exc, (ProviderTimeoutError, asyncio.TimeoutError, TimeoutError)):
                    raise _AgentError("provider_timeout", "provider request timed out") from exc
                raise _AgentError("provider_failure", f"provider request failed: {exc}") from exc
            except ProviderAuthError as exc:
                await self._trace(
                    "provider_attempt",
                    trace_id,
                    phase="error",
                    turn=turn_number,
                    attempt=attempt + 1,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    metadata=metadata,
                )
                raise _AgentError("provider_failure", f"provider authentication failed: {exc}") from exc
            except Exception as exc:
                last = exc
                await self._trace(
                    "provider_attempt",
                    trace_id,
                    phase="error",
                    turn=turn_number,
                    attempt=attempt + 1,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    metadata=metadata,
                )
                if _retryable(exc) and attempt + 1 < MAX_ATTEMPTS_PER_TURN:
                    if self.retry_delay:
                        await asyncio.sleep(self.retry_delay)
                    continue
                raise _AgentError("provider_failure", f"provider request failed: {exc}") from exc
        raise _AgentError("provider_failure", f"provider request failed: {last}")

    def _known_brand(self, brand_id: str) -> bool:
        brands = getattr(self.fact_store, "brands", None)
        return brands is None or brand_id in brands

    def _execute_tool(self, call: _ToolCall) -> tuple[dict[str, Any], set[str]]:
        if call.name != TOOL_NAME:
            raise _AgentError("illegal_tool", f"unsupported tool: {call.name or '<missing>'}")
        args = _parse_tool_arguments(call.arguments)
        brand_id = args["brand_id"]
        if not self._known_brand(brand_id):
            raise _AgentError("unknown_brand", f"unknown brand: {brand_id}")
        try:
            bundle = self.fact_store.get_brand_facts(brand_id)
        except UnknownBrandError as exc:
            raise _AgentError("unknown_brand", f"unknown brand: {brand_id}") from exc
        except Exception as exc:
            raise _AgentError("provider_failure", f"fact lookup failed: {exc}") from exc
        if not isinstance(bundle, FactBundle):
            try:
                bundle = FactBundle.model_validate(bundle)
            except Exception as exc:
                raise _AgentError("provider_failure", f"fact lookup returned invalid data: {exc}") from exc
        if bundle.brand_id != brand_id or any(fact.brand_id != brand_id for fact in bundle.facts):
            raise _AgentError("provider_failure", "fact lookup returned a different brand")
        return _bundle_payload(bundle), {fact.fact_id for fact in bundle.facts}

    async def run(self, input_data: AgentInput) -> AnswerOutcome:
        started = time.perf_counter()
        trace_id: str | None = None
        known_fact_ids: set[str] = set()
        stats = _RunStats()
        outcome: AnswerOutcome | None = None
        metadata = {
            **self.run_metadata,
            "agent_version": self.agent_version,
            "prompt_version": self.agent_version,
            "dataset_version": DATASET_VERSION,
            "fact_version": FACT_VERSION,
            "evaluator_version": EVALUATOR_VERSION,
            "answer_id": input_data.answer_id,
        }
        try:
            trace_id = await self._trace("start_answer", metadata, input_data)
            messages = _initial_messages(input_data, get_prompt(self.agent_version))
            tools = _tool_schema()
            for turn_number in range(1, self.max_turns + 1):
                response = await self._complete(
                    messages,
                    tools,
                    turn_number=turn_number,
                    trace_id=trace_id,
                    metadata=metadata,
                    stats=stats,
                )
                turn = _provider_turn(response)
                stats.completed_turns += 1
                await self._trace(
                    "generation",
                    trace_id,
                    name=f"model-turn-{turn_number}",
                    input_data={"messages": messages, "turn": turn_number},
                    output_data=_jsonable(response),
                    metadata={**metadata, "turn": turn_number},
                )
                if not turn.tool_calls:
                    parsed = _response_payload(response, turn.text)
                    agent_output = validate_output(parsed, input_data, known_fact_ids)
                    outcome = AnswerOutcome(
                        answer_id=input_data.answer_id,
                        status="ok",
                        latency_ms=(time.perf_counter() - started) * 1000,
                        trace_id=trace_id,
                        claims=agent_output.claims,
                    )
                    break

                # Preserve all calls from this one model turn before asking
                # for the next turn; the tool contract permits parallel calls
                # and the message shape retains their individual IDs.
                messages.append(_assistant_tool_message(turn))
                for call in turn.tool_calls:
                    tool_payload, returned_ids = self._execute_tool(call)
                    known_fact_ids.update(returned_ids)
                    await self._trace(
                        "tool_observation",
                        trace_id,
                        name=TOOL_NAME,
                        input_data={"tool_name": call.name, "arguments": _parse_tool_arguments(call.arguments)},
                        output_data=tool_payload,
                        metadata={**metadata, "turn": turn_number},
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.call_id,
                            "name": TOOL_NAME,
                            "content": json.dumps(
                                tool_payload,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        }
                    )
                if turn_number == self.max_turns:
                    raise _AgentError("turn_exhaustion", f"model did not return a result within {self.max_turns} turns")
        except _AgentError as exc:
            outcome = AnswerOutcome(
                answer_id=input_data.answer_id,
                status="error",
                latency_ms=(time.perf_counter() - started) * 1000,
                trace_id=trace_id,
                error_type=exc.error_type,
                error_message=str(exc),
                claims=[],
            )
        except Exception as exc:
            outcome = AnswerOutcome(
                answer_id=input_data.answer_id,
                status="error",
                latency_ms=(time.perf_counter() - started) * 1000,
                trace_id=trace_id,
                error_type="provider_failure",
                error_message=f"agent failure: {exc}",
                claims=[],
            )
        finally:
            if outcome is None:
                outcome = AnswerOutcome(
                    answer_id=input_data.answer_id,
                    status="error",
                    latency_ms=(time.perf_counter() - started) * 1000,
                    trace_id=trace_id,
                    error_type="provider_failure",
                    error_message="agent did not produce a terminal outcome",
                    claims=[],
                )
            await self._trace(
                "end_answer",
                trace_id,
                output_data=outcome,
                metadata={**metadata, "status": outcome.status, "error_type": outcome.error_type},
            )
            await self._trace(
                "answer_diagnostics",
                trace_id,
                attempts_used=stats.attempts_used,
                turns_used=stats.completed_turns,
                attempted_turns=stats.attempted_turns,
                error_type=outcome.error_type,
                error_message=outcome.error_message,
                metadata=metadata,
            )
            await self._trace("flush")
        return outcome

    async def classify(self, input_data: AgentInput) -> AnswerOutcome:
        return await self.run(input_data)


Agent = ClaimAgent


async def run_agent(
    input_data: AgentInput,
    provider: Provider,
    fact_store: FactStore,
    tracer: Tracer | None = None,
    *,
    agent_version: str = "v1",
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    max_turns: int = MAX_TURNS,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    run_metadata: Mapping[str, Any] | None = None,
) -> AnswerOutcome:
    """Convenience entry point used by the integration runner."""

    return await ClaimAgent(
        provider,
        fact_store,
        tracer,
        agent_version=agent_version,
        request_timeout=request_timeout,
        max_turns=max_turns,
        retry_delay=retry_delay,
        run_metadata=run_metadata,
    ).run(input_data)


async def classify_answer(
    input_data: AgentInput,
    provider: Provider,
    fact_store: FactStore,
    tracer: Tracer | None = None,
    *,
    agent_version: str = "v1",
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    max_turns: int = MAX_TURNS,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    run_metadata: Mapping[str, Any] | None = None,
) -> AnswerOutcome:
    """Alias with a route-friendly name for the main integration."""

    return await run_agent(
        input_data,
        provider,
        fact_store,
        tracer,
        agent_version=agent_version,
        request_timeout=request_timeout,
        max_turns=max_turns,
        retry_delay=retry_delay,
        run_metadata=run_metadata,
    )


__all__ = [
    "Agent",
    "ClaimAgent",
    "classify_answer",
    "run_agent",
    "validate_output",
]
