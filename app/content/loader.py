"""Load and validate a case file. TOML is read with the standard library (tomllib)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from app.content.schema import Case


def load_case(path: str | Path) -> Case:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    return Case.model_validate(data)
