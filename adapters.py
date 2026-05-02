"""
Route Mobile WhatsApp Business API adapters + session state.

Usage (from Claude Code via Bash tool):
    python adapters.py login
    python adapters.py create --payload-file /tmp/payload.json
    python adapters.py status --id 1996983677874846
    python adapters.py init-session --file /tmp/context.json
    python adapters.py save-attempt --file /tmp/attempt.json
    python adapters.py session

All commands print JSON to stdout. On error, exit code != 0 and a JSON error
object is printed.

Design notes:
  - JWT is cached in session.json; expiry is read from the JWT itself.
  - On 401 any API call will re-login once and retry once. Second 401 is fatal.
  - History (sessions, attempts, history_summary) lives in Supabase Postgres.
    Local history/*.json files are kept as a write-through cache only and are
    NOT consulted by find-similar / find-exemplars / get-history-summary.
"""

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = "https://apis.rmlconnect.net"
LOGIN_PATH = "/auth/v1/login/"
CREATE_PATH = "/wba/template/create"
STATUS_PATH = "/wba/template/{template_id}"
DELETE_PATH = "/wba/template/"

PROJECT_DIR = Path(__file__).resolve().parent
ENV_FILE = PROJECT_DIR / ".env"
SESSION_FILE = PROJECT_DIR / "session.json"
HISTORY_DIR = PROJECT_DIR / "history"
HISTORY_SUMMARY_FILE = PROJECT_DIR / "history_summary.json"

JWT_REFRESH_BUFFER_SECONDS = 60  # re-login if JWT expires within this window


# ---------------------------------------------------------------------------
# .env parsing (no external dependency)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Supabase Postgres connection
# ---------------------------------------------------------------------------

# Outcomes considered terminal failures (non-success).
_FAIL_OUTCOMES = {"FAIL_RECATEGORIZED", "FAIL_REJECTED", "FAIL_TIMEOUT"}
MAX_ATTEMPTS = 5


def _db():
    """Open a Postgres connection from DATABASE_URL in .env. Caller closes."""
    env = load_env()
    dsn = env.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL not set in .env — needed for Supabase-backed history."
        )
    return psycopg.connect(dsn, autocommit=False)


def _agent_user() -> str:
    env = load_env()
    return (
        env.get("AGENT_USER")
        or os.environ.get("AGENT_USER")
        or os.environ.get("USER")
        or "unknown"
    )


def _ts_to_dt(ts):
    """Convert unix epoch (int/float/str) to a UTC datetime, or None."""
    if ts is None or ts == "":
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)


def _derive_final_outcome(attempts: list) -> str:
    """Roll up an attempt list into a session-level final_outcome."""
    if not attempts:
        return None
    if any(a.get("outcome") == "SUCCESS" for a in attempts):
        return "SUCCESS"
    last = attempts[-1].get("outcome")
    if not last:
        return None
    if len(attempts) >= MAX_ATTEMPTS and last in _FAIL_OUTCOMES:
        return "HARD_STOP"
    return last


def load_env() -> dict:
    if not ENV_FILE.exists():
        raise FileNotFoundError(
            f".env not found at {ENV_FILE}. Copy .env.example to .env and fill in your credentials."
        )
    env = {}
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        env[key.strip()] = val.strip().strip('"').strip("'")
    return env


# ---------------------------------------------------------------------------
# session.json read/write
# ---------------------------------------------------------------------------

def _default_session() -> dict:
    return {
        "auth": {"jwt": None, "expiry_ts": 0},
        "current_session": {
            "base_name": None,
            "context": None,
            "attempts": [],
            "started_at": None,
        },
    }


def _load_session() -> dict:
    if not SESSION_FILE.exists():
        return _default_session()
    try:
        return json.loads(SESSION_FILE.read_text())
    except json.JSONDecodeError:
        return _default_session()


def _save_session(state: dict) -> None:
    SESSION_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def _decode_jwt_exp(jwt: str) -> int:
    """Return the 'exp' claim (unix seconds) from a JWT without verification."""
    try:
        _, payload_b64, _ = jwt.split(".")
        # base64 decode (add padding if needed)
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return int(payload.get("exp", 0))
    except Exception:
        return 0


def _get_valid_jwt() -> str:
    state = _load_session()
    jwt = state["auth"].get("jwt")
    expiry = state["auth"].get("expiry_ts", 0)
    if jwt and expiry - time.time() > JWT_REFRESH_BUFFER_SECONDS:
        return jwt
    # Need fresh token
    return login()


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _http(method: str, path: str, body: dict = None, jwt: str = None) -> tuple:
    """Make an HTTP request. Returns (status_code, parsed_json_or_text)."""
    url = BASE_URL + path
    data_bytes = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data_bytes = json.dumps(body).encode("utf-8")
    if jwt:
        headers["Authorization"] = jwt  # Route Mobile expects raw JWT, no "Bearer "

    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except urllib.error.URLError as e:
        return 0, {"error": "network_error", "reason": str(e.reason)}


def _with_auth_retry(method: str, path: str, body: dict = None):
    """Run an authed call. On 401, re-login once and retry once."""
    jwt = _get_valid_jwt()
    status, resp = _http(method, path, body=body, jwt=jwt)
    if status == 401:
        # Force re-login
        new_jwt = login()
        status, resp = _http(method, path, body=body, jwt=new_jwt)
    return status, resp


# ---------------------------------------------------------------------------
# Public API adapters
# ---------------------------------------------------------------------------

def login() -> str:
    """Log in to Route Mobile, cache JWT + expiry in session.json, return JWT."""
    env = load_env()
    username = env.get("RML_USERNAME")
    password = env.get("RML_PASSWORD")
    if not username or not password:
        raise RuntimeError("RML_USERNAME and RML_PASSWORD must be set in .env")

    status, resp = _http("POST", LOGIN_PATH, body={"username": username, "password": password})
    if status not in (200, 201) or not isinstance(resp, dict) or not resp.get("JWTAUTH"):
        raise RuntimeError(f"Login failed (status={status}): {resp}")

    jwt = resp["JWTAUTH"]
    expiry = _decode_jwt_exp(jwt)

    state = _load_session()
    state["auth"] = {"jwt": jwt, "expiry_ts": expiry}
    _save_session(state)
    return jwt


def create_template(payload: dict) -> dict:
    """POST to /wba/template/create. Returns response dict or raises."""
    status, resp = _with_auth_retry("POST", CREATE_PATH, body=payload)
    if status not in (200, 201, 202):
        raise RuntimeError(f"create_template failed (status={status}): {resp}")
    return resp


def get_template_status(template_id: str) -> dict:
    """GET /wba/template/{id}. Returns parsed object from data[0] for convenience."""
    path = STATUS_PATH.format(template_id=template_id)
    status, resp = _with_auth_retry("GET", path)
    if status != 200:
        raise RuntimeError(f"get_template_status failed (status={status}): {resp}")
    # Response wraps the template in data[0]
    if isinstance(resp, dict) and isinstance(resp.get("data"), list) and resp["data"]:
        return resp["data"][0]
    return resp


def delete_template(template_name: str) -> dict:
    """DELETE /wba/template/?name=<name>. Removes a template by name."""
    path = DELETE_PATH + "?name=" + urllib.parse.quote(template_name, safe="")
    status, resp = _with_auth_retry("DELETE", path)
    if status not in (200, 201, 202, 204):
        raise RuntimeError(f"delete_template failed (status={status}): {resp}")
    return resp if isinstance(resp, dict) else {"raw": resp}


# ---------------------------------------------------------------------------
# Session state helpers (used by CC via CLI)
# ---------------------------------------------------------------------------

def init_session(base_name: str, context: dict) -> dict:
    """Reset current_session for a new template submission series."""
    state = _load_session()
    state["current_session"] = {
        "base_name": base_name,
        "context": context,
        "attempts": [],
        "started_at": int(time.time()),
    }
    _save_session(state)
    return state["current_session"]


def save_attempt(attempt: dict) -> dict:
    """Append or update an attempt in the current session.

    If an attempt with the same template_id already exists, replace it
    (used when polling updates status fields). Otherwise append.
    """
    state = _load_session()
    attempts = state["current_session"]["attempts"]
    replaced = False
    for i, a in enumerate(attempts):
        if a.get("template_id") and a["template_id"] == attempt.get("template_id"):
            attempts[i] = {**a, **attempt}
            replaced = True
            break
    if not replaced:
        attempts.append(attempt)
    _save_session(state)
    return state["current_session"]


def get_session() -> dict:
    return _load_session()


# ---------------------------------------------------------------------------
# Feature: pre-submission lint
# ---------------------------------------------------------------------------

# (severity, rule_name, regex_pattern, case_sensitive, reason)
LINT_RULES = [
    ("high", "promotional_word",
     r"\b(free|offer|discount|coupon|gift|reward|bonus|eligible|entitled)\b",
     False, "Promotional keyword that Meta classifies as marketing."),
    ("high", "urgency_cta",
     r"(limited time|last chance|don'?t miss|hurry|now only|today only)",
     False, "Urgency language suggests promotional intent."),
    ("medium", "promotional_adjective",
     r"\b(amazing|best|exclusive|special|incredible|wonderful|fantastic)\b",
     False, "Promotional adjective. Neutral transactional language is safer."),
    ("medium", "marketing_opener",
     r"(great news|exciting update|good news|pleased to inform)",
     False, "Marketing opener. Utility messages state the fact directly."),
    ("medium", "reengagement",
     r"(come back|miss you|we'?d love|don'?t miss out|check out)",
     False, "Re-engagement language is a strong marketing signal."),
    ("low", "content_marketing",
     r"\b(tips|learn more|blog|read more|webinar|newsletter)\b",
     False, "Content-marketing language; may be flagged as non-transactional."),
    ("low", "formatting_bold", r"\*[^*\s][^*]*\*", True,
     "Bold markdown present; level-3+ redrafts should strip this."),
    ("low", "formatting_italic", r"(?<!\w)_[^_\s][^_]*_(?!\w)", True,
     "Italic markdown present; level-3+ redrafts should strip this."),
    ("low", "emoji_presence",
     r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]", True,
     "Emoji present; neutral transactional messages typically avoid emoji."),
]


def lint_body(body: str, broad_audience: bool = False) -> list:
    """Return a list of warnings for a template body. Empty list = clean."""
    warnings = []
    for severity, rule, pattern, case_sensitive, reason in LINT_RULES:
        flags = 0 if case_sensitive else re.IGNORECASE
        m = re.search(pattern, body, flags)
        if m:
            warnings.append({
                "severity": severity,
                "rule": rule,
                "match": m.group(0),
                "reason": reason,
            })
    if broad_audience and re.search(r"\byour\s+\w+", body, re.IGNORECASE):
        warnings.append({
            "severity": "high",
            "rule": "audience_ownership_mismatch",
            "match": re.search(r"\byour\s+\w+", body, re.IGNORECASE).group(0),
            "reason": "Body uses 'your X' ownership framing, but audience is broad (not restricted to users who took a specific action). Meta is likely to recategorize.",
        })
    return warnings


# ---------------------------------------------------------------------------
# Feature: session history (learning across runs)
# ---------------------------------------------------------------------------

def _insert_session(conn, current: dict) -> int:
    """Insert a (session, attempts) bundle into Supabase. Returns session id."""
    ctx = current.get("context") or {}
    attempts = current.get("attempts") or []
    final_outcome = _derive_final_outcome(attempts)
    started_at = _ts_to_dt(current.get("started_at")) or datetime.now(timezone.utc)
    completed_at = datetime.now(timezone.utc)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sessions (
                base_name, business_purpose, trigger_event, utility_risk,
                language, context, started_at, completed_at, final_outcome,
                created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (
                current.get("base_name"),
                ctx.get("business_purpose") or "",
                ctx.get("trigger_event") or "",
                ctx.get("utility_risk") or "low",
                ctx.get("language"),
                Jsonb(ctx),
                started_at,
                completed_at,
                final_outcome,
                _agent_user(),
            ),
        )
        session_id = cur.fetchone()[0]

        for a in attempts:
            cur.execute(
                """
                INSERT INTO attempts (
                    session_id, attempt_no, template_name, template_id, body,
                    strictness_level, submitted_at, evaluated_at,
                    status, category, previous_category, outcome, rejection_reason
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    session_id,
                    a.get("attempt_no"),
                    a.get("template_name"),
                    a.get("template_id"),
                    a.get("body") or "",
                    a.get("strictness_level") or a.get("attempt_no") or 1,
                    _ts_to_dt(a.get("submitted_at")) or datetime.now(timezone.utc),
                    _ts_to_dt(a.get("evaluated_at")),
                    a.get("status"),
                    a.get("category"),
                    a.get("previous_category"),
                    a.get("outcome"),
                    a.get("rejection_reason"),
                ),
            )
    return session_id


def archive_session() -> dict:
    """Persist the current session: write a local cache file AND insert
    into Supabase (sessions + attempts). Then reset session.json."""
    state = _load_session()
    curr = state.get("current_session", {}) or {}
    if not curr.get("base_name"):
        return None

    HISTORY_DIR.mkdir(exist_ok=True)
    ts = int(time.time())
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", curr["base_name"])
    path = HISTORY_DIR / f"{ts}_{safe_name}.json"
    path.write_text(json.dumps(curr, indent=2))

    session_id = None
    with _db() as conn:
        session_id = _insert_session(conn, curr)
        conn.commit()

    state["current_session"] = _default_session()["current_session"]
    _save_session(state)
    return {"local_cache": str(path), "session_id": session_id}


# Minimum trigram similarity for a hit. pg_trgm's `%` operator default is 0.3;
# we use the function form so we can keep the lower bar from the file-based
# implementation, which proved useful with a small corpus.
_SIMILARITY_THRESHOLD = 0.15


def find_similar(business_purpose: str, trigger_event: str, top_n: int = 3) -> list:
    """Trigram-rank past sessions in Supabase by combined similarity."""
    sql = """
        WITH ranked AS (
            SELECT s.id, s.base_name, s.final_outcome,
                   similarity(s.business_purpose, %s)
                   + similarity(s.trigger_event,  %s) AS score
            FROM sessions s
            WHERE similarity(s.business_purpose, %s) >= %s
               OR similarity(s.trigger_event,  %s) >= %s
        )
        SELECT
            r.id,
            r.base_name,
            r.score,
            r.final_outcome,
            (SELECT count(*) FROM attempts a WHERE a.session_id = r.id) AS attempt_count,
            (SELECT body FROM attempts a
                WHERE a.session_id = r.id AND a.outcome = 'SUCCESS'
                ORDER BY attempt_no LIMIT 1) AS approved_body,
            (SELECT body FROM attempts a
                WHERE a.session_id = r.id
                ORDER BY attempt_no DESC LIMIT 1) AS last_body,
            (SELECT category FROM attempts a
                WHERE a.session_id = r.id
                ORDER BY attempt_no DESC LIMIT 1) AS last_category
        FROM ranked r
        ORDER BY r.score DESC
        LIMIT %s;
    """
    with _db() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            (
                business_purpose, trigger_event,
                business_purpose, _SIMILARITY_THRESHOLD,
                trigger_event,    _SIMILARITY_THRESHOLD,
                top_n,
            ),
        )
        rows = cur.fetchall()
    return [
        {
            "session_id": r[0],
            "base_name": r[1],
            "similarity": round(float(r[2]), 3),
            "final_outcome": r[3],
            "attempts": r[4],
            "approved_body": r[5],
            "last_body": r[6],
            "last_category": r[7],
        }
        for r in rows
    ]


def find_exemplars(business_purpose: str, trigger_event: str, top_n: int = 3) -> list:
    """Return top-N APPROVED-as-UTILITY bodies from history similar to the query."""
    sql = """
        SELECT
            a.body AS approved_body,
            s.base_name,
            similarity(s.business_purpose, %s)
            + similarity(s.trigger_event,  %s) AS score
        FROM attempts a
        JOIN sessions s ON s.id = a.session_id
        WHERE a.outcome = 'SUCCESS'
          AND (similarity(s.business_purpose, %s) >= %s
               OR similarity(s.trigger_event,  %s) >= %s)
        ORDER BY score DESC
        LIMIT %s;
    """
    with _db() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            (
                business_purpose, trigger_event,
                business_purpose, _SIMILARITY_THRESHOLD,
                trigger_event,    _SIMILARITY_THRESHOLD,
                top_n,
            ),
        )
        rows = cur.fetchall()
    return [
        {
            "base_name": r[1],
            "similarity": round(float(r[2]), 3),
            "approved_body": r[0],
        }
        for r in rows
    ]


def list_sessions() -> list:
    """Return every archived session with its attempts, for the LLM-driven
    history-summary refresh (PROMPTS::HISTORY_SUMMARY_PROMPT)."""
    sql = """
        SELECT s.id, s.base_name, s.business_purpose, s.trigger_event,
               s.utility_risk, s.language, s.context,
               s.started_at, s.completed_at, s.final_outcome, s.created_by,
               (SELECT json_agg(json_build_object(
                    'attempt_no',        a.attempt_no,
                    'template_name',     a.template_name,
                    'template_id',       a.template_id,
                    'body',              a.body,
                    'strictness_level',  a.strictness_level,
                    'submitted_at',      a.submitted_at,
                    'evaluated_at',      a.evaluated_at,
                    'status',            a.status,
                    'category',          a.category,
                    'previous_category', a.previous_category,
                    'outcome',           a.outcome,
                    'rejection_reason',  a.rejection_reason
                  ) ORDER BY a.attempt_no)
                  FROM attempts a WHERE a.session_id = s.id) AS attempts
        FROM sessions s
        ORDER BY s.started_at DESC;
    """
    with _db() as conn, conn.cursor() as cur:
        cur.execute(sql)
        cols = [c.name for c in cur.description]
        rows = cur.fetchall()

    out = []
    for r in rows:
        rec = dict(zip(cols, r))
        for k in ("started_at", "completed_at"):
            if rec.get(k) is not None:
                rec[k] = rec[k].isoformat()
        rec["attempts"] = rec["attempts"] or []
        out.append(rec)
    return out


def get_history_summary() -> dict:
    """Read the latest history summary from Supabase. Returns {"exists": False}
    if no summary has ever been written."""
    sql = """
        SELECT id, summarized_at, session_count, clusters, anti_patterns
        FROM history_summary
        ORDER BY summarized_at DESC
        LIMIT 1;
    """
    with _db() as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    if not row:
        return {"exists": False}
    return {
        "exists": True,
        "id": row[0],
        "summarized_at": row[1].isoformat(),
        "session_count": row[2],
        "clusters": row[3],
        "anti_patterns": row[4],
    }


def save_history_summary(summary: dict) -> dict:
    """Insert a new history_summary row. Expected shape:
        {"session_count": int, "clusters": [...], "anti_patterns": [...]}"""
    session_count = int(summary.get("session_count") or 0)
    clusters = summary.get("clusters") or []
    anti_patterns = summary.get("anti_patterns") or []
    sql = """
        INSERT INTO history_summary (session_count, clusters, anti_patterns)
        VALUES (%s, %s, %s)
        RETURNING id, summarized_at;
    """
    with _db() as conn, conn.cursor() as cur:
        cur.execute(sql, (session_count, Jsonb(clusters), Jsonb(anti_patterns)))
        row = cur.fetchone()
        conn.commit()
    return {"id": row[0], "summarized_at": row[1].isoformat()}


def backfill_history() -> dict:
    """One-shot: push every history/*.json into Supabase. Idempotent on
    (base_name, started_at)."""
    if not HISTORY_DIR.exists():
        return {"scanned": 0, "inserted": 0, "skipped": 0, "files": []}

    inserted = []
    skipped = []
    scanned = 0
    with _db() as conn:
        with conn.cursor() as cur:
            for path in sorted(HISTORY_DIR.glob("*.json")):
                scanned += 1
                try:
                    data = json.loads(path.read_text())
                except Exception as e:
                    skipped.append({"file": path.name, "reason": f"unreadable: {e}"})
                    continue
                base_name = data.get("base_name")
                started_at = _ts_to_dt(data.get("started_at"))
                if not base_name or started_at is None:
                    skipped.append({"file": path.name, "reason": "missing base_name or started_at"})
                    continue
                cur.execute(
                    "SELECT id FROM sessions WHERE base_name = %s AND started_at = %s LIMIT 1;",
                    (base_name, started_at),
                )
                existing = cur.fetchone()
                if existing:
                    skipped.append({"file": path.name, "reason": "already in DB", "session_id": existing[0]})
                    continue
                sid = _insert_session(conn, data)
                inserted.append({"file": path.name, "session_id": sid})
        conn.commit()
    return {
        "scanned": scanned,
        "inserted": len(inserted),
        "skipped": len(skipped),
        "details": {"inserted": inserted, "skipped": skipped},
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _out(obj):
    print(json.dumps(obj, indent=2))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login", help="Log in and cache JWT.")

    c = sub.add_parser("create", help="Create a template.")
    c.add_argument("--payload-file", required=True,
                   help="Path to JSON file containing the full create_template payload.")

    s = sub.add_parser("status", help="Fetch a template's status.")
    s.add_argument("--id", required=True, help="Template ID returned by create.")

    d = sub.add_parser("delete", help="Delete a template by name.")
    d.add_argument("--name", required=True, help="Template name to delete.")

    ini = sub.add_parser("init-session", help="Reset current session.")
    ini.add_argument("--base-name", required=True)
    ini.add_argument("--context-file", required=True,
                     help="Path to JSON file containing the context dict.")

    sa = sub.add_parser("save-attempt", help="Add/update an attempt in current session.")
    sa.add_argument("--file", required=True, help="Path to JSON file with attempt dict.")

    sub.add_parser("session", help="Print current session state.")

    lnt = sub.add_parser("lint", help="Lint a body against UTILITY rules.")
    lnt.add_argument("--body", required=True)
    lnt.add_argument("--broad-audience", action="store_true",
                     help="Set if audience isn't restricted to users who took a specific action.")

    sub.add_parser("archive-session",
                   help="Move current_session to history/ and reset.")

    fs = sub.add_parser("find-similar",
                        help="Find similar past submissions in history/.")
    fs.add_argument("--business-purpose", required=True)
    fs.add_argument("--trigger-event", required=True)
    fs.add_argument("--top", type=int, default=3)

    fe = sub.add_parser("find-exemplars",
                        help="Return past APPROVED bodies for similar cases.")
    fe.add_argument("--business-purpose", required=True)
    fe.add_argument("--trigger-event", required=True)
    fe.add_argument("--top", type=int, default=3)

    sub.add_parser("get-history-summary",
                   help="Return the latest LLM-produced history summary (clusters, themes, exemplars) from Supabase.")

    shs = sub.add_parser("save-history-summary",
                         help="Insert a new history_summary row into Supabase.")
    shs.add_argument("--file", required=True,
                     help="Path to JSON file containing {session_count, clusters, anti_patterns}.")

    sub.add_parser("list-sessions",
                   help="Return every archived session + attempts (for HISTORY_SUMMARY_PROMPT input).")

    sub.add_parser("backfill-history",
                   help="One-shot: push every history/*.json into Supabase. Idempotent.")

    args = p.parse_args()

    try:
        if args.cmd == "login":
            jwt = login()
            _out({"ok": True, "jwt_cached": True, "jwt_prefix": jwt[:20] + "..."})

        elif args.cmd == "create":
            payload = json.loads(Path(args.payload_file).read_text())
            resp = create_template(payload)
            _out({"ok": True, "response": resp})

        elif args.cmd == "status":
            resp = get_template_status(args.id)
            _out({"ok": True, "template": resp})

        elif args.cmd == "delete":
            resp = delete_template(args.name)
            _out({"ok": True, "deleted": args.name, "response": resp})

        elif args.cmd == "init-session":
            ctx = json.loads(Path(args.context_file).read_text())
            sess = init_session(args.base_name, ctx)
            _out({"ok": True, "current_session": sess})

        elif args.cmd == "save-attempt":
            attempt = json.loads(Path(args.file).read_text())
            sess = save_attempt(attempt)
            _out({"ok": True, "current_session": sess})

        elif args.cmd == "session":
            _out(get_session())

        elif args.cmd == "lint":
            warnings = lint_body(args.body, broad_audience=args.broad_audience)
            _out({"ok": True, "clean": len(warnings) == 0, "warnings": warnings})

        elif args.cmd == "archive-session":
            result = archive_session()
            _out({"ok": True, "archived": result})

        elif args.cmd == "find-similar":
            matches = find_similar(args.business_purpose, args.trigger_event,
                                   top_n=args.top)
            _out({"ok": True, "matches": matches})

        elif args.cmd == "find-exemplars":
            exemplars = find_exemplars(args.business_purpose, args.trigger_event,
                                       top_n=args.top)
            _out({"ok": True, "exemplars": exemplars})

        elif args.cmd == "get-history-summary":
            _out({"ok": True, "summary": get_history_summary()})

        elif args.cmd == "save-history-summary":
            summary = json.loads(Path(args.file).read_text())
            result = save_history_summary(summary)
            _out({"ok": True, "saved": result})

        elif args.cmd == "list-sessions":
            _out({"ok": True, "sessions": list_sessions()})

        elif args.cmd == "backfill-history":
            result = backfill_history()
            _out({"ok": True, "backfill": result})

    except Exception as e:
        _out({"ok": False, "error": type(e).__name__, "message": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
