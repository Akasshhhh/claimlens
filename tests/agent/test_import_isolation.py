from __future__ import annotations

import ast
import sys
from pathlib import Path

import agent


def test_agent_import_graph_has_no_harness_or_gold_dependency() -> None:
    root = Path(agent.__file__).resolve().parent
    imported: set[str] = set()
    for source in root.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

    assert not any("harness" in name.lower() for name in imported)
    assert not any("gold" in name.lower() for name in imported)
    assert not any("development_gold" in name for name in sys.modules)
