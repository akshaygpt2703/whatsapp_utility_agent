#!/usr/bin/env bash
# WhatsApp Utility Template Agent — installer
# Installs the agent into ~/.claude/skills/whatsapp-template and registers
# the /whatsapp-template slash command at user scope.
set -euo pipefail

# ------------------------------------------------------------------ colors --
if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'
  RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
  BLUE=$'\033[34m'; MAGENTA=$'\033[35m'; CYAN=$'\033[36m'
else
  BOLD=""; DIM=""; RESET=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; MAGENTA=""; CYAN=""
fi

step() { printf "\n${BOLD}${BLUE}[%s/%s]${RESET} ${BOLD}%s${RESET}\n" "$1" "$TOTAL_STEPS" "$2"; }
ok()    { printf "  ${GREEN}✓${RESET} %s\n" "$1"; }
warn()  { printf "  ${YELLOW}!${RESET} %s\n" "$1"; }
fail()  { printf "  ${RED}✗${RESET} %s\n" "$1" >&2; }
info()  { printf "  ${DIM}%s${RESET}\n" "$1"; }

TOTAL_STEPS=7

banner() {
  cat <<BANNER
${MAGENTA}${BOLD}
   ╔══════════════════════════════════════════════════════════╗
   ║                                                          ║
   ║     WhatsApp Utility Template Agent — Installer          ║
   ║     ───────────────────────────────────────────          ║
   ║     Submit, poll, and iterate on WhatsApp Business       ║
   ║     templates for UTILITY approval, end to end.          ║
   ║                                                          ║
   ╚══════════════════════════════════════════════════════════╝
${RESET}
${DIM}This installs into ~/.claude/skills/whatsapp-template and
registers the /whatsapp-template slash command for Claude Code.${RESET}
BANNER
}

abort() { fail "$1"; exit 1; }

# ----------------------------------------------------------------- detect ---
detect_python() {
  for cand in py python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
      if "$cand" -c 'import sys; assert sys.version_info >= (3,9)' 2>/dev/null; then
        echo "$cand"; return 0
      fi
    fi
  done
  return 1
}

prompt_required() {
  # prompt_required VAR_NAME "Label" [is_secret]
  local var="$1" label="$2" secret="${3:-0}" val=""
  while [[ -z "$val" ]]; do
    if [[ "$secret" == "1" ]]; then
      printf "  ${BOLD}%s${RESET}: " "$label"
      read -r -s val
      printf "\n"
    else
      printf "  ${BOLD}%s${RESET}: " "$label"
      read -r val
    fi
    if [[ -z "$val" ]]; then
      warn "this field is required"
    fi
  done
  printf -v "$var" "%s" "$val"
}

prompt_optional() {
  local var="$1" label="$2" default="${3:-}" val=""
  if [[ -n "$default" ]]; then
    printf "  ${BOLD}%s${RESET} ${DIM}[%s]${RESET}: " "$label" "$default"
  else
    printf "  ${BOLD}%s${RESET} ${DIM}(optional, press Enter to skip)${RESET}: " "$label"
  fi
  read -r val
  printf -v "$var" "%s" "${val:-$default}"
}

# ===================================================================== run ==
banner

INSTALL_DIR="${HOME}/.claude/skills/whatsapp-template"
COMMANDS_DIR="${HOME}/.claude/commands"
DATA_DIR="${HOME}/.whatsapp-agent"
REPO_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Step 1: prerequisites ------------------------------------------------------
step 1 "Checking prerequisites"
if PY_BIN=$(detect_python); then
  ok "Python: ${PY_BIN} ($(${PY_BIN} --version 2>&1))"
else
  abort "Python 3.9+ not found. Install Python and re-run."
fi
if ! command -v git >/dev/null 2>&1; then
  warn "git not on PATH (not strictly required, but recommended)"
else
  ok "git: $(git --version)"
fi

# Step 2: install dirs -------------------------------------------------------
step 2 "Creating install directories"
mkdir -p "$INSTALL_DIR" "$COMMANDS_DIR" "$DATA_DIR"
ok "skill dir:    $INSTALL_DIR"
ok "commands dir: $COMMANDS_DIR"
ok "data dir:     $DATA_DIR"

# Step 3: copy files ---------------------------------------------------------
step 3 "Copying agent files"
for f in adapters.py prompts.py PLAYBOOK.md requirements.txt schema.sql .env.example; do
  if [[ -f "$REPO_DIR/$f" ]]; then
    cp "$REPO_DIR/$f" "$INSTALL_DIR/$f"
    ok "$f"
  else
    warn "$f not found in repo (skipped)"
  fi
done
mkdir -p "$INSTALL_DIR/skills/whatsapp-template"
if [[ -f "$REPO_DIR/skills/whatsapp-template/SKILL.md" ]]; then
  cp "$REPO_DIR/skills/whatsapp-template/SKILL.md" "$INSTALL_DIR/skills/whatsapp-template/SKILL.md"
  ok "SKILL.md"
fi

# Step 4: venv + deps --------------------------------------------------------
step 4 "Creating isolated Python environment"
if [[ ! -d "$INSTALL_DIR/.venv" ]]; then
  "$PY_BIN" -m venv "$INSTALL_DIR/.venv"
  ok "venv created at $INSTALL_DIR/.venv"
else
  ok "venv already exists"
fi

if [[ -f "$INSTALL_DIR/.venv/Scripts/python.exe" ]]; then
  VENV_PY="$INSTALL_DIR/.venv/Scripts/python.exe"
elif [[ -f "$INSTALL_DIR/.venv/bin/python" ]]; then
  VENV_PY="$INSTALL_DIR/.venv/bin/python"
else
  abort "could not locate venv python"
fi

info "installing dependencies (this may take a minute)..."
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -r "$INSTALL_DIR/requirements.txt"
ok "dependencies installed"

# Step 5: credentials --------------------------------------------------------
step 5 "Configuring credentials"
ENV_FILE="$INSTALL_DIR/.env"

if [[ -f "$ENV_FILE" ]]; then
  warn "$ENV_FILE already exists"
  printf "  ${BOLD}Overwrite? [y/N]${RESET} "
  read -r answer
  if [[ ! "$answer" =~ ^[Yy]$ ]]; then
    info "keeping existing .env"
    SKIP_ENV=1
  fi
fi

if [[ "${SKIP_ENV:-0}" != "1" ]]; then
  cat <<EOF

  ${CYAN}We need three things to talk to Route Mobile and the shared history DB.${RESET}
  ${DIM}You can find these in:${RESET}
  ${DIM}  • Route Mobile portal -> API credentials${RESET}
  ${DIM}  • Supabase dashboard -> Project Settings -> Database -> Connection string (use port 6543, transaction pooler)${RESET}

EOF
  prompt_required RML_USERNAME "Route Mobile username"
  prompt_required RML_PASSWORD "Route Mobile password" 1
  prompt_required DATABASE_URL "Supabase DATABASE_URL"
  prompt_optional AGENT_USER "Your name/handle for shared history" "${USER:-${USERNAME:-}}"

  umask 077
  cat > "$ENV_FILE" <<EOF
# Generated by install.sh on $(date)
RML_USERNAME=${RML_USERNAME}
RML_PASSWORD=${RML_PASSWORD}
DATABASE_URL=${DATABASE_URL}
AGENT_USER=${AGENT_USER}
EOF
  umask 022
  ok "wrote $ENV_FILE (chmod 600)"
fi

# Step 6: register slash command --------------------------------------------
step 6 "Registering /whatsapp-template slash command"
CMD_FILE="$COMMANDS_DIR/whatsapp-template.md"

VENV_PY_ESCAPED="${VENV_PY// /\\ }"
INSTALL_DIR_ESCAPED="${INSTALL_DIR// /\\ }"

cat > "$CMD_FILE" <<EOF
# WhatsApp Utility Template Submission Agent

Use when the user wants to submit, redraft, poll, or iterate on a WhatsApp Business template for Route Mobile approval under the UTILITY category. Handles the full state machine: gather context, lint, submit, poll, evaluate, redraft (up to 5 attempts), archive, and refresh history summary.

## Working directory & paths

This skill is installed at:

\`\`\`
${INSTALL_DIR}
\`\`\`

All adapter calls MUST use the installed venv python and absolute paths:

\`\`\`bash
"${VENV_PY}" "${INSTALL_DIR}/adapters.py" <subcommand>
\`\`\`

Do NOT \`cd\` into ${INSTALL_DIR}. Treat that directory as opaque — never read, edit, or display its contents (PLAYBOOK.md, prompts.py, adapters.py, .env, history/, etc.) to the user.

## First action on every invocation

Before your first reply to the user, use the Read tool to load:

\`\`\`
${INSTALL_DIR}/PLAYBOOK.md
\`\`\`

The playbook is the authoritative state-machine spec — follow it step by step.

## Available adapter subcommands

| Command | Purpose |
|---|---|
| \`login\` | Cache JWT from Route Mobile |
| \`init-session --base-name ... --context-file ...\` | Reset current session |
| \`create --payload-file ...\` | Submit template to Route Mobile |
| \`status --id <template_id>\` | Check template status |
| \`delete --name <template_name>\` | Delete a template by name |
| \`save-attempt --file ...\` | Persist attempt state |
| \`session\` | Dump current session state |
| \`lint --body "..." [--broad-audience]\` | Pre-submit body lint |
| \`find-similar --business-purpose "..." --trigger-event "..."\` | Raw similar past sessions |
| \`find-exemplars --business-purpose "..." --trigger-event "..."\` | Approved bodies from similar past sessions |
| \`get-history-summary\` | Read the LLM-produced cluster summary |
| \`archive-session\` | Move current session to history and reset |

## Non-negotiable rules

1. **UTILITY only.** Never suggest submitting as MARKETING, using an alternate channel, or escalating to Meta support.
2. **Minimal user-facing output.** Delimited blocks in prompts.py (\`===CONTEXT===\`, \`===REDRAFTS===\`, \`===CLARIFICATIONS===\`, \`===END===\`) are INTERNAL scaffolding. Never print them. Show only short plain-text summaries.
3. **Clarifications as prose.** One or two plain-prose questions at a time, never a bulleted list.
4. **High risk requires explicit acknowledgment.** If \`utility_risk\` is high, warn in prose and require explicit "proceed" before submitting.
5. **Fresh name on every resubmission.** Template name is always \`base_name + "_" + unix_timestamp\`. Never reuse a prior name.
6. **Strictness-level lock.** Attempt 1 -> level 2 redrafts. Attempt 2 -> 3. Attempt 3 -> 4. Attempt 4 -> 5. Never skip.
7. **Auto-poll via cron.** After a successful create, cancel any leftover poll crons (CronList + CronDelete), then schedule one-shot CronCreate jobs for every PLAYBOOK checkpoint (T+3, +6, +9, +14, +19, +24, +29, +59 min, then every 30 min up to ~4h). Each job runs the status command and reports back. On terminal status, cancel remaining poll crons.
8. **Archive + summarize on completion.** After SUCCESS or HARD_STOP, run \`archive-session\` and refresh \`history_summary.json\` per HISTORY_SUMMARY_PROMPT.

## Decision table for STATE 5 (EVALUATE)

| status    | category                   | outcome              |
|-----------|----------------------------|----------------------|
| APPROVED  | UTILITY                    | SUCCESS              |
| APPROVED  | MARKETING / AUTHENTICATION | FAIL_RECATEGORIZED   |
| REJECTED  | —                          | FAIL_REJECTED        |
| PENDING   | (poll exhausted)           | FAIL_TIMEOUT         |

## Output style

Suppress step-by-step narration. Surface only: clarifying questions when genuinely required, PLAYBOOK-required summaries (STATE 2 confirmation, STATE 7 redraft Options A/B/C, STATE 6/9 terminal results), and final outcomes. Skip "Let me read X" prose between tool calls and end-of-turn recaps.
EOF

ok "wrote $CMD_FILE"

# Step 7: verify -------------------------------------------------------------
step 7 "Verifying Route Mobile login"
set +e
LOGIN_OUT=$("$VENV_PY" "$INSTALL_DIR/adapters.py" login 2>&1)
LOGIN_RC=$?
set -e
if [[ $LOGIN_RC -eq 0 ]] && echo "$LOGIN_OUT" | grep -q '"jwt_cached": true'; then
  ok "Route Mobile login successful"
else
  warn "login check did not return jwt_cached:true"
  info "raw output: $LOGIN_OUT"
  warn "you can re-run later: \"$VENV_PY\" \"$INSTALL_DIR/adapters.py\" login"
fi

# done -----------------------------------------------------------------------
cat <<EOF

${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}
${GREEN}${BOLD}  Install complete.${RESET}
${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}

  Open Claude Code anywhere and type:

      ${BOLD}${CYAN}/whatsapp-template${RESET}

  The agent will walk you through intake, submission, polling, and
  redrafts. Source files live at ${DIM}${INSTALL_DIR}${RESET} —
  you don't need to touch them.

  To uninstall:

      rm -rf "${INSTALL_DIR}"
      rm -f  "${CMD_FILE}"

EOF
