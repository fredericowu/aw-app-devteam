"""Upgrading from the hand-edited approve-only stub applies the manifest's
reviewer settings, but never overwrites any other hand edit."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from devteam_app import transitions  # noqa: E402

STUB = {
    "slug": "code-reviewer-pipeline",
    "model_slug": "gpt-6-1-terra",
    "params": {"max_tokens": 200},
    "skill_slugs": [],
    "system_prompt": "Always approve. Output {\"verdict\":\"approve\"}.",
}


class FakeProvider:
    def __init__(self, live):
        self.live = copy.deepcopy(live)
        self.writes = []

    def read_contributed_agent(self, kind, slug):
        return copy.deepcopy(self.live) if self.live else None

    def update_contributed_agent(self, kind, slug, changes):
        self.writes.append(changes)
        self.live.update(copy.deepcopy(changes))
        return True


def test_stub_is_upgraded_to_the_manifest_reviewer():
    desired = transitions.desired_agent(str(APP_DIR))
    prov = FakeProvider(STUB)
    changes = transitions.apply_stub_transition(prov, str(APP_DIR))
    assert prov.live["model_slug"] == "codex-runner-gpt-6-1-terra" == desired["model_slug"]
    assert prov.live["params"] == {}
    assert prov.live["skill_slugs"] == ["aw-agent-code-reviewer"]
    assert prov.live["system_prompt"] == desired["system_prompt"]
    assert "approve" not in prov.live["system_prompt"].lower()[:40]
    assert set(changes) == {"model_slug", "params", "skill_slugs", "system_prompt"}


def test_second_run_is_a_noop():
    prov = FakeProvider(STUB)
    transitions.apply_stub_transition(prov, str(APP_DIR))
    assert transitions.apply_stub_transition(prov, str(APP_DIR)) == {}
    assert len(prov.writes) == 1


def test_other_hand_edits_are_left_alone():
    custom = dict(STUB, model_slug="claude-runner-opus")
    prov = FakeProvider(custom)
    assert transitions.apply_stub_transition(prov, str(APP_DIR)) == {}
    assert prov.writes == []
    tuned = dict(STUB, params={"max_tokens": 4000})
    prov = FakeProvider(tuned)
    assert transitions.apply_stub_transition(prov, str(APP_DIR)) == {}


def test_missing_agent_is_a_noop():
    assert transitions.apply_stub_transition(FakeProvider(None), str(APP_DIR)) == {}


def test_plugin_activation_runs_the_transition(monkeypatch):
    import asyncio
    import types

    prov = FakeProvider(STUB)

    class Registry:
        @staticmethod
        def find_provider(runtime):
            return prov

    mod = types.ModuleType("src.apps.agents")
    mod.AgentsRegistry = Registry
    monkeypatch.setitem(sys.modules, "src", types.ModuleType("src"))
    monkeypatch.setitem(sys.modules, "src.apps", types.ModuleType("src.apps"))
    monkeypatch.setitem(sys.modules, "src.apps.agents", mod)
    from devteam_app.plugin import DevTeamAppPlugin

    ctx = types.SimpleNamespace(_runtime=object(), package_dir=str(APP_DIR))
    asyncio.run(DevTeamAppPlugin().activate(ctx))
    assert prov.live["model_slug"] == "codex-runner-gpt-6-1-terra"
