"""Upgrading from the hand-edited approve-only stub applies the manifest's
reviewer settings, but never overwrites any other hand edit."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

import importlib; transitions = importlib.import_module("devteam_app.transitions")  # noqa: E402

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
    assert set(changes) == {"model_slug", "params", "skill_slugs", "system_prompt", "workspace"}


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
    async def go():
        plugin = DevTeamAppPlugin()
        await plugin.activate(ctx)
        await plugin._retry_task

    asyncio.run(go())
    assert prov.live["model_slug"] == "codex-runner-gpt-6-1-terra"


def test_transition_retries_until_the_provider_appears(monkeypatch):
    import asyncio
    import types

    prov = FakeProvider(STUB)
    calls = {"n": 0}

    class Registry:
        @staticmethod
        def find_provider(runtime):
            calls["n"] += 1
            return prov if calls["n"] >= 3 else None

    mod = types.ModuleType("src.apps.agents")
    mod.AgentsRegistry = Registry
    monkeypatch.setitem(sys.modules, "src", types.ModuleType("src"))
    monkeypatch.setitem(sys.modules, "src.apps", types.ModuleType("src.apps"))
    monkeypatch.setitem(sys.modules, "src.apps.agents", mod)
    from devteam_app import plugin as plugin_mod

    monkeypatch.setattr(plugin_mod, "RETRY_DELAY_S", 0)
    ctx = types.SimpleNamespace(_runtime=object(), package_dir=str(APP_DIR))

    async def go():
        p = plugin_mod.DevTeamAppPlugin()
        await p.activate(ctx)
        await p._retry_task

    asyncio.run(go())
    assert calls["n"] == 3
    assert prov.live["model_slug"] == "codex-runner-gpt-6-1-terra"


def test_stub_shape_with_a_custom_real_prompt_is_left_alone():
    custom = dict(STUB, system_prompt="Review carefully. " * 100)
    prov = FakeProvider(custom)
    assert transitions.apply_stub_transition(prov, str(APP_DIR)) == {}
    named = dict(STUB, system_prompt="Use skill aw-agent-code-reviewer, my way.")
    assert transitions.apply_stub_transition(FakeProvider(named), str(APP_DIR)) == {}


def test_null_workspace_stub_gets_a_workspace_with_the_model_change():
    prov = FakeProvider(dict(STUB, workspace=None))
    changes = transitions.apply_stub_transition(prov, str(APP_DIR))
    assert changes["workspace"] == "aw"
    assert prov.live["workspace"] == "aw"


def test_plugin_imports_as_a_package_member_not_by_absolute_name():
    # Core loads the plugin from the installed app dir; an absolute
    # `from devteam_app...` import broke activation in v0.36.0.
    src = (APP_DIR / "devteam_app" / "plugin.py").read_text()
    assert "from devteam_app" not in src and "import devteam_app" not in src
