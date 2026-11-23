"""Versioned, fingerprint-scoped upgrade transitions for seeded agents.

Why this exists: aw-workspace's reconcile (``src/apps/agents.py::_reconcile``)
only rewrites a field while the live value still equals what the app seeded.
``code-reviewer-pipeline`` had been hand-edited into a temporary approve-only
stub (model ``gpt-6-1-terra``, params ``{max_tokens: 200}``, a stub prompt, no
skills), so the v0.35.0 manifest — the real Terra reviewer — was treated as
"edited by a human, leave it" and never applied.

A transition is NOT a blanket overwrite. It fires only when the live agent
matches a known prior state *exactly* on every discriminating field; any other
hand edit is left alone, exactly as reconcile leaves it. Once the transition
has applied (or the agent already matches the manifest), it is a no-op, so it
is safe to run on every activation.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

log = logging.getLogger("aw_apps.devteam")

AGENT_SLUG = "code-reviewer-pipeline"

#: Fields compared against the manifest to decide what to write.
_TARGET_FIELDS = ("model_slug", "params", "skill_slugs", "system_prompt")

#: Known prior states. ``match`` lists the live values that identify the
#: stub; every key must be equal. Several params shapes are the same stub
#: (before / after the temperature was dropped by hand).
STUB_STATES: tuple[dict[str, Any], ...] = (
    {"model_slug": "gpt-6-1-terra", "params": {"max_tokens": 200}, "skill_slugs": []},
    {"model_slug": "gpt-6-1-terra",
     "params": {"temperature": 0, "max_tokens": 200}, "skill_slugs": []},
)


def _norm(field: str, value: Any) -> Any:
    if field == "skill_slugs":
        return list(value or [])
    if field == "params":
        return dict(value or {})
    return value


#: The original stub prompt text is not recoverable (it was hand-typed into the
#: live agent and never committed), so it cannot be fingerprinted by hash. The
#: stand-in is deliberately narrow: a stub is a short, approve-only blurb. Any
#: prompt that is long, names the reviewer skill, or carries a condition,
#: criterion or review instruction is a real hand edit and is never rewritten.
_STUB_PROMPT_MAX_CHARS = 200
_SKILL_MARKER = "aw-agent-code-reviewer"
_STUB_WORD = "approve"
_NOT_A_STUB_WORDS = re.compile(
    r"\b(if|only|unless|when|whenever|except|but|test|tests|diff|review|check|"
    r"reject|fail|flag|block|regression|regressions|risk|risky|bug|bugs)\b",
    re.IGNORECASE,
)


def _prompt_looks_like_stub(prompt: Any) -> bool:
    text = prompt or ""
    return (
        len(text) <= _STUB_PROMPT_MAX_CHARS
        and _SKILL_MARKER not in text
        and _STUB_WORD in text.lower()
        and not _NOT_A_STUB_WORDS.search(text)
    )


def matches_known_stub(live: dict[str, Any], desired: dict[str, Any]) -> bool:
    """True only for a known stub that is not already the desired state."""
    if not _prompt_looks_like_stub(live.get("system_prompt")):
        return False
    if all(_norm(f, live.get(f)) == _norm(f, desired.get(f)) for f in _TARGET_FIELDS):
        return False
    return any(
        all(_norm(f, live.get(f)) == _norm(f, v) for f, v in state.items())
        for state in STUB_STATES
    )


def desired_agent(package_dir: str) -> dict[str, Any] | None:
    """The manifest's declared ``code-reviewer-pipeline`` entry, prompt inlined."""
    with open(os.path.join(package_dir, "aw-app.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    agents = manifest["contributes"]["agents"]["agents"]
    # Same workspace the sibling reviewer agents declare: a model_slug update
    # on a null-workspace row is rejected (422) on a multi-runner tenant.
    workspace = next((a["workspace"] for a in agents if a.get("workspace")), None)
    for entry in agents:
        if entry.get("slug") != AGENT_SLUG:
            continue
        out = {
            "model_slug": entry.get("model_slug"),
            "params": entry.get("params") or {},
            "skill_slugs": entry.get("skill_slugs") or [],
            "workspace": entry.get("workspace") or workspace,
        }
        prompt_file = entry.get("system_prompt_file")
        if prompt_file:
            with open(os.path.join(package_dir, prompt_file), encoding="utf-8") as fh:
                out["system_prompt"] = fh.read().rstrip()
        else:
            out["system_prompt"] = entry.get("system_prompt")
        return out
    return None


def apply_stub_transition(provider: Any, package_dir: str) -> dict[str, Any] | None:
    """Move a known stub ``code-reviewer-pipeline`` to the manifest's state.

    Returns the changes written (``{}`` for a correct no-op) or ``None`` when
    the live agent could not be read (absent or the platform is unreachable:
    the provider cannot tell which, so the caller must retry). Raises
    ``RuntimeError`` when the provider rejects the write, so the caller keeps
    the transition eligible for a retry.
    """
    read = getattr(provider, "read_contributed_agent", None)
    write = getattr(provider, "update_contributed_agent", None)
    if read is None or write is None:
        return {}
    desired = desired_agent(package_dir)
    if desired is None:
        return {}
    live = read("agents", AGENT_SLUG)
    if not live:
        return None
    if not matches_known_stub(live, desired):
        return {}
    changes = {f: desired[f] for f in _TARGET_FIELDS
               if _norm(f, live.get(f)) != _norm(f, desired.get(f))}
    if not live.get("workspace") and desired.get("workspace"):
        changes["workspace"] = desired["workspace"]
    if write("agents", AGENT_SLUG, changes) is False:
        log.warning("devteam transition: write failed for %s", AGENT_SLUG)
        raise RuntimeError(f"provider rejected the update of {AGENT_SLUG}")
    log.info("devteam transition: moved stub %s to manifest state (%s)",
             AGENT_SLUG, ", ".join(sorted(changes)))
    return changes
