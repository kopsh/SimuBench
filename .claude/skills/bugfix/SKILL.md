---
name: bugfix
description: Fix a SimuBench bug or a "[建议]" follow-up issue (a claimable issue with a reproduction) — reproduce, regression test, same-place doc/coverage sync, PR. Use when the issue is a defect, not a new model or component.
---

# Fixing a bug or a "[建议]" follow-up

`dev-workflow` has the loop and the checks; this file is the order of work for a defect. A
`[建议]` issue is a review of an earlier PR: it lists evidence, a reproduction and numbered
acceptance criteria. Those criteria are the scope — do them all, and nothing else (`AGENTS.md`).

## Steps

1. **Claim** the issue, then read it for: the reproduction, expected vs actual, the acceptance
   criteria, and the PR or issue it follows up.
2. **Reproduce first.** Turn the reproduction into a failing test *before* touching the code, and
   run it to see it fail for the stated reason (`uv run pytest tests/test_x.py -q`). A bug you
   cannot reproduce: comment on the issue with what you ran; do not guess a fix.
3. **Find the one place.** Use the `codebase-map` skill. Fix the cause where the behaviour is
   declared once, not at each symptom (recent work keeps merging duplicated logic into one
   place). A fix that needs a new capability is not a bug fix: open a `核心` issue (`dev-workflow`).
4. **Pin it with the right test.** Which test file depends on where the bug is:

   | Bug in | Regression test goes in |
   |---|---|
   | one real model's command/reply | `tests/models/<id>/cases.yaml` (with `manual:` naming the section), and `tests/test_<model>.py` when state or error-queue effects need asserting |
   | a generic component or engine rule | a unit test next to the component's existing `tests/test_*.py` |
   | server / API / MCP | the existing API or MCP test module for that endpoint |
   | TUI / web | follow `web-ui`; TUI wording is shared with the web |

   Follow the D49 tiers (`dev-workflow`): unit tests stay ≤100 ms, no server, no Textual. A test
   must fail on the old code — if reverting the fix leaves it green, it does not pin the bug
   (the usual reason a `[建议]` issue exists, e.g. #510).
5. **Edge inputs, same place.** When the bug is a bad input (zero, negative, `None`, a name with a
   special character), also test the siblings the issue names (`WIDT 0` next to `PER 0`), and
   assert that state is unchanged after a rejected command.
6. **Sync what the fix changes**, in the same PR:
   - the model's coverage table `src/simubench/library/instruments/<id>.coverage.yaml` (levels,
     notes — cite the issue when a behaviour has no manual basis);
   - the doc that states the old behaviour (`grep -rn '<keyword>' docs/`); stale "not scheduled"
     or "always 0" wording is a common `[建议]` finding;
   - a skill, if the fix changes something it states (`AGENTS.md`).
   A Pydantic model or API change also regenerates `schemas/` and `web/src/api/generated`
   (`dev-workflow` traps).
7. **Check and PR.** `scripts/check.sh`, then a PR with `Closes #<n>`. The title is the changelog
   entry (D58): what now works for the user (`33622A：周期为 0 或负值回 -222`), not how.
   Add the `免审查` label only if the issue says no review is needed.

## Traps

- A fix with no failing-first test is the most common reason a follow-up issue gets opened.
- Do not widen the fix: a second bug found on the way gets its own issue, not a bigger PR.
- Error codes and reply formats come from the manual (`manuals/`, `docs/real-models.md`), not from
  memory; if the manual is silent, say so in the coverage note rather than inventing a basis.
