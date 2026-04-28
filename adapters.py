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
  - stdlib only (urllib, json, os) — no pip install needed.
  - JWT is cached in session.json; expiry is read from the JWT itself.
  - On 401 any API call will re-login once and retry once. Second 401 is fatal.
  - No delete endpoint wired (per request).
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
from pathlib import Path

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

def archive_session() -> str:
    """Move current_session to history/<ts>_<base_name>.json, reset, and
    refresh derived lint rules from the updated history."""
    state = _load_session()
    curr = state.get("current_session", {}) or {}
    if not curr.get("base_name"):
        return None
    HISTORY_DIR.mkdir(exist_ok=True)
    ts = int(time.time())
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", curr["base_name"])
    path = HISTORY_DIR / f"{ts}_{safe_name}.json"
    path.write_text(json.dumps(curr, indent=2))
    state["current_session"] = _default_session()["current_session"]
    _save_session(state)
    return str(path)


def _tokenize(text: str) -> set:
    return set(w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2)


def get_history_summary() -> dict:
    """Read the LLM-produced history_summary.json. Summary generation is the
    orchestrator's (Claude Code's) job — see prompts.py::HISTORY_SUMMARY_PROMPT.
    Returns {"exists": False} if the file hasn't been produced yet."""
    if not HISTORY_SUMMARY_FILE.exists():
        return {"exists": False}
    try:
        data = json.loads(HISTORY_SUMMARY_FILE.read_text())
        data["exists"] = True
        return data
    except Exception as e:
        return {"exists": False, "error": str(e)}


def find_exemplars(business_purpose: str, trigger_event: str,
                   top_n: int = 3) -> list:
    """Return top-N approved bodies from history similar to the query."""
    matches = find_similar(business_purpose, trigger_event, top_n=top_n * 3)
    exemplars = [
        {
            "base_name": m["base_name"],
            "similarity": m["similarity"],
            "approved_body": m["approved_body"],
        }
        for m in matches if m.get("approved_body")
    ]
    return exemplars[:top_n]


def find_similar(business_purpose: str, trigger_event: str, top_n: int = 3,
                 min_similarity: float = 0.15) -> list:
    """Scan history/ for past sessions similar to the query. Jaccard on tokens."""
    if not HISTORY_DIR.exists():
        return []
    query = _tokenize(business_purpose + " " + trigger_event)
    if not query:
        return []
    results = []
    for path in sorted(HISTORY_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        ctx = data.get("context") or {}
        hay = _tokenize(ctx.get("business_purpose", "") + " " + ctx.get("trigger_event", ""))
        if not hay:
            continue
        sim = len(query & hay) / len(query | hay)
        if sim < min_similarity:
            continue
        attempts = data.get("attempts", []) or []
        outcomes = [a.get("outcome") for a in attempts if a.get("outcome")]
        final_outcome = outcomes[-1] if outcomes else "UNKNOWN"
        approved_attempt = next(
            (a for a in attempts if a.get("outcome") == "SUCCESS"), None
        )
        results.append({
            "file": path.name,
            "similarity": round(sim, 2),
            "base_name": data.get("base_name"),
            "attempts": len(attempts),
            "final_outcome": final_outcome,
            "approved_body": approved_attempt["body"] if approved_attempt else None,
            "last_body": attempts[-1]["body"] if attempts else None,
            "last_category": attempts[-1].get("category") if attempts else None,
        })
    results.sort(key=lambda r: r["similarity"], reverse=True)
    return results[:top_n]


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
                   help="Return the LLM-produced history summary (clusters, themes, exemplars).")

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
            path = archive_session()
            _out({"ok": True, "archived_to": path})

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

    except Exception as e:
        _out({"ok": False, "error": type(e).__name__, "message": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
