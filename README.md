# WhatsApp Utility Template Agent

An autonomous self learning agent that submits WhatsApp Business templates to
Route Mobile and iterates on redrafts until Meta approves them under the
**UTILITY** category (not MARKETING).

## Why this exists

Meta's WhatsApp Business Platform silently recategorizes templates from
UTILITY to MARKETING when it detects promotional intent in the body.
That recategorization is costly — MARKETING templates carry a higher
per-message fee, need marketing opt-in, and can be blocked by users.

Getting a template approved under UTILITY typically takes multiple
iterations: you submit, Meta rejects or recategorizes, you rewrite more
conservatively, you resubmit. This agent automates that loop.

## What it does

Given a plain-language description of a message you want to send, the
agent:

1. Extracts structured context and flags utility-category risks up front.
2. Lints the body for promotional language before submission.
3. Submits to Route Mobile and polls Meta for the verdict.
4. On failure, generates three progressively stricter redrafts, grounded
   in patterns that have previously worked for similar templates.
5. Loops up to five attempts. Stops after that.
6. Archives every completed session and learns from it — future runs
   benefit from semantic cluster summaries of past successes and failures.

Its only goal is UTILITY approval. It will not propose MARKETING as an
alternative, suggest other channels, or escalate to support.

## Features

- **Nine-state playbook** driving gather → lint → submit → poll → evaluate
  → redraft → archive.
- **Pre-submission regex lint** with ten built-in rules (promotional
  words, urgency language, re-engagement patterns, audience-ownership
  mismatch, formatting and emoji detection).
- **Progressive redraft strictness** from Level 2 (clean up the obvious)
  through Level 5 (bare-bones transactional).
- **History-based learning.** Every archived session feeds an
  LLM-produced cluster summary (`history_summary.json`) that future
  sessions consult for winning patterns, failure themes, and exemplars.
- **Deterministic tool layer.** `adapters.py` is pure Python stdlib —
  no external packages, no AI SDK dependency. All LLM work is done by
  Claude Code itself.
- **Short-circuit polling.** Agent tells you when to check back; never
  blocks a turn waiting for Meta.

## Getting started

### Prerequisites

- [Claude Code](https://claude.com/claude-code) installed.
- Python 3.9+ on your PATH as `python3`.
- A Route Mobile WhatsApp Business API account with API credentials.

### 1. Clone and configure

```bash
git clone <your-fork-url> whatsapp_utility_agent
cd whatsapp_utility_agent
cp .env.example .env
```

Edit `.env` and fill in:

```
RML_USERNAME=...
RML_PASSWORD=...
```

### 2. Verify authentication

```bash
python3 adapters.py login
```

Expected:

```json
{
  "ok": true,
  "jwt_cached": true,
  "jwt_prefix": "eyJ0..."
}
```

If this fails, check your credentials in `.env` before going further.

### 3. Run the agent

From the project directory:

```bash
claude
```

Then tell Claude Code:

> "I want to submit a WhatsApp utility template."

Claude Code will detect the subagent, load `PLAYBOOK.md`, and walk you
through the full flow starting with structured intake questions.

## Usage

### Option A: Invoke implicitly

Any request about submitting, redrafting, or checking a WhatsApp template
will be routed to the agent automatically (via the `description` field
in `.claude/agents/whatsapp-template.md`).

### Option B: Invoke explicitly

Ask Claude Code:

> "Use the whatsapp-template subagent to submit a new reminder template."

### Option C: Direct CLI (no Claude Code)

Every adapter command is scriptable:

```bash
python3 adapters.py login
python3 adapters.py lint --body "Hi {{1}}, your order is confirmed." --broad-audience
python3 adapters.py create --payload-file payload.json
python3 adapters.py status --id <template_id>
python3 adapters.py find-exemplars --business-purpose "order confirmation" --trigger-event "order placed"
python3 adapters.py archive-session
python3 adapters.py get-history-summary
```

Full list: `python3 adapters.py --help`.

## Project layout

```
whatsapp_utility_agent/
|-- PLAYBOOK.md                          # Authoritative state machine
|-- adapters.py                          # Route Mobile API + session CLI
|-- prompts.py                           # Intake, redraft, and summary prompts
|-- .claude/agents/whatsapp-template.md  # Claude Code subagent definition
|-- .env                                 # Your RML credentials (gitignored)
|-- .env.example
|-- session.json                         # Current session state (gitignored)
|-- history/                             # Archived past sessions (gitignored)
|-- history_summary.json                 # Cluster summary (gitignored)
|-- README.md
```

## The nine-state flow

| State | Name              | What happens                                                       |
|-------|-------------------|--------------------------------------------------------------------|
| 1     | GATHER_CONTEXT    | Structured intake, extract fields, consult history summary         |
| 2     | CONFIRM_DRAFT     | Show summary, warn if utility_risk is high, get explicit proceed   |
| 3     | SUBMIT            | Lint body, build payload, call Route Mobile create endpoint        |
| 4     | POLL              | Short-circuit schedule (T+3, +6, +9, +14, +19, +24, +29, +59, ...) |
| 5     | EVALUATE          | Apply decision table to status + category                          |
| 6     | SUCCESS           | Report approval, archive session, refresh history summary          |
| 7     | PROPOSE_REDRAFTS  | Generate 3 options at strictness level N, informed by exemplars    |
| 8     | USER_CHOOSES      | User picks or edits; loop back to STATE 3                          |
| 9     | HARD_STOP         | After 5 failed attempts: show history, archive, refresh, stop      |

The decision table in STATE 5:

| status    | category                   | outcome              |
|-----------|----------------------------|----------------------|
| APPROVED  | UTILITY                    | SUCCESS              |
| APPROVED  | MARKETING / AUTHENTICATION | FAIL_RECATEGORIZED   |
| REJECTED  | any                        | FAIL_REJECTED        |
| PENDING   | (poll exhausted)           | FAIL_TIMEOUT         |

## How history improves the agent

Every completed session (success or hard stop) is archived under
`history/`. After archive, Claude Code reads every past session and
produces `history_summary.json` with:

- Semantic clusters of use-cases (order confirmation, event reminder, etc.)
- Per-cluster pass rates and average attempts to approval
- Winning patterns and failure themes distilled from the raw bodies
- Curated approved exemplars for reuse

Future sessions consult the summary in STATE 1 and STATE 7. Semantic
clustering and anti-pattern extraction are handled by the LLM, not
hardcoded heuristics. The tool layer (`adapters.py`) stays deterministic
and dependency-free; it just reads and writes the files Claude Code
produces.

## Debugging

```bash
python3 adapters.py session                      # Inspect current session state
python3 adapters.py status --id <template_id>    # Manual status check
python3 adapters.py login                        # Force re-authentication
python3 prompts.py                               # Preview all strictness levels
```

## What to commit, what not to

Gitignored by default (do not commit):

- `.env` — contains your RML credentials.
- `session.json` — contains the cached JWT and in-flight template state.
- `history/` — archived sessions may contain CTA URLs with session tokens.
- `history_summary.json` — derived from `history/`.
- `.claude/settings.local.json` — local Claude Code permission state.

Safe to commit:

- `PLAYBOOK.md`, `adapters.py`, `prompts.py`, `.env.example`,
  `.claude/agents/whatsapp-template.md`, `README.md`, `.gitignore`.

Before your first push, run `git status` and confirm no sensitive files
are listed.

## Scope

In scope:

- Create template, check status, detect recategorization (category vs
  `previous_category`), progressive redrafts, five-attempt cap.

Out of scope (for now):

- Deleting old rejected or recategorized templates from Route Mobile's
  library.
- Non-URL button shapes beyond the documented form (phone, quick reply,
  catalog). The payload scaffold covers URL buttons; extend
  `adapters.py::create_template` and `PLAYBOOK.md::STATE 3` if you need
  others.
- Header media (image, document, video).
- Non-English language variants.
- Sending messages using approved templates.

## License

Specify a license (MIT, Apache-2.0, etc.) before open-sourcing.
