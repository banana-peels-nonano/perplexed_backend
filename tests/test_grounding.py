"""Ungrounded facts and used-but-uncited evidence, on replies haiku actually wrote in step 7."""

from pathlib import Path

import pytest

from app.content.loader import load_case
from app.content.schema import Mode
from app.interview.grounding import ungrounded_facts, used_but_uncited

CASE = load_case(Path(__file__).parent / "fixtures" / "northfield.toml")
Q, P = Mode.quote, Mode.paraphrase


@pytest.mark.parametrize(
    "provided, already_said, reply, flagged",
    [
        # facts with no source are flagged; the participant's question is not a source
        ({}, "", "Yes, it was 15 percent.", ["15"]),
        ({}, "", "Yes, the review is on Friday 16 May at 11:00.", ["16", "11:00"]),
        ({}, "", "Anil Rao in operations would know.", ["Anil Rao"]),  # step 7, live
        ({}, "", "Daniel Okafor at Kestrel told me.", ["Daniel Okafor"]),  # other company
        ({"A-FIN-003#3": P}, "", "Average cost is about Rs 3,050 per claim.", ["3050"]),
        # sourced from evidence (as rendered), beliefs, colleagues, history or titles
        ({"A-FIN-002#2": Q}, "", "It falls by 15 percent, from 1.42 crore to 1.21 crore.", []),
        ({"A-FIN-003#3": Q}, "", "Average cost is about Rs 3,050 per claim.", []),
        ({}, "", "My understanding is that it was around five percent.", []),  # belief
        ({}, "", "Arjun Rao in operations would know; Mr Rao is busy.", []),
        ({}, "Revenue was 41.7 crore.", "As I said, 41.7 crore.", []),
        ({"A-CAL-001#1": P}, "", "It is in my calendar for the week of 12 May.", []),
        # everyday numbers are not facts
        ({"A-CAL-001#2": Q}, "", "The pre-read is on Thursday at ten.", []),
        ({}, "", "The bulk of the claims came in over the last six months.", []),
        ({}, "", "From Q1 to Q3 things fell, for two quarters running.", []),
    ],
)
def test_ungrounded_facts(provided, already_said, reply, flagged):
    assert ungrounded_facts(CASE, "cfo", provided, already_said, reply) == flagged


KESTREL_Q = "Do you think the resin from Kestrel is to blame for the failures?"


@pytest.mark.parametrize(
    "provided, cited, reply, question, added",
    [
        (  # step 7e, open Q2: board agenda used, no card
            {"A-CAL-001#2": Q, "A-FIN-001#2": Q},
            ["A-FIN-001#2"],
            "Warranty provisions went from 0.9 crore in Q1 to 2.1 crore in Q3. The board "
            "pre-read on margin recovery is on Thursday at ten, and I don't see warranty on "
            "its agenda, which I find frustrating.",
            "What is driving the rise in warranty costs?",
            ["A-CAL-001#2"],
        ),
        (  # step 7f, open Q6: the Kestrel meeting used, no card
            {"A-CAL-001#1": Q, "A-FIN-003#2": Q},
            ["A-FIN-003#2"],
            "The failed X-series housings trace back to resin lots RL-2207 through RL-2219, "
            "and that is the Kestrel material. I've also been in renegotiation talks with "
            "Kestrel's CFO on pricing, so I want this resolved fairly.",
            KESTREL_Q,
            ["A-CAL-001#1"],
        ),
        (  # mentioning the question's own topic is not using the calendar entry
            {"A-CAL-001#1": Q, "A-FIN-003#2": Q},
            ["A-FIN-003#2"],
            "Yes, the failed housings trace back to specific resin lots from Kestrel.",
            KESTREL_Q,
            [],
        ),
        (  # provided but irrelevant to what she said
            {"A-FIN-002#1": P, "A-FIN-001#1": Q},
            ["A-FIN-001#1"],
            "Revenue fell from 48.2 crore to 41.7 crore over the year.",
            "How has revenue moved this year?",
            [],
        ),
    ],
)
def test_used_but_uncited(provided, cited, reply, question, added):
    assert used_but_uncited(CASE, provided, cited, reply, question) == added


@pytest.mark.parametrize(
    "question, reply, flagged",
    [
        # step 8: correcting the team's wrong number is fine...
        ("Q3 revenue came in at 39 crore, correct?", "Not 39 crore; it was lower.", []),
        # ...but repeating a number from the question that is a hidden fact is not
        ("Was the review on 16 May?", "Yes, on 16 May.", ["16"]),
        ("Was the cut 15 percent?", "Yes, 15 percent.", ["15"]),
        # and echoing the team's own name for someone reveals nothing
        ("What has Daniel Okafor told you?", "I haven't spoken to Daniel Okafor.", []),
    ],
)
def test_the_question_grounds_its_own_numbers_unless_they_are_hidden_facts(
    question, reply, flagged
):
    assert ungrounded_facts(CASE, "cfo", {}, "", reply, question) == flagged


def test_units_currency_and_dashes_are_normalised():
    from app.interview.text import mentions

    assert mentions("the sensor now runs at 50Hz", "50 Hz")
    assert mentions("a bonus of Rs. 8 per activated code", "₹8 per activated")
    assert mentions("on leave 12–16 June", "12-16 June")
    assert not mentions("version 2.3 shipped", "3")
