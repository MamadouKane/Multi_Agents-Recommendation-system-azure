"""Baskets and small JSON documents passed between pipeline steps."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .data import Basket

BASKETS_FILE = "baskets.jsonl"


def write_baskets(baskets: list[Basket], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / BASKETS_FILE
    with path.open("w", encoding="utf-8") as f:
        for basket in baskets:
            f.write(json.dumps({"date": basket.date, "items": sorted(basket.items)}) + "\n")
    return path


def read_baskets(directory: Path) -> list[Basket]:
    with (directory / BASKETS_FILE).open(encoding="utf-8") as f:
        return [Basket(row["date"], frozenset(row["items"])) for row in map(json.loads, f)]


def write_json(document: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_json(path: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document
