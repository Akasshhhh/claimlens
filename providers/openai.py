"""Official OpenAI SDK provider adapter.

The adapter deliberately returns the SDK response unchanged.  The agent owns
response normalization and strict output validation; this module only maps
transport failures to the shared provider error contract.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import openai

from claimlens.contracts import (
    ProviderAuthError,
    ProviderTimeoutError,
    ProviderTransientError,
)


DEFAULT_REQUEST_TIMEOUT = 15.0
DEFAULT_REASONING_EFFORT = "minimal"
DEFAULT_MAX_COMPLETION_TOKENS = 5000


def _status_code(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", getattr(exc, "status", None))
    return value if isinstance(value, int) else None


def _message(exc: BaseException) -> str:
    text = str(exc).replace("\n", " ").replace("\r", " ")
    return text[:500] or type(exc).__name__


class OpenAIProvider:
    """One OpenAI Chat Completions request per ``complete`` call.

    ``AsyncOpenAI`` is injectable so tests and local callers can use a fake
    without changing the production request shape.
    """

    provider_name = "openai"
    reasoning_effort = DEFAULT_REASONING_EFFORT

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        *,
        client: Any | None = None,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
        max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
    ) -> None:
        if request_timeout <= 0:
            raise ValueError("request_timeout must be positive")

        self.api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")
        self.model = model if model is not None else os.getenv("OPENAI_MODEL", "")
        if not self.model:
            raise ValueError("OPENAI_MODEL is not set")
        if client is None and not self.api_key:
            raise ProviderAuthError("OPENAI_API_KEY is not set")

        self.request_timeout = float(request_timeout)
        if max_completion_tokens <= 0:
            raise ValueError("max_completion_tokens must be positive")
        self.reasoning_effort = reasoning_effort
        self.max_completion_tokens = int(max_completion_tokens)
        self._client = client or openai.AsyncOpenAI(
            api_key=self.api_key,
            timeout=self.request_timeout,
        )

    @property
    def settings(self) -> dict[str, Any]:
        """Exact non-secret request settings for run metadata."""

        return {
            "provider": self.provider_name,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "temperature": None,
            "request_timeout_seconds": self.request_timeout,
            "max_completion_tokens": self.max_completion_tokens,
        }

    @property
    def metadata(self) -> dict[str, Any]:
        return dict(self.settings)

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Any:
        """Make one bounded SDK request using the shared provider contract."""

        try:
            # Do not add temperature: reasoning models reject it and the
            # assessment requires the explicit low reasoning setting.
            return await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                reasoning_effort=self.reasoning_effort,
                max_completion_tokens=self.max_completion_tokens,
                timeout=self.request_timeout,
            )
        except ProviderAuthError:
            raise
        except ProviderTimeoutError:
            raise
        except ProviderTransientError:
            raise
        except openai.AuthenticationError as exc:
            raise ProviderAuthError("OpenAI authentication failed") from exc
        except openai.RateLimitError as exc:
            raise ProviderTransientError(f"OpenAI rate limit: {_message(exc)}") from exc
        except openai.APITimeoutError as exc:
            raise ProviderTimeoutError("OpenAI request timed out") from exc
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise ProviderTimeoutError("OpenAI request timed out") from exc
        except openai.InternalServerError as exc:
            raise ProviderTransientError(f"OpenAI server error: {_message(exc)}") from exc
        except openai.APIConnectionError as exc:
            raise ProviderTransientError(f"OpenAI connection error: {_message(exc)}") from exc
        except openai.APIStatusError as exc:
            status = _status_code(exc)
            if status in {401, 403}:
                raise ProviderAuthError("OpenAI authentication failed") from exc
            if status == 408:
                raise ProviderTimeoutError("OpenAI request timed out") from exc
            if status == 429 or status is not None and status >= 500:
                raise ProviderTransientError(f"OpenAI API error: {_message(exc)}") from exc
            raise
        except Exception as exc:
            # Fakes and compatible OpenAI endpoints often expose status_code
            # without using the SDK exception classes.
            status = _status_code(exc)
            if status in {401, 403}:
                raise ProviderAuthError("OpenAI authentication failed") from exc
            if status == 408:
                raise ProviderTimeoutError("OpenAI request timed out") from exc
            if status == 429 or status is not None and status >= 500:
                raise ProviderTransientError(f"OpenAI API error: {_message(exc)}") from exc
            raise


OpenAIAdapter = OpenAIProvider


__all__ = [
    "DEFAULT_REASONING_EFFORT",
    "DEFAULT_REQUEST_TIMEOUT",
    "OpenAIAdapter",
    "OpenAIProvider",
]
