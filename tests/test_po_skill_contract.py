"""Manager v2 parking contract must stay in the PO skill."""
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skills/aw-agent-product-owner/SKILL.md"


def test_po_parking_contract():
    t = " ".join(SKILL.read_text().split())
    for needle in ("ask", "owner", "evidence", "tried", "action",
                   "Leave Manager-parked show-stoppers alone",
                   "24 hours idle", "qa_not_needed=true"):
        assert needle in t, needle
