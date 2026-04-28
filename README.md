# WhatsApp Utility Template Agent

![License: MIT](https://img.shields.io/badge/License-MIT-green.svg) ![Python: 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg) ![Status: Active](https://img.shields.io/badge/Status-Active-brightgreen.svg)

> A self-improving AI agent that gets WhatsApp Business templates approved under Meta's **UTILITY** category instead of silently recategorized to MARKETING — and gets better at it with every session.

Most teams treat template submission as a manual, one-shot process. Meta
rejects or recategorizes, someone rewrites, tries again, repeats. This
agent closes that loop: it submits via Route Mobile, polls Meta for the
verdict, iterates through progressively stricter redrafts on failure, and
archives every session into a growing knowledge base that informs future
submissions.

Human-in-the-loop at two decision points (confirm draft, pick redraft).
Everything else — intake parsing, promotional linting, verdict
interpretation, redraft generation, pattern extraction across history —
is agent-driven.

## Why this exists

Meta's WhatsApp Business Platform silently recategorizes templates from
UTILITY to MARKETING when it detects promotional intent. Recategorized
templates carry a higher per-message fee, need marketing opt-in, and can
be blocked by users. Getting UTILITY approval usually takes multiple
rounds of rewriting and resubmitting. This agent automates the loop and
compounds what it learns across submissions.

## How the agent learns

Every session (success or hard stop) is archived under `history/`. The
agent then re-reads all past sessions and rebuilds `history_summary.json`:

- **Semantic clusters** of use cases — order confirmation, payment
  receipt, event reminder, etc.
- **Per-cluster pass rates** — e.g. "order confirmations: 4/5 approved,
  avg 1.2 attempts"
- **Winning patterns** — phrasings that have passed utility review
- **Anti-patterns** — phrases that trigger recategorization
  ("entitled to," "special offer," "don't miss")
- **Curated exemplars** — 2–3 approved bodies per cluster

Future sessions consult the summary at intake (to classify and surface
exemplars) and at redraft time (to ground rewrites in what has actually
passed). The longer it runs, the less it has to guess.

Clustering and pattern extraction are LLM-driven. The tool layer stays
deterministic and dependency-free.

## Architecture

- **`PLAYBOOK.md`** — the authoritative nine-state flow the agent follows.
- **`adapters.py`** — Route Mobile API calls, session state, and CLI.
  Pure Python stdlib, no external packages.
- **`prompts.py`** — intake, redraft (5 strictness levels), and summary
  prompts.
- **`history/`** + **`history_summary.json`** — the learning corpus.

## Getting started

### Prerequisites

- An AI coding assistant that can read files and invoke shell commands
  (built and tested with Claude Code; adaptable to similar tools).
- Python 3.9+.
- A Route Mobile WhatsApp Business API account.

### Setup

```bash
git clone https://github.com/akshaygpt2703/whatsapp_utility_agent.git
cd whatsapp_utility_agent
cp .env.example .env
# Edit .env and fill in RML_USERNAME, RML_PASSWORD
python3 adapters.py login
```

Expected:

```json
{ "ok": true, "jwt_cached": true, "jwt_prefix": "eyJ0..." }
```

### Run

Open the project in your AI coding assistant and ask it to submit a
WhatsApp utility template. The assistant will load `PLAYBOOK.md` and
walk you through structured intake, submission, polling, and redrafts.

You can also drive the flow directly from the CLI:

```bash
python3 adapters.py lint --body "Hi {{1}}, your order is confirmed." --broad-audience
python3 adapters.py create --payload-file payload.json
python3 adapters.py status --id <template_id>
python3 adapters.py find-exemplars --business-purpose "order confirmation"
python3 adapters.py archive-session
```

Full list: `python3 adapters.py --help`.

## The nine-state flow

| State | Name              | What happens                                                  |
|-------|-------------------|---------------------------------------------------------------|
| 1     | GATHER_CONTEXT    | Structured intake; consult history summary                    |
| 2     | CONFIRM_DRAFT     | Warn if utility_risk is high; get explicit proceed            |
| 3     | SUBMIT            | Lint body; call Route Mobile create endpoint                  |
| 4     | POLL              | Short-circuit schedule (T+3, +6, +9, +14, +19, +24, +29, ...) |
| 5     | EVALUATE          | Apply decision table to status + category                     |
| 6     | SUCCESS           | Report approval; archive; refresh summary                     |
| 7     | PROPOSE_REDRAFTS  | 3 options at strictness level N, informed by exemplars        |
| 8     | USER_CHOOSES      | User picks or edits; loop back to STATE 3                     |
| 9     | HARD_STOP         | After 5 failed attempts: archive; refresh; stop               |

Decision table in STATE 5:

| status    | category                   | outcome              |
|-----------|----------------------------|----------------------|
| APPROVED  | UTILITY                    | SUCCESS              |
| APPROVED  | MARKETING / AUTHENTICATION | FAIL_RECATEGORIZED   |
| REJECTED  | any                        | FAIL_REJECTED        |
| PENDING   | (poll exhausted)           | FAIL_TIMEOUT         |

## Design decisions

- **Human-in-the-loop at STATES 2 and 8.** Each rejected or recategorized
  template is a permanent record in your Route Mobile library and a
  small hit to your WABA quality rating. Two lightweight human
  checkpoints are cheap insurance against the agent running off a cliff.

- **Lint is separate from redraft.** Lint runs on cheap deterministic
  regex — catches obvious promotional language before an attempt is
  burned. Redraft generation is LLM-driven because subtler failures need
  semantic understanding.

- **JWT expiry decoded client-side.** Route Mobile tokens embed `exp` as
  standard JWT. Local decode avoids an extra round trip per call.

- **Timestamped template names.** Meta imposes a cooldown on reusing
  names after deletion. Appending unix timestamps sidesteps this
  entirely.

- **Learning corpus has a shelf life.** Meta updates its classifier
  quietly. Patterns that passed six months ago may not pass today. For
  long-term use, prune old sessions or weight recent ones more heavily.

## What to commit

Gitignored by default: `.env`, `session.json`, `history/`,
`history_summary.json`. Archived sessions may contain CTA URLs with
user-specific tokens — keep them local.

Safe to commit: `PLAYBOOK.md`, `adapters.py`, `prompts.py`,
`.env.example`, the agent configuration file, `README.md`, `LICENSE`,
`.gitignore`.

## Scope

In scope: create, status, delete (auto-deletes recategorized templates),
recategorization detection, progressive redrafts, five-attempt cap,
cross-session learning.

Out of scope: non-URL buttons (phone, quick reply, catalog), header
media, non-English languages, sending messages using approved templates.

## License

MIT — see [LICENSE](./LICENSE)