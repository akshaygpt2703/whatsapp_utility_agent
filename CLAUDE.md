# WhatsApp Utility Agent

## Autonomy
- No need to ask permission for any action in this project, including running crons.
- Do NOT display contents of context.json, payload.json, attempt.json, cron lists, or RML payloads to the user — keep those internal.

## File Permissions

### Read-only
| File | Purpose |
|---|---|
| `PLAYBOOK.md` | Workflow instructions |
| `prompts.py` | Reference prompts (GATHER_CONTEXT, PROPOSE_REDRAFTS, HISTORY_SUMMARY) |
| `adapters.py` | CLI — invoked via Bash, never edited |
| `.env` | Credentials — read by `adapters.py`, not by Claude directly |
| `.env.example` | Setup reference |
| `session.json` | State — managed exclusively by `adapters.py` |
| `README.md` | Project documentation |

### Write-allowed
| File | Purpose |
|---|---|
| `context.json` | Extracted context from user input (STATE 1) |
| `payload.json` | Route Mobile API payload (STATE 3) |
| `attempt.json` | Attempt tracking dict (STATE 3/4/5) |
| `history_summary.json` | Refreshed history summary after SUCCESS or HARD_STOP (STATE 6/9) |

### Read-allowed (not write)
| File | Purpose |
|---|---|
| `history/*.json` | Past session data — read for history summary generation |

### Never edit
- `adapters.py`, `prompts.py`, `session.json`, `.env`, `PLAYBOOK.md` — these are core infrastructure or user-managed files.
