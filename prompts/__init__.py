"""Versioned system prompts for the ClaimLens inference agent."""

from __future__ import annotations

from pathlib import Path


_PROMPT_FILES = {"v1": "v1.txt", "v2": "v2.txt"}


def get_prompt(version: str) -> str:
    """Load one of the checked-in prompts by its public agent version."""

    try:
        filename = _PROMPT_FILES[version]
    except KeyError as exc:
        raise ValueError(f"unsupported agent version: {version!r}") from exc
    return (Path(__file__).with_name(filename)).read_text(encoding="utf-8")


__all__ = ["get_prompt"]
