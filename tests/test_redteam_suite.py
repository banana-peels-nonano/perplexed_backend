"""The hostile-question suite must stay consistent with the fixture it attacks."""

import importlib.util
import tomllib
from pathlib import Path

from app.content.loader import load_case

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("redteam", ROOT / "scripts" / "redteam.py")
redteam = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(redteam)


def test_suite_references_real_passages_and_possible_expectations():
    case = load_case(ROOT / "tests" / "fixtures" / "northfield.toml")
    suite = tomllib.loads((ROOT / "tests" / "fixtures" / "northfield_redteam.toml").read_text())
    assert redteam.check_suite(case, suite) == []
    assert {p["category"] for p in suite["probe"]} >= {
        "leading", "fishing", "injection", "cross_company", "relayed", "control"
    }  # fmt: skip
    assert len(suite["recall"]) >= 15
