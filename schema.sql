-- schema.sql — run once against a fresh Supabase project to set up all tables.
-- Requires: Supabase Postgres (or any Postgres 13+ instance).

-- Enables trigram similarity for find-similar / find-exemplars text matching.
create extension if not exists pg_trgm;

-- One row per archived session (i.e. what's currently in history/*.json).
create table sessions (
  id               bigserial primary key,
  base_name        text        not null,
  business_purpose text        not null,
  trigger_event    text        not null,
  utility_risk     text        not null check (utility_risk in ('low','medium','high','LOW','MEDIUM','HIGH')),
  language         text,
  context          jsonb       not null,                  -- full context blob
  started_at       timestamptz not null,
  completed_at     timestamptz,
  final_outcome    text        check (final_outcome in ('SUCCESS','FAIL_RECATEGORIZED','FAIL_REJECTED','FAIL_TIMEOUT','HARD_STOP')),
  created_by       text,                                  -- which teammate ran it
  created_at       timestamptz not null default now()
);

create index sessions_purpose_trgm  on sessions using gin (business_purpose gin_trgm_ops);
create index sessions_trigger_trgm  on sessions using gin (trigger_event   gin_trgm_ops);
create index sessions_base_name_idx on sessions (base_name);
create index sessions_outcome_idx   on sessions (final_outcome);

-- Many attempts per session (max 5 per the playbook).
create table attempts (
  id                bigserial primary key,
  session_id        bigint not null references sessions(id) on delete cascade,
  attempt_no        int    not null,
  template_name     text   not null,
  template_id       text,
  body              text   not null,
  strictness_level  int    not null,                     -- 1..5
  submitted_at      timestamptz not null,
  evaluated_at      timestamptz,
  status            text,                                -- APPROVED / REJECTED / PENDING
  category          text,                                -- UTILITY / MARKETING / AUTHENTICATION
  previous_category text,
  outcome           text,                                -- SUCCESS / FAIL_RECATEGORIZED / FAIL_REJECTED / FAIL_TIMEOUT
  rejection_reason  text,
  unique (session_id, attempt_no)
);

create index attempts_outcome_idx on attempts (outcome);
create index attempts_body_trgm   on attempts using gin (body gin_trgm_ops);

-- Singleton-style cluster summary refreshed after each archive.
create table history_summary (
  id            bigserial primary key,
  summarized_at timestamptz not null default now(),
  session_count int          not null,
  clusters      jsonb        not null,
  anti_patterns jsonb        not null
);
-- Read latest with: select * from history_summary order by summarized_at desc limit 1;
