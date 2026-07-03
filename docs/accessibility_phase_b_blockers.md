# Phase B — Blocker & Discovery List

**Purpose:** any agent working on WCAG remediation who hits something outside their assigned scope — a new accessibility issue not in the original audit, an ambiguous design decision, a conflict with another agent's territory, a finding that changes effort/sequencing — logs it here instead of silently deciding on their own or expanding scope unprompted. The manager (Donavan/orchestrator) triages and reassigns from this list between agent runs.

**Rule for agents:** if you hit something blocking or newly-discovered, add an entry below with your agent name, what you found, why it's outside your scope or blocking, and what you did in the meantime (stopped / worked around / skipped). Do not resolve it yourself unless it's trivially within your own file boundary.

**Rule for the manager:** review this list after each agent completes, before deciding what runs next. Mark entries `[RESOLVED — <how>]` or `[REASSIGNED — <to whom>]` once handled, don't delete them — this becomes the record of what actually happened during Phase B, not just what was planned.

---

## Open

_(none currently blocking — see logged judgement calls below; made conservatively, not blocking further work)_

---

## Resolved / Reassigned

_(none yet)_

---

## Logged judgement calls (Tier 2 — consent modal focus/ARIA, `session_join.html` + `details.html`)

**Agent:** Tier 2 consent-modal focus/ARIA remediation agent (worktree `agent-a6b188a26919673a1`).

These were in-scope decisions the task explicitly asked to be logged rather than guessed silently. None are blocking — implemented with the most conservative option in each case.

1. **Escape key is fully suppressed (not just re-focused into the modal).** These consent dialogs have no cancel/decline affordance anywhere in the UI — the only paths forward are scrolling to the end and clicking "I accept," or leaving the page/tab entirely. There is nowhere meaningful for Escape to send the learner, and letting it close the `<dialog>` would strand them on a page with no way to reopen it except the "Open consent agreement" button, which only exists in the pre-consent render state, not after the dialog is already showing. Rather than build new "decline" UX (out of scope — that's a consent-flow logic change), I `preventDefault()` the dialog's native `cancel` event so Escape is a no-op while the modal is open. If product wants an explicit "I do not consent" exit path, that's a decision for whoever owns `HonestyForm`/the consent flow, not this Tier 2 ticket.

2. **Focus trap implementation is a hand-rolled Tab/Shift-Tab cycle**, not a library (no focus-trap dependency exists in this codebase and adding one felt like scope creep for a 2-dialog fix). It recomputes the focusable-element list on every Tab press (cheap, small DOM) so it stays correct as the Accept button transitions from `disabled` to enabled mid-session. `#consent-scroll` is included in the boundary list even though it carries `tabindex="-1"` (deliberately excluded from normal Tab order) because it's still the modal's initial-focus landing spot and needs to count as a valid trap boundary.

3. **Scroll-gate unlock announcement uses both an `aria-live="polite"` region AND moves focus to the newly-enabled Accept button.** Belt-and-braces: the live region announces the state change for anyone not already focused on the button; the focus move gives keyboard users an immediate, unambiguous "you can act now" without requiring them to go find the button. This double signal was a judgement call — a single mechanism (just the live region, or just the focus move) felt less certain to work reliably across screen reader/browser combinations, and the modal has no other focusable content between the sentinel and the button, so moving focus there doesn't skip anything a learner still needs to read.

No new accessibility issues were discovered outside the stated scope during this work. `question.html`, timer code, and match/long-division question types were not touched.
