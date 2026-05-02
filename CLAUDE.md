# WhatsApp Utility Agent

## Autonomy
- No need to ask permission for any action in this project, including running crons.
- Do NOT display contents of context.json, payload.json, attempt.json, cron lists, or RML payloads to the user — keep those internal.

## History storage
History (sessions, attempts, history_summary) lives in **Supabase Postgres**, accessed exclusively through `adapters.py` (`archive-session`, `list-sessions`, `find-similar`, `find-exemplars`, `get-history-summary`, `save-history-summary`). The `history/*.json` files are a local write-through cache only and are never read back. Never query the DB by hand.

## File Permissions

### Read-only
| File | Purpose |
|---|---|
| `.env` | Credentials — read by `adapters.py`, not by Claude directly |
| `.env.example` | Setup reference |
| `session.json` | State — managed exclusively by `adapters.py` |
| `README.md` | Project documentation |

### Edit-allowed (with care)
| File | Purpose |
|---|---|
| `PLAYBOOK.md` | Workflow instructions — only edit when the workflow itself changes |
| `prompts.py` | Reference prompts — only edit when the prompt contract changes |
| `adapters.py` | CLI / data-layer — only edit for genuine schema or command changes |

### Write-allowed (transient)
| File | Purpose |
|---|---|
| `context.json` | Extracted context from user input (STATE 1) |
| `payload.json` | Route Mobile API payload (STATE 3) |
| `attempt.json` | Attempt tracking dict (STATE 3/4/5) |
| `/tmp/history_summary.json` | Staging file passed to `save-history-summary` (STATE 6/9) |

### Read-allowed (not write)
| File | Purpose |
|---|---|
| `history/*.json` | Local cache of archived sessions — Supabase is source of truth |

### Never edit
- `session.json`, `.env` — managed by `adapters.py` / the user.
