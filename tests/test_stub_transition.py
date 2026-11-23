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


import pytest


@pytest.fixture
def trust_stub(monkeypatch):
    """Stand-in for trusted evidence so the plugin retry paths can be driven."""
    monkeypatch.setattr(transitions, "TRUSTED_STUB_FINGERPRINTS",
                        frozenset({transitions.fingerprint(STUB)}))


def _approve(prov, tmp_path, by="operator"):
    path = str(tmp_path / "approval.json")
    transitions.write_approval(path, transitions.fingerprint(prov.live), str(APP_DIR), by)
    return path


def _row_snapshot(prov):
    return copy.deepcopy(prov.live)


QA_FIXTURE = {
    "slug": "code-reviewer-pipeline",
    "model_slug": "gpt-6-1-terra",
    "params": {"max_tokens": 200},
    "skill_slugs": [],
    "system_prompt": "Do not approve changes that remove authentication.",
}


def test_qa_old_model_fixture_gets_zero_writes(tmp_path):
    prov = FakeProvider(QA_FIXTURE)
    before = _row_snapshot(prov)
    result = transitions.apply_stub_transition(
        prov, str(APP_DIR), approval_file=str(tmp_path / "none.json"))
    assert result == {}
    assert prov.writes == []
    assert prov.live == before


def test_stub_shaped_rows_are_ambiguous_zero_writes(tmp_path):
    rows = [
        STUB,  # prompt wording alone cannot prove it is the stub
        dict(STUB, params={"temperature": 0, "max_tokens": 200}),
        dict(STUB, model_slug="claude-runner-opus"),
        dict(STUB, params={"max_tokens": 4000}),
        dict(STUB, system_prompt="Review carefully. " * 100),
        dict(STUB, system_prompt="Use skill aw-agent-code-reviewer, my way."),
        dict(STUB, system_prompt="Review for regressions. Flag risky changes."),
        dict(STUB, system_prompt="Review the diff and approve only if all tests pass."),
        dict(STUB, workspace=None),
    ]
    for row in rows:
        prov = FakeProvider(row)
        before = _row_snapshot(prov)
        assert transitions.apply_stub_transition(
            prov, str(APP_DIR), approval_file=str(tmp_path / "none.json")) == {}
        assert prov.writes == [] and prov.live == before


def test_already_converged_reviewer_gets_zero_writes(tmp_path):
    prov = FakeProvider(transitions.desired_agent(str(APP_DIR)))
    assert transitions.apply_stub_transition(
        prov, str(APP_DIR), approval_file=_approve(prov, tmp_path)) == {}
    assert prov.writes == []


def test_trusted_fingerprint_restores_the_whole_row(monkeypatch, tmp_path):
    prov = FakeProvider(STUB)
    monkeypatch.setattr(transitions, "TRUSTED_STUB_FINGERPRINTS",
                        frozenset({transitions.fingerprint(STUB)}))
    desired = transitions.desired_agent(str(APP_DIR))
    changes = transitions.apply_stub_transition(
        prov, str(APP_DIR), approval_file=str(tmp_path / "none.json"))
    assert set(changes) == set(transitions._TARGET_FIELDS)
    for f in transitions._TARGET_FIELDS:
        assert prov.live[f] == desired[f]


def test_no_trusted_fingerprint_ships_today():
    assert transitions.TRUSTED_STUB_FINGERPRINTS == frozenset()


def test_operator_approval_restores_the_complete_manifest_once(tmp_path):
    prov = FakeProvider(QA_FIXTURE)
    approval = _approve(prov, tmp_path)
    desired = transitions.desired_agent(str(APP_DIR))
    changes = transitions.apply_stub_transition(prov, str(APP_DIR), approval_file=approval)
    assert set(changes) == {"model_slug", "params", "skill_slugs", "system_prompt", "workspace"}
    for f in transitions._TARGET_FIELDS:
        assert prov.live[f] == desired[f]
    assert prov.live["model_slug"] == "codex-runner-gpt-6-1-terra"
    assert prov.live["workspace"] == "aw"
    assert transitions.apply_stub_transition(prov, str(APP_DIR), approval_file=approval) == {}
    assert len(prov.writes) == 1


def test_approval_after_drift_or_for_a_different_row_writes_nothing(tmp_path):
    prov = FakeProvider(QA_FIXTURE)
    approval = _approve(prov, tmp_path)
    prov.live["system_prompt"] = "Reject anything that weakens authentication."  # drift
    before = _row_snapshot(prov)
    assert transitions.apply_stub_transition(prov, str(APP_DIR), approval_file=approval) == {}
    assert prov.writes == [] and prov.live == before


def test_incomplete_or_stale_approval_writes_nothing(tmp_path):
    import json

    prov = FakeProvider(QA_FIXTURE)
    path = _approve(prov, tmp_path)
    doc = json.load(open(path))
    for bad in (dict(doc, fields=["model_slug"]), dict(doc, values_fingerprint="0" * 64),
                dict(doc, slug="other"), dict(doc, approved_by="")):
        json.dump(bad, open(path, "w"))
        assert transitions.apply_stub_transition(prov, str(APP_DIR), approval_file=path) == {}
    open(path, "w").write("not json")
    assert transitions.apply_stub_transition(prov, str(APP_DIR), approval_file=path) == {}
    assert prov.writes == []


def test_ambiguous_report_never_contains_prompt_text(tmp_path, caplog):
    import logging

    secret = "Do not approve changes that remove authentication."
    prov = FakeProvider(dict(QA_FIXTURE, system_prompt=secret))
    with caplog.at_level(logging.WARNING, logger="aw_apps.devteam"):
        transitions.apply_stub_transition(
            prov, str(APP_DIR), approval_file=str(tmp_path / "none.json"))
    assert caplog.records and secret not in caplog.text
    assert transitions.fingerprint(prov.live) in caplog.text


def test_missing_agent_is_a_noop():
    assert transitions.apply_stub_transition(FakeProvider(None), str(APP_DIR)) is None


def test_plugin_activation_runs_the_transition(monkeypatch, trust_stub):
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


def test_transition_retries_until_the_provider_appears(monkeypatch, trust_stub):
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


def test_plugin_imports_as_a_package_member_not_by_absolute_name():
    # Core loads the plugin from the installed app dir; an absolute
    # `from devteam_app...` import broke activation in v0.36.0.
    src = (APP_DIR / "devteam_app" / "plugin.py").read_text()
    assert "from devteam_app" not in src and "import devteam_app" not in src


def test_rejected_write_raises_and_the_plugin_retries(monkeypatch, trust_stub):
    import asyncio
    import pytest

    class Rejecting(FakeProvider):
        def __init__(self, live):
            super().__init__(live)
            self.fail = True

        def update_contributed_agent(self, kind, slug, changes):
            if self.fail:
                self.fail = False
                return False
            return super().update_contributed_agent(kind, slug, changes)

    with pytest.raises(RuntimeError):
        transitions.apply_stub_transition(Rejecting(STUB), str(APP_DIR))
    assert Rejecting(STUB).fail  # still eligible

    import types

    prov = Rejecting(STUB)
    attempts = {"n": 0}

    class Registry:
        @staticmethod
        def find_provider(runtime):
            attempts["n"] += 1
            return prov

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
    assert attempts["n"] == 2
    assert prov.live["model_slug"] == "codex-runner-gpt-6-1-terra"


def test_unreadable_agent_after_a_rejected_write_keeps_retrying(monkeypatch, trust_stub):
    import asyncio
    import types

    class Flaky(FakeProvider):
        def __init__(self, live):
            super().__init__(live)
            self.reads = 0

        def read_contributed_agent(self, kind, slug):
            self.reads += 1
            if self.reads == 2:  # retry GET after the rejected PUT: 503 -> None
                return None
            return super().read_contributed_agent(kind, slug)

        def update_contributed_agent(self, kind, slug, changes):
            if not self.writes:
                self.writes.append(changes)
                return False
            return super().update_contributed_agent(kind, slug, changes)

    prov = Flaky(STUB)

    class Registry:
        @staticmethod
        def find_provider(runtime):
            return prov

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
    assert prov.live["model_slug"] == "codex-runner-gpt-6-1-terra"
