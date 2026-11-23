# Restoring `code-reviewer-pipeline` after the approve-only stub

Upgrading never rewrites a reviewer row on a guess. The original stub prompt
was never committed, so model, params, skills or prompt wording cannot prove a
row is the stub, and a valid hand-written reviewer instruction must not be
erased. On activation (`devteam_app/transitions.py`):

| Live row | Result |
|---|---|
| Equals the manifest | no write |
| Fingerprint in `TRUSTED_STUB_FINGERPRINTS` (empty today) | whole row restored |
| Differs from the manifest, no approval | **zero writes** to every field (model, params, skills, prompt, workspace); a warning logs the row fingerprint and the differing field names, never prompt text |
| Differs, operator approval matches | whole manifest row written (all five fields, prompt included) |
| Approval exists but the row or the manifest changed since | zero writes; a fresh approval is needed |

A rejected write (HTTP 422, etc.) leaves the row untouched and is retried by
the plugin's loop and on the next activation while the approval still matches.

## Operator steps (ambiguous row)

1. Find the warning `devteam transition: code-reviewer-pipeline differs from the
   manifest (...) live_fingerprint=<sha256>` in the workspace log.
2. Review the live reviewer yourself (agent page) and decide that replacing
   model, params, skills, **prompt** and workspace with the manifest values is
   acceptable. Copy the prompt first if you want to keep it.
3. Record the approval for exactly that row:
   `python3 -I devteam_app/transitions.py --live-fingerprint <sha256> --approved-by <you>`
   (run from the installed app dir; writes
   `$AW_WORKSPACE_HOME/data/aw-app-devteam/restore-approval.json`).
4. Re-activate the app (or restart). The row is restored once and the approval file is renamed `.applied` (one approval, one restore); later
   activations are zero-write.

Editing the row after step 3, or upgrading to a manifest with different
reviewer values, voids the approval. Delete the approval file to withdraw it.
Manual alternative: update the agent by hand with the manifest values.
