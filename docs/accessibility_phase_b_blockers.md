# Phase B — Blocker & Discovery List

**Purpose:** any agent working on WCAG remediation who hits something outside their assigned scope — a new accessibility issue not in the original audit, an ambiguous design decision, a conflict with another agent's territory, a finding that changes effort/sequencing — logs it here instead of silently deciding on their own or expanding scope unprompted. The manager (Donavan/orchestrator) triages and reassigns from this list between agent runs.

**Rule for agents:** if you hit something blocking or newly-discovered, add an entry below with your agent name, what you found, why it's outside your scope or blocking, and what you did in the meantime (stopped / worked around / skipped). Do not resolve it yourself unless it's trivially within your own file boundary.

**Rule for the manager:** review this list after each agent completes, before deciding what runs next. Mark entries `[RESOLVED — <how>]` or `[REASSIGNED — <to whom>]` once handled, don't delete them — this becomes the record of what actually happened during Phase B, not just what was planned.

---

## Open

### [Manager — during review] `_attempt_expires_at()` vs `_expire_overdue_attempts()` use different duration sources
**Found while verifying:** Agent 2's extended-time accommodation work.
**What:** `_attempt_expires_at()` (learner.py) computes the overall-attempt deadline from the hardcoded `ASSESSMENT_DURATION` constant (2 hours). `_expire_overdue_attempts()` (_common.py) computes it from `_template_total_duration(template)` — the real, summed duration of that specific template's sections. For the real production templates these currently agree (60+60min sections = 2hrs), so it's never surfaced. For a template with a different total (e.g. a single 60-min section, as used in the new accommodation tests), they diverge — the constant-based check doesn't fire when the template-duration-based one would.
**Why it's a blocker, not something to silently fix:** this predates today's work entirely — not introduced by any Phase B agent. Reconciling it (making `_attempt_expires_at` use `_template_total_duration` instead of the constant) is a real behavior change to core timing logic, not an accessibility fix, and deserves a deliberate decision rather than a silent patch bundled into this remediation.
**What was done in the meantime:** the two affected tests in `test_extended_time_accommodation.py` were rewritten to exercise section-level expiry (`_section_expires_at`, which correctly uses each section's real duration and is not affected by this inconsistency) instead of overall-attempt expiry, so Agent 2's actual scope (multiplier threading) is still fully verified. The underlying constant-vs-template-duration inconsistency itself is untouched.

---

## Resolved / Reassigned

_(none yet)_
