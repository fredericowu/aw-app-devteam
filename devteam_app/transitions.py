"""Upgrade transitions for the seeded ``code-reviewer-pipeline`` agent.

Why this exists: aw-workspace's reconcile (``src/apps/agents.py::_reconcile``)
only rewrites a field while the live value still equals what the app seeded.
``code-reviewer-pipeline`` had been hand-edited into a temporary approve-only
stub, so the manifest's real Terra reviewer was treated as "edited by a human,
leave it" and never applied.

Safety rule (Product Owner decision, card 4215bf3b): the original stub prompt
is unrecoverable, so model / params / skills / prompt wording can NOT prove a
row is the stub, and a valid hand-written reviewer instruction must never be
erased by an upgrade. Therefore:

* Automatic restore happens only when the live row's full-state fingerprint
  is listed in ``TRUSTED_STUB_FINGERPRINTS`` (trusted evidence of the exact
  temporary stub). No such evidence exists today, so the list is empty.
* Every other row that differs from the manifest is ambiguous: ZERO writes
  across the whole row (model, params, skills, prompt, workspace). It is
  reported (fingerprint and differing field names only, never prompt text).
* An authorized operator may approve the specific row. The approval names the
  slug, the live-row fingerprint it was reviewed against, and the exact
  manifest values to write (all fields, prompt included). Any drift in the live
  row or the manifest voids it; a changed row needs a fresh approval.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from typing import Any

log = logging.getLogger("aw_apps.devteam")

AGENT_SLUG = "code-reviewer-pipeline"
APP_DATA_DIR = "aw-app-devteam"
APPROVAL_FILE = "restore-approval.json"

#: Fields compared against the manifest. ``workspace`` is part of the row.
_TARGET_FIELDS = ("model_slug", "params", "skill_slugs", "system_prompt", "workspace")

#: sha256 fingerprints (see :func:`fingerprint`) of rows positively known to be
#: the temporary stub. Empty: the original stub prompt was never committed, so
#: nothing can be fingerprinted. Add an entry only with trusted evidence.
TRUSTED_STUB_FINGERPRINTS: frozenset[str] = frozenset()


def _norm(field: str, value: Any) -> Any:
    if field == "skill_slugs":
        return list(value or [])
    if field == "params":
        return dict(value or {})
    if field == "workspace":
        return value or None
    return value


def fingerprint(row: dict[str, Any], fields: tuple[str, ...] = _TARGET_FIELDS) -> str:
    """Stable digest of a row's target fields; reveals no prompt text."""
    blob = json.dumps({f: _norm(f, row.get(f)) for f in fields},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def differing_fields(live: dict[str, Any], desired: dict[str, Any]) -> list[str]:
    return [f for f in _TARGET_FIELDS if _norm(f, live.get(f)) != _norm(f, desired.get(f))]


def approval_path() -> str:
    home = os.environ.get("AW_WORKSPACE_HOME") or os.path.join(
        os.environ.get("AW_WORKSPACE_CONTAINER_DIR", "/opt/aw-workspace"), ".aw-workspace")
    return os.path.join(home, "data", APP_DATA_DIR, APPROVAL_FILE)


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


def write_approval(path: str, live_fingerprint: str, package_dir: str,
                   approved_by: str) -> dict[str, Any]:
    """Record an operator approval for restoring the full manifest state."""
    desired = desired_agent(package_dir)
    if desired is None:
        raise ValueError("manifest does not declare " + AGENT_SLUG)
    doc = {
        "slug": AGENT_SLUG,
        "approved_by": approved_by,
        "live_fingerprint": live_fingerprint,
        "fields": list(_TARGET_FIELDS),
        "values_fingerprint": fingerprint(desired),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    return doc


def _approval_problem(approval: Any, live: dict[str, Any], desired: dict[str, Any]) -> str | None:
    """Why ``approval`` does not authorize writing ``desired`` over ``live``."""
    if not isinstance(approval, dict) or approval.get("slug") != AGENT_SLUG:
        return "approval does not name " + AGENT_SLUG
    if not approval.get("approved_by"):
        return "approval has no approver"
    fields = approval.get("fields")
    if (not isinstance(fields, list) or not all(isinstance(f, str) for f in fields)
            or sorted(fields) != sorted(_TARGET_FIELDS)):
        return "approval does not cover every field"
    if approval.get("values_fingerprint") != fingerprint(desired):
        return "manifest values changed since approval"
    if approval.get("live_fingerprint") != fingerprint(live):
        return "live row changed since approval"
    return None


def _load_approval(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {}


def apply_stub_transition(provider: Any, package_dir: str,
                          approval_file: str | None = None) -> dict[str, Any] | None:
    """Move ``code-reviewer-pipeline`` to the manifest state, only if safe.

    Returns the changes written (``{}`` for a no-op, including every skipped
    ambiguous row) or ``None`` when the live agent could not be read, so the
    caller retries. Raises ``RuntimeError`` when the provider rejects a write
    so the caller keeps the transition eligible for a retry. Writes are always
    the complete manifest state for the row; never a partial update.
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
    differing = differing_fields(live, desired)
    if not differing:
        return {}
    live_fp = fingerprint(live)
    approval_used = None
    if live_fp in TRUSTED_STUB_FINGERPRINTS:
        reason = "trusted stub fingerprint"
    else:
        path = approval_file or approval_path()
        approval = _load_approval(path)
        if approval is None:
            log.warning(
                "devteam transition: %s differs from the manifest (%s) and is not a "
                "trusted stub; left untouched. live_fingerprint=%s. To restore, an "
                "operator must approve it (see docs/stub-restore.md).",
                AGENT_SLUG, ", ".join(differing), live_fp)
            return {}
        problem = _approval_problem(approval, live, desired)
        if problem:
            log.warning("devteam transition: %s left untouched, approval not "
                        "applicable: %s (live_fingerprint=%s)", AGENT_SLUG, problem, live_fp)
            return {}
        approval_used = path
        reason = "operator approval by " + str(approval["approved_by"])
    changes = {f: desired[f] for f in _TARGET_FIELDS}
    # The platform PUT is unconditional, so re-read right before writing and
    # require the same row that was approved (narrows the race to one request).
    recheck = read("agents", AGENT_SLUG)
    if not recheck:
        return None
    if fingerprint(recheck) != live_fp:
        log.warning("devteam transition: %s changed while restoring; no write, "
                    "fresh review needed", AGENT_SLUG)
        return {}
    if write("agents", AGENT_SLUG, changes) is False:
        log.warning("devteam transition: write failed for %s", AGENT_SLUG)
        raise RuntimeError(f"provider rejected the update of {AGENT_SLUG}")
    if approval_used:
        try:  # one approval authorizes one restore; a rejected write keeps it
            os.replace(approval_used, approval_used + ".applied")
        except OSError:
            log.warning("devteam transition: could not consume the approval file")
    log.info("devteam transition: restored %s to manifest state (%s; fields: %s)",
             AGENT_SLUG, reason, ", ".join(differing))
    return changes


if __name__ == "__main__":  # operator helper: record an approval, no provider needed
    import argparse

    ap = argparse.ArgumentParser(description="Approve restoring the reviewer to the manifest.")
    ap.add_argument("--live-fingerprint", required=True,
                    help="live_fingerprint from the activation log warning")
    ap.add_argument("--approved-by", required=True)
    ap.add_argument("--package-dir", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--approval-file", default=None)
    a = ap.parse_args()
    out = write_approval(a.approval_file or approval_path(), a.live_fingerprint,
                         a.package_dir, a.approved_by)
    print(json.dumps(out, indent=2))
    sys.exit(0)
