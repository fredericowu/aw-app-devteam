"""Contract guards for the Project Manager split rule (card 4255bf3b)."""

from __future__ import annotations

from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]


def _skill() -> str:
    return " ".join((APP_DIR / "skills/aw-agent-project-manager/SKILL.md").read_text().split())


def test_skill_has_split_rule_with_canonical_property_and_title_prefix():
    s = _skill()
    sec = s[s.index("## Split rule"):s.index("## Card moves")]
    assert "`SplitCard`" in sec and "[SPLIT] <original title>" in sec
    assert "move_kanban_task(status=\"done\"" in sec and "create_kanban_task" in sec
    # original is closed only after the new card exists and is linked
    assert "only after the new card exists" in sec


def test_split_guardrails():
    s = _skill()
    sec = s[s.index("## Split rule"):s.index("## Card moves")]
    assert "never split under a live run" in sec
    assert "do not split a second time" in sec
    assert "link it" in sec


def test_card_moves_and_park_protocol_carve_out_the_split():
    s = _skill()
    assert "only exception is closing the original card of a split" in s
    assert "except by the Split rule" in s


def test_prompt_states_split_rule():
    p = " ".join((APP_DIR / "prompts/project-manager.md").read_text().split())
    assert "Split rule" in p and "[SPLIT] <original title>" in p and "SplitCard" in p
    assert "The one exception is the split rule" in p
