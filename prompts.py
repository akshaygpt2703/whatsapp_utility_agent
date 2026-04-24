"""
Prompt templates used by the WhatsApp template submission agent.

These are NOT sent to an LLM API — they are reference specifications that
Claude Code reads and applies mentally at two points in the playbook:

  1. GATHER_CONTEXT — when extracting structured context from the user's
     freeform description of their template.

  2. PROPOSE_REDRAFTS_LEVELS[n] — after a failed attempt, when generating
     3 redraft options. The level is tied to the current attempt number.

Placeholders use __MARKER__ syntax (not {marker}) to avoid collision with
WhatsApp's {{n}} variable syntax in template bodies.
"""


INITIAL_INTAKE_PROMPT = """
This is the first turn. The user has not described their template yet.
Before doing anything else, ask them for the information needed to draft a
submission. Use this exact structure so the user knows what to provide:

> Hi! I'll help you submit a WhatsApp template for approval under the
> UTILITY category. To get started, please share:
>
> 1. **Business context** — What does your business do, and which product
>    or service is this message related to?
> 2. **Trigger event** — What specific user action causes this message to
>    fire? (e.g., "user completed SIP registration", "order placed",
>    "payment received") This is the #1 factor Meta uses to decide
>    utility vs marketing.
> 3. **Recipient** — Who receives it? (existing customer, new signup,
>    lead who hasn't transacted yet, etc.)
> 4. **Draft message body** — The exact text you want to send. Use
>    `{{1}}`, `{{2}}`, ... for personalization variables if you know them,
>    or just describe the placeholders in plain English and I'll format.
> 5. **Variables** — What does each `{{n}}` represent? (e.g., `{{1}}` =
>    customer first name, `{{2}}` = order ID)
> 6. **Call-to-action button** (optional) — Do you want a button? If yes:
>    URL / phone number / quick reply? What text and where does it point?
> 7. **Header / media** (optional) — Any header text, image, or document
>    above the body?
>
> You can paste everything in one message, or share what you have and
> we'll fill gaps as we go.

After the user responds, move to the GATHER_CONTEXT extraction step below.
If critical fields are still missing (especially trigger_event), ask
targeted follow-ups before extracting.
"""


GATHER_CONTEXT_PROMPT = """
You are extracting structured context from the user's freeform description
of a WhatsApp template they want to submit. The delimited block below is
for INTERNAL use only — write it to the context JSON file, but do NOT
print it to the user. To the user, show a short plain-text summary with
only the fields they need: body, variables, CTA, utility_risk + one-line
reason.

REQUIRED FIELDS:
- business_purpose:   one sentence summary
- trigger_event:      the specific user action that causes this message to fire
                      (e.g., "user completed SIP registration", "order placed",
                      "mandate approved by bank"). If the user can't name a
                      concrete trigger, mark MISSING and add a clarification.
                      This is the #1 signal for utility vs marketing.
- base_name:          snake_case template name, no timestamp (agent adds timestamp)
- body:               exact WhatsApp body with {{1}}, {{2}}, ... placeholders,
                      preserving any formatting the user included
- variables:          list of (index, meaning) pairs
- has_cta:            true/false
- cta:                type (url|phone|quick_reply), text, value — or none
- language:           always "en"

UTILITY SANITY CHECK:
Meta's UTILITY category requires the message to be a transactional response
to something the user did, or a status update for an account/transaction
they own. Flag these as RED flags pushing toward marketing:
- Promotes an event, product, or offer the user didn't explicitly opt into
- Re-engagement ("come back", "don't miss out", "last chance")
- Cross-sell or upsell language
- Announces something new rather than responding to a user action
- Trigger event is vague ("user is a subscriber") rather than concrete

Set utility_risk to low | medium | high with a one-line reason.

OUTPUT FORMAT — return EXACTLY this block, nothing else:

===CONTEXT===
business_purpose: <one sentence>
trigger_event: <concrete user action, or MISSING>
base_name: <snake_case>
body: <full body with {{n}} placeholders, preserving formatting>
variables:
  1: <meaning of {{1}}>
  2: <meaning of {{2}}>
has_cta: <true|false>
cta_type: <url|phone|quick_reply|none>
cta_text: <button text or none>
cta_value: <url/phone/payload or none>
language: en
utility_risk: <low|medium|high>
utility_risk_reason: <one line>
===CLARIFICATIONS===
- <question 1, if any>
- <question 2, if any>
===END===
"""


# ---------------------------------------------------------------------------
# Redraft prompt sections — combined by render_redraft_prompt()
# ---------------------------------------------------------------------------

_REDRAFT_HEADER = """
You are the redraft generator for a WhatsApp utility template submission agent.
The previous attempt failed (REJECTED, or APPROVED but recategorized away from
UTILITY). Generate 3 redraft options at strictness level __LEVEL__.

CONTEXT:
__CONTEXT_BLOCK__

ATTEMPT HISTORY (most recent last):
__ATTEMPTS_BLOCK__

APPROVED EXEMPLARS (past bodies that got UTILITY approval for similar use
cases — use as reference for tone and structure; do NOT copy verbatim):
__EXEMPLARS_BLOCK__

LATEST FAILURE:
- outcome: __OUTCOME__
- status: __STATUS__
- category returned by Meta: __CATEGORY__
- previous_category: __PREVIOUS_CATEGORY__
- rejected_reason: __REJECTED_REASON__

META UTILITY RULES (non-negotiable):
QUALIFIES as utility: order confirmation, appointment reminder, payment receipt,
account status update, OTP, shipping update, subscription activation/renewal
confirmation for existing subscribers, transaction status, document ready,
KYC status, mandate/payment authorization outcome.

DOES NOT QUALIFY (these are MARKETING, regardless of how they're phrased):
- Event invitations, live session announcements, webinar reminders (even for
  existing users, unless they explicitly registered for that specific event)
- Promotional content, offers, discounts, coupons, free items, rewards
- Re-engagement ("we miss you", "come back", "check out")
- Educational or content marketing ("5 tips...", "new blog post")
- Cross-sell or upsell of products the user hasn't bought
- Announcements of new products/features the user didn't ask about
"""


_LEVEL_2 = """

STRICTNESS LEVEL 2 — "Clean up the obvious"
This is the first redraft after the user's original. Change only what's
clearly triggering rejection. Preserve tone, structure, and variables.

RULES FOR LEVEL 2:
- Remove any mention of: "free", "coupon", "gift", "offer", "discount",
  "reward", "bonus", "entitled to", "you're eligible for", "limited time"
- Remove promotional adjectives ("amazing", "best", "exclusive", "special")
- Remove marketing openers ("Great news!", "Exciting update!")
- KEEP: formatting (bold/italic), CTA button, personalization, all variables
- KEEP: the overall tone and structure the user wrote

Each of the 3 options should take a slightly different approach to removing
promotional content — don't just produce 3 near-identical rewrites.
"""


_LEVEL_3 = """

STRICTNESS LEVEL 3 — "Strip styling and filler"
Level 2 wasn't enough. Now cut deeper.

RULES FOR LEVEL 3:
- Apply all Level 2 rules PLUS:
- Remove ALL formatting: no *bold*, no _italic_, no emoji
- Remove non-factual adjectives even if neutral ("successfully", "quickly",
  "easily", "conveniently") — state facts without qualifiers
- Remove filler phrases ("We're pleased to inform you", "As you know",
  "Thank you for being a valued customer")
- Remove second-sentence explanations that aren't strictly needed to convey
  the transaction outcome
- KEEP: variables, the core transactional fact, the CTA if functionally required

The 3 options should differ in how aggressively they trim — one conservative,
one medium, one aggressive within level 3 bounds.
"""


_LEVEL_4 = """

STRICTNESS LEVEL 4 — "Transactional core only"
Levels 2 and 3 didn't pass. Reduce to bare transactional essentials.

RULES FOR LEVEL 4:
- Apply all Level 2 + 3 rules PLUS:
- The message must answer one question: "What happened to my <thing>?"
- ONE or TWO sentences maximum
- No greetings beyond optional "Hi <name-variable>" — even that is droppable
- No closing lines ("Thank you", "Team ABC", "Regards")
- CTA only if the user must take an action to complete the transaction;
  otherwise drop it
- If a variable isn't strictly needed to identify the transaction, drop it
- The message should read like a bank SMS, not a marketing email

The 3 options should experiment with: (a) absolute minimum words,
(b) minimum words + CTA preserved, (c) minimum words + one contextual detail.
"""


_LEVEL_5 = """

STRICTNESS LEVEL 5 — "Bare-bones, last-attempt mode"
This is the final attempt. Pass or fail, this is the last try.

RULES FOR LEVEL 5:
- Apply all previous level rules PLUS:
- ONE sentence. No exceptions.
- Format shape (example): "<name-var>, your <action> is <outcome>. <one
  optional critical detail, only if operationally required>."
- No CTA unless the transaction is literally incomplete without user action
- Drop all variables except those required to identify what happened
- Tone is robotic and factual. Prioritize passing over sounding good.
- If you cannot meaningfully shorten further without losing the transactional
  meaning, acknowledge this in change_summary and propose only small variants.

Explicitly consider and state whether this use case fundamentally cannot
fit under UTILITY. If you believe it cannot, set fundamental_mismatch:
true and briefly note why. Do NOT recommend MARKETING or any alternative
— just flag the mismatch.

The 3 options should be minor variants of each other — wording tweaks to
the same minimal transactional structure.
"""


_REDRAFT_OUTPUT = """

If the underlying use case is fundamentally not utility (no genuine user
action to anchor to), set fundamental_mismatch: true and explain briefly.
Still generate 3 best-effort options. Do NOT suggest MARKETING or any
alternative channel in mismatch_reason or change_summary.

OUTPUT FORMAT — the delimited block below is for INTERNAL use only. Do
NOT print it to the user. Use it to extract the 3 options, then present
them to the user as plain-text Option A/B/C with one-line notes.

===REDRAFTS===
fundamental_mismatch: <true|false>
mismatch_reason: <one line, or none>
---OPTION 1---
body: <redrafted body with {{n}} placeholders>
variables_used: <comma-separated indices, e.g. 1,2>
cta_suggestion: <keep original | modify to: X | remove>
change_summary: <one line — what changed and why at this strictness level>
utility_confidence: <high|medium|low>
---OPTION 2---
body: ...
variables_used: ...
cta_suggestion: ...
change_summary: ...
utility_confidence: ...
---OPTION 3---
body: ...
variables_used: ...
cta_suggestion: ...
change_summary: ...
utility_confidence: ...
===END===
"""


HISTORY_SUMMARY_PROMPT = """
You (Claude Code) are refreshing the history summary for the WhatsApp
utility template agent. This runs after a session is archived. Read
every file under history/*.json (use Glob + Read), then produce a single
structured summary that future sessions will consult for guidance.

OUTPUT: write JSON to history_summary.json with this exact shape.

{
  "summarized_at": <unix_ts>,
  "session_count": <int>,
  "clusters": [
    {
      "name": "<snake_case_use_case>",
      "description": "<one sentence>",
      "session_count": <int>,
      "pass_rate": <float 0..1>,
      "avg_attempts_to_approval": <float or null>,
      "winning_patterns": ["<one-line theme>", ...],
      "failure_themes": ["<one-line theme>", ...],
      "exemplars": [
        {"body": "<approved body>", "from": "<history_file_name>"}
      ]
    }
  ],
  "anti_patterns": [
    {
      "theme": "<short_snake_case_name>",
      "description": "<one sentence>",
      "seen_in": ["<history_file_name>", ...]
    }
  ]
}

RULES:
- Cluster by semantic use-case, not by template name. "event_reminder"
  groups Fund Advisor Live, webinar reminders, live-session alerts —
  all semantically the same, regardless of base_name.
- Anti-patterns must be SEMANTIC themes, not raw n-grams. Examples:
  - audience_ownership_mismatch — using "your X" when audience is broad
  - promotional_topic_tease — previewing topic instead of stating the fact
  - urgency_language — limited-time, don't-miss, hurry phrasing
  - unregistered_ownership — implying the user registered when audience
    is all subscribers
  Do NOT list phrases like "in a few" or "right now" as anti-patterns.
- Winning patterns must transfer across the cluster — e.g., "anchor on
  registered-user status", "lead with time-to-event", "state fact
  without adjectives".
- Pick exemplars that are short, purely transactional, and reusable as
  style references. At most 3 per cluster.
- If history/ has fewer than 3 completed sessions total (any outcome),
  write a minimal summary: session_count set, clusters: [],
  anti_patterns: []. Future sessions will just skip it.

After writing the file, tell the user in one line: "History summary
refreshed — N sessions across M clusters." Do not dump the JSON.
"""


PROPOSE_REDRAFTS_LEVELS = {
    2: _REDRAFT_HEADER + _LEVEL_2 + _REDRAFT_OUTPUT,
    3: _REDRAFT_HEADER + _LEVEL_3 + _REDRAFT_OUTPUT,
    4: _REDRAFT_HEADER + _LEVEL_4 + _REDRAFT_OUTPUT,
    5: _REDRAFT_HEADER + _LEVEL_5 + _REDRAFT_OUTPUT,
}


def render_redraft_prompt(level: int, context_block: str, attempts_block: str,
                          outcome: str, status: str, category: str,
                          previous_category: str = None,
                          rejected_reason: str = None,
                          exemplars_block: str = None) -> str:
    """Render the redraft prompt for a given strictness level."""
    if level < 2 or level > 5:
        raise ValueError(f"Strictness level must be 2-5, got {level}")
    template = PROPOSE_REDRAFTS_LEVELS[level]
    replacements = {
        "__LEVEL__": str(level),
        "__CONTEXT_BLOCK__": context_block,
        "__ATTEMPTS_BLOCK__": attempts_block,
        "__EXEMPLARS_BLOCK__": exemplars_block or "(no similar approved bodies in history)",
        "__OUTCOME__": outcome,
        "__STATUS__": status,
        "__CATEGORY__": category,
        "__PREVIOUS_CATEGORY__": previous_category or "none",
        "__REJECTED_REASON__": rejected_reason or "none",
    }
    out = template
    for k, v in replacements.items():
        out = out.replace(k, v)
    return out


if __name__ == "__main__":
    dummy_ctx = (
        "business_purpose: Notify user about SIP activation\n"
        "trigger_event: SIP registration completed"
    )
    dummy_attempts = (
        "Attempt 1:\n"
        "  body: Hi {{1}}, your SIP for Order ID {{2}} is now active. "
        "You're entitled to a free coupon!\n"
        "  status: APPROVED\n"
        "  category: MARKETING\n"
        "  previous_category: UTILITY"
    )
    for lvl in (2, 3, 4, 5):
        p = render_redraft_prompt(
            level=lvl,
            context_block=dummy_ctx,
            attempts_block=dummy_attempts,
            outcome="FAIL_RECATEGORIZED",
            status="APPROVED",
            category="MARKETING",
            previous_category="UTILITY",
            rejected_reason=None,
            exemplars_block="(no similar approved bodies in history)",
        )
        print(f"\n{'='*60}\nLEVEL {lvl} PROMPT (length: {len(p)} chars)\n{'='*60}")
        assert "{{1}}" in p, f"Level {lvl}: {{{{1}}}} syntax not preserved"
        assert "{{2}}" in p, f"Level {lvl}: {{{{2}}}} syntax not preserved"
        assert "__LEVEL__" not in p, f"Level {lvl}: unreplaced __LEVEL__ marker"
        assert "__CONTEXT_BLOCK__" not in p, f"Level {lvl}: unreplaced __CONTEXT_BLOCK__"
        assert "__EXEMPLARS_BLOCK__" not in p, f"Level {lvl}: unreplaced __EXEMPLARS_BLOCK__"
        print(p[:500] + "\n...\n[truncated]")
    print("\nAll assertions passed.")
