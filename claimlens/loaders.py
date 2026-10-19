"""Lossless CSV loaders and catalogue access."""

from __future__ import annotations

import csv
import hashlib
from datetime import date
from pathlib import Path
from typing import Iterable

from .contracts import (
    AnswerRecord,
    Fact,
    FactBundle,
    FactStore,
    GoldClaim,
    UnknownBrandError,
    typed_fact_value,
)

ANSWER_COLUMNS = ["answer_id", "split", "brand_id", "question", "ai_answer"]
FACT_COLUMNS = [
    "fact_id",
    "brand_id",
    "model_id",
    "field",
    "value",
    "value_type",
    "unit",
    "condition",
    "valid_from",
    "valid_to",
]
GOLD_COLUMNS = [
    "answer_id",
    "claim_id",
    "start_char",
    "end_char",
    "quote",
    "expected_label",
    "evidence_fact_ids",
    "explanation",
    "difficulty_tags",
]


def _reader(path: str | Path, expected: list[str]) -> Iterable[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != expected:
            raise ValueError(f"{path}: expected columns {expected}, got {reader.fieldnames}")
        yield from reader


def load_answers(path: str | Path, split: str | None = None) -> list[AnswerRecord]:
    rows = [AnswerRecord.model_validate(row) for row in _reader(path, ANSWER_COLUMNS)]
    return [row for row in rows if split is None or row.split == split]


def _optional_date(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


def load_facts(path: str | Path) -> list[Fact]:
    facts: list[Fact] = []
    for row in _reader(path, FACT_COLUMNS):
        facts.append(
            Fact(
                fact_id=row["fact_id"],
                brand_id=row["brand_id"],
                model_id=row["model_id"],
                field=row["field"],
                value=typed_fact_value(row["value"], row["value_type"]),
                value_type=row["value_type"],
                unit=row["unit"] or None,
                condition=row["condition"] or None,
                valid_from=_optional_date(row["valid_from"]),
                valid_to=_optional_date(row["valid_to"]),
            )
        )
    return facts


def load_gold(path: str | Path) -> list[GoldClaim]:
    claims: list[GoldClaim] = []
    for row in _reader(path, GOLD_COLUMNS):
        claims.append(
            GoldClaim(
                answer_id=row["answer_id"],
                claim_id=row["claim_id"],
                start_char=int(row["start_char"]),
                end_char=int(row["end_char"]),
                quote=row["quote"],
                expected_label=row["expected_label"],
                evidence_fact_ids=row["evidence_fact_ids"].split("|")
                if row["evidence_fact_ids"]
                else [],
                explanation=row["explanation"],
                difficulty_tags=row["difficulty_tags"].split("|")
                if row["difficulty_tags"]
                else [],
            )
        )
    return claims


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def csv_hashes(data_dir: str | Path = ".") -> dict[str, str]:
    root = Path(data_dir)
    return {
        name: sha256_file(root / name)
        for name in ("answers.csv", "brand_facts.csv", "development_gold.csv")
        if (root / name).exists()
    }


class CsvFactStore(FactStore):
    def __init__(self, facts: list[Fact], fact_version: str = "speaker-catalogue-v4") -> None:
        self._by_brand: dict[str, list[Fact]] = {}
        for fact in facts:
            self._by_brand.setdefault(fact.brand_id, []).append(fact)
        self.fact_version = fact_version

    @classmethod
    def from_csv(cls, path: str | Path) -> "CsvFactStore":
        return cls(load_facts(path))

    @property
    def brands(self) -> set[str]:
        return set(self._by_brand)

    def get_brand_facts(self, brand_id: str) -> FactBundle:
        if brand_id not in self._by_brand:
            raise UnknownBrandError(f"unknown brand: {brand_id}")
        return FactBundle(
            brand_id=brand_id,
            fact_version=self.fact_version,
            facts=list(self._by_brand[brand_id]),
        )
