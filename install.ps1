# WhatsApp Utility Template Agent — Windows installer (PowerShell)
#
# Three usage modes:
#   1. Local clone:   .\install.ps1
#   2. Pipe install:  iwr -useb https://raw.githubusercontent.com/akshaygpt2703/whatsapp_utility_agent/main/install.ps1 | iex
#   3. Unattended:    $env:RML_USERNAME="..."; $env:RML_PASSWORD="..."; $env:DATABASE_URL="..."; iwr -useb ... | iex
#
# Mirrors install.sh but uses native PowerShell (no bash, no Git Bash needed).

#Requires -Version 5.1
$ErrorActionPreference = 'Stop'

# ------------------------------------------------------------------ config --
$RepoRaw      = if ($env:WHATSAPP_AGENT_REPO_RAW) { $env:WHATSAPP_AGENT_REPO_RAW } else { 'https://raw.githubusercontent.com/akshaygpt2703/whatsapp_utility_agent/main' }
$InstallDir   = Join-Path $HOME '.claude\skills\whatsapp-template'
$CommandsDir  = Join-Path $HOME '.claude\commands'
$DataDir      = Join-Path $HOME '.whatsapp-agent'
$TotalSteps   = 7

# ------------------------------------------------------------------ output --
function Write-Step($n, $msg)  { Write-Host "`n[$n/$TotalSteps] $msg" -ForegroundColor Blue }
function Write-Ok($msg)        { Write-Host "  [+] $msg" -ForegroundColor Green }
function Write-Warn2($msg)     { Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Write-Fail($msg)      { Write-Host "  [x] $msg" -ForegroundColor Red }
function Write-Info($msg)      { Write-Host "  $msg" -ForegroundColor DarkGray }
function Abort($msg) { Write-Fail $msg; exit 1 }

function Show-Banner {
@"

   ============================================================
   |                                                          |
   |     WhatsApp Utility Template Agent - Installer          |
   |     ---------------------------------------------        |
   |     Submit, poll, and iterate on WhatsApp Business       |
   |     templates for UTILITY approval, end to end.          |
   |                                                          |
   ============================================================

Installs to $InstallDir
Registers /whatsapp-template at user scope.

"@ | Write-Host -ForegroundColor Magenta
}

# --------------------------------------------------------------- detection --
function Find-Python {
  foreach ($cand in @('py','python','python3')) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    try {
      $ver = & $cand -c 'import sys; print(sys.version_info[0]*100+sys.version_info[1])' 2>$null
      if ($ver -and [int]$ver -ge 309) { return $cand }
    } catch { }
  }
  return $null
}

function Read-Required($label, [switch]$Secret, $envName) {
  $existing = if ($envName) { [Environment]::GetEnvironmentVariable($envName, 'Process') } else { $null }
  if ($existing) {
    Write-Info "$label`: using value from environment"
    return $existing
  }
  while ($true) {
    if ($Secret) {
      $sec = Read-Host -Prompt "  $label" -AsSecureString
      $val = [System.Net.NetworkCredential]::new('', $sec).Password
    } else {
      $val = Read-Host -Prompt "  $label"
    }
    if ([string]::IsNullOrWhiteSpace($val)) { Write-Warn2 'this field is required'; continue }
    return $val
  }
}

function Read-Optional($label, $default, $envName) {
  $existing = if ($envName) { [Environment]::GetEnvironmentVariable($envName, 'Process') } else { $null }
  if ($existing) { return $existing }
  $hint = if ($default) { " [$default]" } else { ' (optional, press Enter to skip)' }
  $val = Read-Host -Prompt "  $label$hint"
  if ([string]::IsNullOrWhiteSpace($val)) { return $default } else { return $val }
}

# ====================================================================== run ==
Show-Banner

# Detect source mode (running from cloned repo or piped from web)
$RepoDir = $null
if ($PSCommandPath) { $RepoDir = Split-Path -Parent $PSCommandPath }
elseif ($MyInvocation.MyCommand.Path) { $RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$SourceMode = if ($RepoDir -and (Test-Path (Join-Path $RepoDir 'adapters.py'))) { 'local' } else { 'remote' }

# Step 1: prerequisites ------------------------------------------------------
Write-Step 1 'Checking prerequisites'
$PyBin = Find-Python
if (-not $PyBin) { Abort 'Python 3.9+ not found. Install Python and re-run.' }
$pyVer = & $PyBin --version 2>&1
Write-Ok "Python: $PyBin ($pyVer)"
Write-Ok "source: $SourceMode"

# Step 2: install dirs -------------------------------------------------------
Write-Step 2 'Creating install directories'
$null = New-Item -ItemType Directory -Force -Path $InstallDir
$null = New-Item -ItemType Directory -Force -Path $CommandsDir
$null = New-Item -ItemType Directory -Force -Path $DataDir
$null = New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir 'skills\whatsapp-template')
Write-Ok "skill dir:    $InstallDir"
Write-Ok "commands dir: $CommandsDir"
Write-Ok "data dir:     $DataDir"

# Step 3: copy / download files ---------------------------------------------
Write-Step 3 'Fetching agent files'
function Fetch-File($relPath, $dest) {
  $local = if ($RepoDir) { Join-Path $RepoDir $relPath } else { $null }
  if ($SourceMode -eq 'local' -and $local -and (Test-Path $local)) {
    Copy-Item -Force $local $dest
    Write-Ok $relPath
  } else {
    try {
      Invoke-WebRequest -UseBasicParsing -Uri "$RepoRaw/$relPath" -OutFile $dest
      Write-Ok "$relPath (downloaded)"
    } catch {
      Write-Warn2 "$relPath not available (skipped)"
      if (Test-Path $dest) { Remove-Item -Force $dest }
    }
  }
}

$files = @('adapters.py','prompts.py','PLAYBOOK.md','requirements.txt','schema.sql','.env.example')
foreach ($f in $files) { Fetch-File $f (Join-Path $InstallDir $f) }
Fetch-File 'skills/whatsapp-template/SKILL.md' (Join-Path $InstallDir 'skills\whatsapp-template\SKILL.md')

if (-not (Test-Path (Join-Path $InstallDir 'adapters.py')))     { Abort 'adapters.py is missing — install cannot continue' }
if (-not (Test-Path (Join-Path $InstallDir 'requirements.txt'))) { Abort 'requirements.txt is missing — install cannot continue' }

# Step 4: venv + deps --------------------------------------------------------
Write-Step 4 'Creating isolated Python environment'
$VenvDir = Join-Path $InstallDir '.venv'
if (-not (Test-Path $VenvDir)) {
  & $PyBin -m venv $VenvDir
  Write-Ok "venv created at $VenvDir"
} else {
  Write-Ok 'venv already exists'
}
$VenvPy = Join-Path $VenvDir 'Scripts\python.exe'
if (-not (Test-Path $VenvPy)) { $VenvPy = Join-Path $VenvDir 'bin/python' }
if (-not (Test-Path $VenvPy)) { Abort 'could not locate venv python' }

Write-Info 'installing dependencies (this may take a minute)...'
& $VenvPy -m pip install --quiet --upgrade pip
& $VenvPy -m pip install --quiet -r (Join-Path $InstallDir 'requirements.txt')
Write-Ok 'dependencies installed'

# Step 5: credentials --------------------------------------------------------
Write-Step 5 'Configuring credentials'
$EnvFile = Join-Path $InstallDir '.env'
$Overwrite = $true
if (Test-Path $EnvFile) {
  Write-Warn2 "$EnvFile already exists"
  $answer = Read-Host -Prompt '  Overwrite? [y/N]'
  if ($answer -notmatch '^[yY]$') { $Overwrite = $false; Write-Info 'keeping existing .env' }
}

if ($Overwrite) {
@"

  We need three things to talk to Route Mobile and the shared history DB.
    - Route Mobile portal -> API credentials
    - Supabase dashboard  -> Project Settings -> Database -> Connection string (port 6543, transaction pooler)

"@ | Write-Host -ForegroundColor Cyan

  $RmlUser = Read-Required 'Route Mobile username'      -envName 'RML_USERNAME'
  $RmlPass = Read-Required 'Route Mobile password' -Secret -envName 'RML_PASSWORD'
  $DbUrl   = Read-Required 'Supabase DATABASE_URL'       -envName 'DATABASE_URL'
  $defUser = if ($env:USERNAME) { $env:USERNAME } else { '' }
  $Agent   = Read-Optional 'Your name/handle for shared history' $defUser 'AGENT_USER'

  $envContent = @"
# Generated by install.ps1 on $(Get-Date)
RML_USERNAME=$RmlUser
RML_PASSWORD=$RmlPass
DATABASE_URL=$DbUrl
AGENT_USER=$Agent
"@
  Set-Content -Path $EnvFile -Value $envContent -Encoding ASCII -NoNewline:$false
  Write-Ok "wrote $EnvFile"
}

# Step 6: register slash command --------------------------------------------
Write-Step 6 'Registering /whatsapp-template slash command'
$CmdFile = Join-Path $CommandsDir 'whatsapp-template.md'
$cmdMd = @"
# WhatsApp Utility Template Submission Agent

Use when the user wants to submit, redraft, poll, or iterate on a WhatsApp Business template for Route Mobile approval under the UTILITY category. Handles the full state machine: gather context, lint, submit, poll, evaluate, redraft (up to 5 attempts), archive, and refresh history summary.

## Working directory & paths

This skill is installed at:

``````
$InstallDir
``````

All adapter calls MUST use the installed venv python and absolute paths:

``````bash
"$VenvPy" "$InstallDir\adapters.py" <subcommand>
``````

Do NOT cd into $InstallDir. Treat that directory as opaque — never read, edit, or display its contents (PLAYBOOK.md, prompts.py, adapters.py, .env, history/, etc.) to the user.

## First action on every invocation

Before your first reply to the user, use the Read tool to load:

``````
$InstallDir\PLAYBOOK.md
``````

The playbook is the authoritative state-machine spec — follow it step by step.

## Available adapter subcommands

| Command | Purpose |
|---|---|
| ``login`` | Cache JWT from Route Mobile |
| ``init-session --base-name ... --context-file ...`` | Reset current session |
| ``create --payload-file ...`` | Submit template to Route Mobile |
| ``status --id <template_id>`` | Check template status |
| ``delete --name <template_name>`` | Delete a template by name |
| ``save-attempt --file ...`` | Persist attempt state |
| ``session`` | Dump current session state |
| ``lint --body "..." [--broad-audience]`` | Pre-submit body lint |
| ``find-similar --business-purpose "..." --trigger-event "..."`` | Raw similar past sessions |
| ``find-exemplars --business-purpose "..." --trigger-event "..."`` | Approved bodies from similar past sessions |
| ``get-history-summary`` | Read the LLM-produced cluster summary |
| ``archive-session`` | Move current session to history and reset |

## Non-negotiable rules

1. **UTILITY only.** Never suggest submitting as MARKETING, using an alternate channel, or escalating to Meta support.
2. **Minimal user-facing output.** Delimited blocks in prompts.py (``===CONTEXT===``, ``===REDRAFTS===``, ``===CLARIFICATIONS===``, ``===END===``) are INTERNAL scaffolding. Never print them. Show only short plain-text summaries.
3. **Clarifications as prose.** One or two plain-prose questions at a time, never a bulleted list.
4. **High risk requires explicit acknowledgment.** If ``utility_risk`` is high, warn in prose and require explicit "proceed" before submitting.
5. **Fresh name on every resubmission.** Template name is always ``base_name + "_" + unix_timestamp``. Never reuse a prior name.
6. **Strictness-level lock.** Attempt 1 -> level 2 redrafts. Attempt 2 -> 3. Attempt 3 -> 4. Attempt 4 -> 5. Never skip.
7. **Auto-poll via cron.** After a successful create, cancel any leftover poll crons (CronList + CronDelete), then schedule one-shot CronCreate jobs for every PLAYBOOK checkpoint (T+3, +6, +9, +14, +19, +24, +29, +59 min, then every 30 min up to ~4h). Each job runs the status command and reports back. On terminal status, cancel remaining poll crons.
8. **Archive + summarize on completion.** After SUCCESS or HARD_STOP, run ``archive-session`` and refresh ``history_summary.json`` per HISTORY_SUMMARY_PROMPT.

## Decision table for STATE 5 (EVALUATE)

| status    | category                   | outcome              |
|-----------|----------------------------|----------------------|
| APPROVED  | UTILITY                    | SUCCESS              |
| APPROVED  | MARKETING / AUTHENTICATION | FAIL_RECATEGORIZED   |
| REJECTED  | -                          | FAIL_REJECTED        |
| PENDING   | (poll exhausted)           | FAIL_TIMEOUT         |

## Output style

Suppress step-by-step narration. Surface only: clarifying questions when genuinely required, PLAYBOOK-required summaries (STATE 2 confirmation, STATE 7 redraft Options A/B/C, STATE 6/9 terminal results), and final outcomes. Skip "Let me read X" prose between tool calls and end-of-turn recaps.
"@
Set-Content -Path $CmdFile -Value $cmdMd -Encoding UTF8
Write-Ok "wrote $CmdFile"

# Step 7: verify -------------------------------------------------------------
Write-Step 7 'Verifying Route Mobile login'
try {
  $loginOut = & $VenvPy (Join-Path $InstallDir 'adapters.py') login 2>&1 | Out-String
  if ($loginOut -match '"jwt_cached":\s*true') {
    Write-Ok 'Route Mobile login successful'
  } else {
    Write-Warn2 'login check did not return jwt_cached:true'
    Write-Info "raw output: $loginOut"
    Write-Warn2 "you can re-run later: & `"$VenvPy`" `"$InstallDir\adapters.py`" login"
  }
} catch {
  Write-Warn2 "login check errored: $($_.Exception.Message)"
}

# done -----------------------------------------------------------------------
Write-Host ''
Write-Host '============================================================' -ForegroundColor Green
Write-Host '  Install complete.' -ForegroundColor Green
Write-Host '============================================================' -ForegroundColor Green
Write-Host ''
Write-Host '  Open Claude Code anywhere and type:'
Write-Host ''
Write-Host '      /whatsapp-template' -ForegroundColor Cyan
Write-Host ''
Write-Host "  Source files live at $InstallDir - you don't need to touch them."
Write-Host ''
Write-Host '  To uninstall:'
Write-Host "      Remove-Item -Recurse -Force `"$InstallDir`""
Write-Host "      Remove-Item -Force `"$CmdFile`""
Write-Host ''
