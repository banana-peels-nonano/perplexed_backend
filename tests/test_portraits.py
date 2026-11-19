"""Stakeholder looks: art direction from the case file, passed through to the portal only."""

import pytest
from pydantic import ValidationError

from app.content.loader import load_case
from app.content.schema import Look
from app.interview.prompt import build_system
from tests.test_alliances_submissions import _login

API = "/api/v1"
FIXTURE = "tests/fixtures/northfield.toml"


def test_look_is_optional_and_validated():
    case = load_case(FIXTURE)
    assert case.stakeholder["cfo"].look.hair == "bob"
    assert case.stakeholder["ops_head"].look is None
    with pytest.raises(ValidationError):
        Look(hair="mohawk")
    with pytest.raises(ValidationError):
        Look(background="red")  # colours are #rrggbb
    with pytest.raises(ValidationError):
        Look(skin=7)


def test_look_never_reaches_the_model():
    system = build_system(load_case(FIXTURE), "cfo")
    assert "B23A5C" not in system and "dark_brown" not in system and "blazer" not in system


async def test_portal_gets_the_look(seeded, make_client):
    a01 = await _login(make_client, seeded["A01"])
    people = {s["id"]: s for s in (await a01.get(f"{API}/stakeholders")).json()}
    assert people["cfo"]["look"]["background"] == "#B23A5C"
    assert people["cfo"]["look"]["expression"] == "stern"
    assert people["ops_head"]["look"] == {}  # drawn from the id instead
