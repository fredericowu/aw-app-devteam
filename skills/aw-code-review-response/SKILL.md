---
name: aw-code-review-response
description: How a Coder acts on a reviewed PR - judge every review finding against the card's instructions, reply on each one (handled here / handled by another card / won't do), then push or merge per the v2 gate mechanics, or escalate to the manager. Use whenever a wake prompt says "load the aw-code-review-response skill and act on PR <url>", or any time you are about to act on review findings.
---

# aw-code-review-response - acting on a reviewed PR

The wake prompt that sent you here is short on purpose: a PR url plus the
PR-specific facts (wake n of max, stored findings, the reason). **Every rule
lives in this skill, not in the prompt.** If a prompt and this skill seem to
disagree, this skill wins.

## 1. You decide, the reviewer advises

You hold the card's instructions; the reviewer does not. Findings are
**input**, not an obligation to satisfy the reviewer.

- You do NOT need the reviewer to agree with you.
- You CAN merge, when the mechanics in section 4 allow it.
- Fix what is right to fix, defer what belongs elsewhere, decline what is
  wrong or out of scope. Do not harden or refactor beyond what a finding
  names; low-severity or speculative findings are usually a "won't do".

Check the PR's live state first (it may already be merged, closed, or have
new findings): `gh pr view <n> --json state,mergeStateStatus,headRefOid,labels`,
`gh pr checks <n>`, and the review comments.

## 2. Reply on EVERY finding

For every review comment or finding (stored findings in the prompt AND the
inline comments on the PR), post a reply **on that comment, in the same PR**,
with exactly one of:

| Option | Reply must say |
|---|---|
| (a) **Handled in this PR** | how, and the commit sha |
| (b) **Handled by another card** | the card link; create the card first if none exists |
| (c) **Won't do** | the reason |

One reply per finding, no silent skips, no batch "addressed all". The point is
that the thought process is visible to the next reader. Reply to inline review
comments with `gh api repos/<owner>/<repo>/pulls/<n>/comments/<comment_id>/replies -f body=...`;
a finding that only exists in a review body or the stored digest gets a PR
comment that quotes it.

## 3. Then move the PR

- **Pushed a fix:** a push retriggers the review automatically. Do NOT post
  `/aw-review`. Do NOT touch labels (`devteam:*` and `aw-run:*` are gate-owned).
- **Fixed nothing (every finding declined or deferred):** there is nothing to
  push, so see section 4 before you do anything.
- **Merge** when the rules allow it (section 4): `gh pr merge <n> --squash
  --match-head-commit <head_sha>`. Never an admin override or bypass. If GitHub
  refuses, report the refusal on the card instead of forcing it.
- After merge: sync the tree, hand to QA per the PR-first flow, close the card.

## 4. Mechanics of the v2 gate (verified against aw-shared-workflows `code-review-reusable.yml`, `lifecycle: v2`)

- Review 1 is the only review that can block. A finding at or above
  `block_severity` (default `medium`) turns `code-review` red and withholds
  `devteam/merge-eligible` on that head.
- After review 1 has completed, any later `pull_request` event on a NEW head
  sha takes the `bypass` round: the gate writes `devteam/code-review` success
  and `devteam/merge-eligible` success on that sha and the PR merges once CI
  is green. No second review. `/aw-review` after review 1 is informational only
  and never grants eligibility.
- **The reviewed head itself is never cleared.** The bypass refuses any sha that
  already carries the round-1 `[rk r=1 ...]` status (the `n != 0` check in the
  Decide step), on purpose, so a replay cannot skip a blocked review.
  `ready_for_review` / close-reopen events on the same sha do not help.
- So declining every finding without a new sha leaves the head red from review 1
  and **no path merges it**. There is no reviewer-free bypass for the same sha,
  and do not invent one (no admin override, no label games, no ruleset edits).
- **The path that exists is a new head sha, without an empty commit.** Pick the
  first that applies:
  1. Behind master: `gh pr update-branch <n> --rebase` (or rebase locally and
     `git push --force-with-lease`). This is real branch hygiene.
  2. Otherwise amend the head commit's message to record the decline rationale
     ("Review findings: <short list of won't-do/deferred with reasons or card
     links>") and `git push --force-with-lease`. It is a real, useful change to
     history, not an artificial commit.
  The push is a `synchronize` after a completed review 1, so the bypass clears
  the new head; merge when CI is green. Reply to every finding (section 2)
  BEFORE this push, so the record is complete when the gate re-runs.
- Review cap: after `max_reviews` (default 3) no push or `/aw-review` runs another
  review. Stop pushing and report; a human decides.

## 5. Escalation

If you cannot move the PR (CI red for a reason outside the diff, GitHub refuses,
the findings need a decision you do not own): report on the card, then escalate
to `manager` with a Mode B PR-shaped work order: `pr`, `url`, `reason`,
`evidence`. Name which findings you replied to and how. Do not go silent.
