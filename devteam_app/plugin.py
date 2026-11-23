"""
Entrypoint referenced by aw-app.json's runtime.entrypoint
("devteam_app.plugin:DevTeamAppPlugin").

Like aw-app-maintenance-agents, this app is deliberately almost empty.
Everything it delivers — the six agents, the config bundle they run under,
the group instructions they share, the two skills that define the Product
Owner and Architect contracts, and the Agents Flow that says how they hand
off — is *declared* in ``aw-app.json`` and seeded by the workspace's own
contribution surfaces (``contributes.agents``, ``contributes.skills``).
There is no HTTP route, no window and no CLI, so ``activate`` has nothing
to register.

Why the app exists at all, then: a team of agents used to be a dozen
unrelated things somebody created by hand across two UIs — an agent row per
member, a config bundle, a group, a skill file each, and then a flow graph
drawn node by node to connect them. Nothing linked them, nothing checked
them, and the failure mode when one was missing was silent: an agent
pointing at a skill slug that doesn't exist reads its own name and nothing
else, and a team with no flow is just a list of agents that never hand off.
Packaging all of it as one installable unit is the point; the Python here
is the hook the framework needs to hang that manifest on.

Seed-once, never updated (see aw-workspace's ``src/apps/agents.py``):
re-installing will not overwrite a system prompt, a flow graph or a model
choice the user has since tuned. Shipping a corrected prompt means a new
slug, or an edit in the UI.
"""

from __future__ import annotations

import asyncio
import logging

from devteam_app.transitions import apply_stub_transition

log = logging.getLogger("aw_apps.devteam")

RETRY_ATTEMPTS = 20
RETRY_DELAY_S = 30


class DevTeamAppPlugin:
    """Tier-1 in-process plugin with no runtime surface of its own."""

    def __init__(self, ctx=None):
        self.ctx = ctx

    async def activate(self, ctx=None) -> None:
        if ctx is not None:
            self.ctx = ctx
        # The contribution registries run from the framework's own activation
        # path, not from here — an app declares, the runtime dispatches. All
        # this needs to do is come up cleanly so that dispatch happens.
        self._retry_task = asyncio.ensure_future(self._transition_loop())
        log.info(
            "aw-app-devteam active — agents, skills and the software-engineering "
            "flow are seeded by the workspace from aw-app.json's contributes block"
        )

    async def _transition_loop(self) -> None:
        """Try now, then keep retrying (bounded) until the agents provider is
        loaded and the transition has been evaluated. Each attempt runs in a
        worker thread: the provider's HTTP client is synchronous."""
        for _ in range(RETRY_ATTEMPTS):
            if await asyncio.to_thread(self._run_transitions):
                return
            await asyncio.sleep(RETRY_DELAY_S)

    def _run_transitions(self) -> bool:
        """Apply fingerprint-scoped upgrade transitions (see transitions.py).

        Never fails activation: a transition that can't run (no agents
        provider loaded yet, platform unreachable) is retried on the next one.
        """
        try:
            from src.apps.agents import AgentsRegistry  # in-process app: core is importable

            provider = AgentsRegistry.find_provider(self.ctx._runtime)
            if provider is None:
                return False
            apply_stub_transition(provider, self.ctx.package_dir)
            return True
        except Exception:  # noqa: BLE001
            log.exception("devteam upgrade transition failed")
            return False

    async def deactivate(self) -> None:
        task = getattr(self, "_retry_task", None)
        if task is not None:
            task.cancel()
        log.info("aw-app-devteam deactivated")
