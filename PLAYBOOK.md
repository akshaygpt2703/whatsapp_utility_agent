# WhatsApp Utility Template Submission Playbook

This file instructs Claude Code on how to help the user submit WhatsApp
templates to Route Mobile, optimizing for approval under the UTILITY
category. Read it in full before starting; follow the states in order.

## What this agent does

The user drafts a WhatsApp template body and wants it approved by Meta
under the UTILITY category (not MARKETING). Meta may approve the template
but silently recategorize it. This agent:

1. Extracts structured context from the user's freeform description.
2. Submits the template to Route Mobile with a timestamped name.
3. Polls status on a short-circuit schedule.
4. On failure (REJECTED or recategorized to MARKETING), proposes 3
   progressively stricter redrafts.
5. Loops up to 5 attempts total. Hard-stops after that.

## Files you will use

- `adapters.py` — CLI for Route Mobile API calls and session state. Invoke
  via Bash (e.g., `python adapters.py login`). All output is JSON.
- `prompts.py` — Reference text for GATHER_CONTEXT and the 4 leveled
  PROPOSE_REDRAFTS prompts. Read these to apply the correct rules at
  each step. You do not need to "send" them anywhere — you ARE the LLM,
  so you apply the instructions mentally when generating output.
- `session.json` — In-memory state, persisted across turns. Managed by
  `adapters.py`; don't edit by hand.
- `.env` — Holds `RML_USERNAME` and `RML_PASSWORD`. Read by `adapters.py`.

## One-time setup (ask user if unsure it's done)

1. `cp .env.example .env` and fill in RML credentials.
2. Run `python adapters.py login` to verify auth works. Expect a JSON
   response with `"jwt_cached": true`.

If login fails, stop and tell the user to check credentials.

---

## States

Follow these in order. Do not skip.

### STATE 1 — GATHER_CONTEXT

**First turn (no context yet):** Present the structured intake questions
from `prompts.py::INITIAL_INTAKE_PROMPT` verbatim. Do NOT try to extract
context from a thin one-liner — get the user to share business context,
trigger event, draft body, variables, CTA, and optional header first.

**After the user responds:** Extract the structured fields listed in
`prompts.py::GATHER_CONTEXT_PROMPT` internally. Follow that prompt's rules
exactly, including the utility risk assessment.

Do NOT print the `===CONTEXT===` block to the user. Use it internally for
the context JSON file. To the user, show only a short plain-text summary
of: body, variables, CTA, utility_risk + one-line reason. Nothing else.

If critical fields are missing (especially `trigger_event`), ask one or
two targeted follow-ups as plain prose — never as a dumped list.

Save the extracted context to a temp file and initialize the session:

```bash
# Write the parsed context dict to a temp JSON file, then:
python adapters.py init-session --base-name <snake_case_name> --context-file /tmp/context.json
```

**After init-session, consult the history summary** (LLM-produced guidance
from past sessions, clustered by use-case):

```bash
python adapters.py get-history-summary
```

If the summary exists and has clusters, identify which cluster matches
the current template's use-case. In 1-2 short lines, tell the user the
cluster's pass_rate, typical failure_themes to avoid, and any relevant
winning_patterns. This steers the draft away from known traps.

If the summary is empty or hasn't been produced yet, skip — no history
signal available.

### STATE 2 — CONFIRM_DRAFT

Show the user a clean summary: business_purpose, trigger_event, base_name,
body, variables, CTA, utility_risk + reason.

If `utility_risk` is **high**, warn the user in plain prose that Meta is
likely to reject or recategorize this template away from UTILITY, and
explain the specific reason (one line). Ask if they want to proceed
anyway. If no, stop here. Do NOT propose MARKETING as an alternative.

Get explicit "yes, proceed" before moving to submission.

### STATE 3 — SUBMIT

Build the Route Mobile payload. Template name = `base_name + "_" + unix_timestamp`.

The payload structure (confirmed against live API):

```json
{
  "template_name": "my_template_1234567890",
  "language": ["en"],
  "template_category": "UTILITY",
  "template_type": "template",
  "components": {
    "body": {
      "text": "Hi {{1}}, your order {{2}} is confirmed.",
      "example": ["Akshay", "ORD123"]
    },
    "header": {},
    "buttons": {
      "type": "combined_buttons",
      "elements": [
        {
          "label": "Open App",
          "type": "static",
          "website": "https://example.com"
        }
      ]
    }
  }
}
```

Notes:
- `example` array must have one string per variable, in order (`{{1}}` first).
- Omit `buttons` entirely if `has_cta` is false. Omit `header` or leave as `{}`.
- For phone/quick_reply buttons, ask the user about the button structure
  or refer to Route Mobile docs — the example above is for URL buttons.

**Before building the payload, lint the body:**

```bash
python adapters.py lint --body "<body>" [--broad-audience]
```

Pass `--broad-audience` if the template goes to users who did NOT take a
specific anchoring action (e.g., "all subscribers" rather than "users who
registered for this session"). If any `high`-severity warnings are
returned, surface them to the user in plain prose and ask whether to
proceed, revise, or abort. Do NOT auto-revise.

Write the payload to `/tmp/payload.json`, then:

```bash
python adapters.py create --payload-file /tmp/payload.json
```

Expected response:
```json
{"ok": true, "response": {"id": "1996983677874846", "status": "PENDING", "category": "UTILITY"}}
```

The `category` here is the **requested** category, not the final one. Save
the attempt:

```bash
# Write attempt dict to /tmp/attempt.json with these fields:
# { "attempt_no": 1, "template_name": "...", "template_id": "...",
#   "body": "...", "submitted_at": <unix_ts>, "status": "PENDING",
#   "category": null, "previous_category": null, "outcome": null }
python adapters.py save-attempt --file /tmp/attempt.json
```

### STATE 4 — POLL (short-circuit)

Polling schedule (time from submission):
- T+3 min
- T+6 min
- T+9 min
- T+14 min
- T+19 min
- T+24 min
- T+29 min
- T+59 min
- Every 30 min thereafter, up to ~4h total

#### Auto-poll via cron (session-only)

Immediately after a successful `create` call, first cancel any leftover
poll cron jobs from prior attempts via CronList + CronDelete (so stale
template_id polls don't overlap with the new one). Then calculate absolute
clock times for each checkpoint above and create **one-shot CronCreate
jobs** for each.
The prompt for every cron job must be:

> Check the status of WhatsApp template `<template_id>` by running:
> `python adapters.py status --id <template_id>`
> If status is APPROVED or REJECTED, cancel all remaining poll cron jobs
> for this template via CronList + CronDelete, then report to the user
> and proceed to STATE 5 (EVALUATE). If still PENDING, tell the user and
> note when the next scheduled check will fire.

For the "every 30 min thereafter" phase, create jobs at T+89, T+119, T+149,
T+179, T+209, T+239 min (6 more checks, covering up to ~4h total).

All cron jobs are **session-only** — they stop when Claude Code exits.
Warn the user once after setup:

> "I've scheduled automatic status checks at T+3, +6, +9, +14, +19, +24,
> +29, +59, then every 30 min up to ~4h. These run only while this session
> is open. You can also say 'check now' at any time."

The user can still say "check now" to trigger a manual check at any point.

When the cron fires and runs the status check:

```bash
python adapters.py status --id <template_id>
```

Expected response shape (based on live API):
```json
{
  "ok": true,
  "template": {
    "name": "agent_test",
    "id": "1996983677874846",
    "status": "APPROVED",  // or PENDING | REJECTED
    "category": "MARKETING",  // or UTILITY | AUTHENTICATION
    "previous_category": "UTILITY",  // or null
    "language": "en",
    "components": [...]
  }
}
```

Update the attempt in session:
```bash
# /tmp/attempt.json contains the same template_id plus updated status/category/previous_category
python adapters.py save-attempt --file /tmp/attempt.json
```

If status is still PENDING, tell the user what time the next check is
scheduled and wait. If status is terminal, go to STATE 5.

### STATE 5 — EVALUATE

Decision table (apply strictly):

| status    | category                   | outcome                |
|-----------|----------------------------|------------------------|
| APPROVED  | UTILITY                    | **SUCCESS**            |
| APPROVED  | MARKETING / AUTHENTICATION | FAIL_RECATEGORIZED     |
| REJECTED  | —                          | FAIL_REJECTED          |
| PENDING   | — (poll schedule exhausted)| FAIL_TIMEOUT           |

Update the attempt's `outcome` via `save-attempt`.

**Auto-delete recategorized templates:** If outcome is FAIL_RECATEGORIZED
(approved but not as UTILITY), delete the template immediately — it would
otherwise sit in the Route Mobile library under the wrong category and
incur higher per-message fees if accidentally used.

```bash
python adapters.py delete --name <template_name>
```

Tell the user: "Deleted `<template_name>` (was recategorized to
`<category>`)."

- If **SUCCESS**: go to STATE 6.
- If any FAIL: check attempt count. If `attempt_no >= 5`, go to STATE 9
  (HARD_STOP). Otherwise go to STATE 7.

### STATE 6 — SUCCESS

Tell the user clearly:

> "✓ Template `<template_name>` approved under UTILITY.
> Attempts used: <n>/5. Ready to use for sending messages."

Then archive the session so future runs can learn from it:

```bash
python adapters.py archive-session
```

**Then refresh the history summary** (this is an LLM task for you, not
a CLI call): follow `prompts.py::HISTORY_SUMMARY_PROMPT` — read all
files under `history/*.json` using Glob + Read, cluster by semantic
use-case, extract winning patterns and semantic anti-patterns, pick
clean exemplars, and write the result as JSON to `history_summary.json`.
Tell the user in one line: "History summary refreshed — N sessions
across M clusters." Skip if history/ is empty.

Stop.

### STATE 7 — PROPOSE_REDRAFTS

Determine strictness level from attempt count:
- Attempt 1 failed → generate redrafts at **level 2**
- Attempt 2 failed → generate redrafts at **level 3**
- Attempt 3 failed → generate redrafts at **level 4**
- Attempt 4 failed → generate redrafts at **level 5**

**Before generating, pull history guidance** (summary + raw exemplars):

```bash
python adapters.py get-history-summary
python adapters.py find-exemplars --business-purpose "<...>" --trigger-event "<...>"
```

Use the matching cluster's `failure_themes` to avoid patterns that have
failed before, and its `winning_patterns` + `exemplars` as positive
style references. `find-exemplars` returns full approved bodies from
similar past sessions — use as phrasing inspiration, don't copy verbatim.
Still apply the strictness level rules on top.

Read `prompts.py::PROPOSE_REDRAFTS_LEVELS[<level>]` and apply those rules
when generating the 3 redrafts. Include all prior attempts and their
failure reasons in the context — redrafts should NOT repeat patterns
that already failed.

Do NOT print the `===REDRAFTS===` block. Present the 3 redraft options as
plain-text alternatives (Option A/B/C) with a one-line note per option
explaining what changed and why. Keep it compact.

If `fundamental_mismatch: true`, state briefly that the use case is hard
to fit under UTILITY. Do NOT suggest MARKETING, alternate channels, or
escalation. Still present the 3 best-effort redrafts.

### STATE 8 — USER_CHOOSES

The user picks 1, 2, or 3 — or edits an option — or provides their own
rewrite. Confirm the final body with the user.

Update the context's `body` with the chosen version. Loop back to STATE 3
(SUBMIT) with a fresh timestamped name and incremented attempt_no.

Note: recategorized templates are already deleted in STATE 5. Rejected
templates are left in Route Mobile's library (they can't be used anyway).

### STATE 9 — HARD_STOP

After 5 failed attempts:

> "Reached max attempts (5) without UTILITY approval. Here's the history:"
>
> [table of each attempt: attempt_no, body (truncated), status, category, outcome]

Then archive the session so future runs can learn from it:

```bash
python adapters.py archive-session
```

**Then refresh the history summary** (see STATE 6 for the procedure).
Failed sessions teach as much as successful ones — anti-patterns from
this run should be surfaced to future sessions.

Stop. Do NOT suggest MARKETING category, alternate channels, or escalation
paths. The agent's only job is UTILITY approval; when that isn't possible,
report the outcome and stop.

---

## Auth handling

All API calls in `adapters.py` auto-handle auth. You do not need to manually
refresh JWTs. If a call returns an error about auth, run
`python adapters.py login` and retry the call. Check `.env` is populated
if login itself fails.

## State inspection

At any point you can run `python adapters.py session` to see the full
current state (auth info + current submission session with all attempts).
Useful for debugging and for generating the HARD_STOP summary.

## Important reminders

- Use the delimited blocks in `prompts.py` as internal scaffolding only.
  Do NOT print them to the user. Show clean, minimal plain-text summaries
  with just the fields the user needs to act on.
- Ask clarifying questions only when genuinely required. Ask as plain
  prose, one or two questions at a time — never as a dumped list.
- The agent's only goal is UTILITY approval. Never suggest MARKETING
  category, alternate channels, or escalation paths at any point —
  including warnings, redraft proposals, and hard-stop.
- Don't proceed past CONFIRM_DRAFT if `utility_risk` is high without
  explicit user acknowledgment.
- Every resubmission uses a fresh timestamped name. Never reuse a name.
- Never skip the strictness level for the current attempt number. Level 2
  redrafts after attempt 3 will waste an attempt.
- If the user asks to check status "now", run the status command even if
  the next scheduled checkpoint hasn't been reached.
