-- Phase 1: Core Supabase/Postgres schema for Steam GTM Intelligence Assistant

-- 5.1 Extensions
create extension if not exists pgcrypto;
create extension if not exists pg_trgm;

-- 7. Common updated_at Trigger
create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- 6. Enum Types
create type member_role as enum (
  'owner',
  'admin',
  'analyst',
  'viewer'
);

create type game_status as enum (
  'concept',
  'prototype',
  'vertical_slice',
  'steam_page_live',
  'released',
  'unknown'
);

create type run_status as enum (
  'draft',
  'queued',
  'running',
  'needs_review',
  'completed',
  'failed',
  'cancelled'
);

create type run_stage as enum (
  'intake',
  'page_signals',
  'discovery',
  'enrichment',
  'filtering',
  'scoring',
  'shortlisting',
  'classification',
  'report_generation',
  'review_collection',
  'review_analysis',
  'completed'
);

create type candidate_source as enum (
  'steam_more_like_this',
  'tag_search',
  'tag_combination_search',
  'user_supplied',
  'client_supplied',
  'reverse_similarity',
  'llm_suggested',
  'manual_research',
  'external_source',
  'unknown'
);

create type candidate_control_type as enum (
  'require_include',
  'exclude',
  'must_consider',
  'benchmark_only',
  'watchlist',
  'note_only'
);

create type candidate_pipeline_status as enum (
  'discovered',
  'enriched',
  'filtered_out',
  'scored',
  'shortlisted',
  'classified',
  'selected_for_report',
  'excluded_by_user',
  'excluded_by_system',
  'error'
);

create type competitor_classification as enum (
  'direct_comp',
  'adjacent_comp',
  'audience_comp',
  'mechanic_comp',
  'commercial_benchmark',
  'emerging_comp',
  'low_fit',
  'noise',
  'unknown'
);

create type report_type as enum (
  'competitor_report',
  'review_insights_report',
  'executive_summary',
  'positioning_report',
  'gtm_plan'
);

-- 8.1 organizations
create table organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger organizations_set_updated_at
before update on organizations
for each row execute function set_updated_at();

-- 8.2 organization_members
create table organization_members (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role member_role not null default 'analyst',
  created_at timestamptz not null default now(),
  unique (organization_id, user_id)
);

create index idx_organization_members_user_id
on organization_members(user_id);

create index idx_organization_members_org_id
on organization_members(organization_id);

-- 8.3 games
create table games (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations(id) on delete cascade,
  title text not null,
  slug text,
  steam_appid integer,
  steam_url text,
  website_url text,
  developer text,
  publisher text,
  status game_status not null default 'unknown',
  short_description text,
  concept_notes text,
  target_audience_notes text,
  positioning_notes text,
  release_date date,
  expected_release_window text,
  created_by uuid references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  raw_intake_json jsonb not null default '{}'::jsonb
);

create index idx_games_organization_id
on games(organization_id);

create index idx_games_steam_appid
on games(steam_appid);

create unique index idx_games_org_slug_unique
on games(organization_id, slug)
where slug is not null;

create trigger games_set_updated_at
before update on games
for each row execute function set_updated_at();

-- 8.4 steam_apps
create table steam_apps (
  appid integer primary key,
  name text not null,
  steam_url text generated always as (
    'https://store.steampowered.com/app/' || appid::text
  ) stored,
  app_type text,
  developer text,
  publisher text,
  release_date_text text,
  release_date date,
  release_status text,
  is_free boolean,
  price_initial_cents integer,
  price_final_cents integer,
  currency text,
  review_summary text,
  recent_review_summary text,
  review_count integer,
  metacritic_score integer,
  short_description text,
  genres text[] not null default '{}',
  categories text[] not null default '{}',
  tags text[] not null default '{}',
  tag_ids integer[] not null default '{}',
  last_fetched_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  raw_appdetails_json jsonb not null default '{}'::jsonb,
  raw_page_signals_json jsonb not null default '{}'::jsonb
);

create index idx_steam_apps_name_trgm
on steam_apps using gin (name gin_trgm_ops);

create index idx_steam_apps_tags
on steam_apps using gin(tags);

create index idx_steam_apps_tag_ids
on steam_apps using gin(tag_ids);

create index idx_steam_apps_review_count
on steam_apps(review_count);

create trigger steam_apps_set_updated_at
before update on steam_apps
for each row execute function set_updated_at();

-- 8.5 steam_app_snapshots
create table steam_app_snapshots (
  id uuid primary key default gen_random_uuid(),
  appid integer not null references steam_apps(appid) on delete cascade,
  fetched_at timestamptz not null default now(),
  fetch_source text not null default 'steam',
  name text,
  review_summary text,
  recent_review_summary text,
  review_count integer,
  price_final_cents integer,
  currency text,
  release_status text,
  tags text[] not null default '{}',
  tag_ids integer[] not null default '{}',
  raw_json jsonb not null default '{}'::jsonb
);

create index idx_steam_app_snapshots_appid_fetched_at
on steam_app_snapshots(appid, fetched_at desc);

-- 9.1 research_runs
create table research_runs (
  id uuid primary key default gen_random_uuid(),
  game_id uuid not null references games(id) on delete cascade,
  organization_id uuid not null references organizations(id) on delete cascade,
  name text,
  status run_status not null default 'draft',
  current_stage run_stage not null default 'intake',
  model_version text,
  discovery_strategy_version text,
  scoring_version text,
  classification_prompt_version text,
  report_template_version text,
  min_review_count integer default 1000,
  include_upcoming boolean not null default true,
  include_low_review_strategic_candidates boolean not null default true,
  run_config jsonb not null default '{}'::jsonb,
  started_at timestamptz,
  completed_at timestamptz,
  failed_at timestamptz,
  failure_message text,
  created_by uuid references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_research_runs_game_id
on research_runs(game_id);

create index idx_research_runs_org_id
on research_runs(organization_id);

create index idx_research_runs_status
on research_runs(status);

create trigger research_runs_set_updated_at
before update on research_runs
for each row execute function set_updated_at();

-- 9.2 run_events
create table run_events (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references research_runs(id) on delete cascade,
  stage run_stage,
  event_type text not null,
  message text,
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index idx_run_events_run_id_created_at
on run_events(run_id, created_at desc);

-- 10.1 run_candidate_controls
create table run_candidate_controls (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references research_runs(id) on delete cascade,
  organization_id uuid not null references organizations(id) on delete cascade,
  control_type candidate_control_type not null,
  steam_appid integer references steam_apps(appid),
  title text,
  steam_url text,
  external_url text,
  reason text,
  user_notes text,
  created_by uuid references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  raw_input_json jsonb not null default '{}'::jsonb,
  constraint run_candidate_controls_has_identifier check (
    steam_appid is not null
    or title is not null
    or steam_url is not null
    or external_url is not null
  )
);

create index idx_run_candidate_controls_run_id
on run_candidate_controls(run_id);

create index idx_run_candidate_controls_steam_appid
on run_candidate_controls(steam_appid);

create index idx_run_candidate_controls_control_type
on run_candidate_controls(control_type);

create trigger run_candidate_controls_set_updated_at
before update on run_candidate_controls
for each row execute function set_updated_at();

-- 11.1 run_candidates
create table run_candidates (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references research_runs(id) on delete cascade,
  organization_id uuid not null references organizations(id) on delete cascade,
  steam_appid integer references steam_apps(appid),
  title text not null,
  steam_url text,
  external_url text,
  primary_source candidate_source not null default 'unknown',
  pipeline_status candidate_pipeline_status not null default 'discovered',
  user_control_type candidate_control_type,
  user_control_id uuid references run_candidate_controls(id) on delete set null,
  is_user_required boolean not null default false,
  is_user_excluded boolean not null default false,
  is_benchmark_only boolean not null default false,
  is_shortlisted boolean not null default false,
  is_selected_for_report boolean not null default false,
  system_exclusion_reason text,
  user_exclusion_reason text,
  discovery_rank integer,
  final_rank integer,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  raw_candidate_json jsonb not null default '{}'::jsonb,
  constraint run_candidates_has_identifier check (
    steam_appid is not null
    or title is not null
    or external_url is not null
  )
);

create unique index idx_run_candidates_run_steam_appid_unique
on run_candidates(run_id, steam_appid)
where steam_appid is not null;

create index idx_run_candidates_run_id
on run_candidates(run_id);

create index idx_run_candidates_org_id
on run_candidates(organization_id);

create index idx_run_candidates_steam_appid
on run_candidates(steam_appid);

create index idx_run_candidates_pipeline_status
on run_candidates(pipeline_status);

create index idx_run_candidates_selected
on run_candidates(run_id, is_selected_for_report);

create trigger run_candidates_set_updated_at
before update on run_candidates
for each row execute function set_updated_at();

-- 11.2 candidate_discovery_evidence
create table candidate_discovery_evidence (
  id uuid primary key default gen_random_uuid(),
  candidate_id uuid not null references run_candidates(id) on delete cascade,
  run_id uuid not null references research_runs(id) on delete cascade,
  source candidate_source not null,
  query text,
  source_rank integer,
  source_score numeric(10,4),
  evidence_notes text,
  raw_evidence_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index idx_candidate_discovery_evidence_candidate_id
on candidate_discovery_evidence(candidate_id);

create index idx_candidate_discovery_evidence_run_id
on candidate_discovery_evidence(run_id);

create index idx_candidate_discovery_evidence_source
on candidate_discovery_evidence(source);

-- 12.1 candidate_scores
create table candidate_scores (
  id uuid primary key default gen_random_uuid(),
  candidate_id uuid not null references run_candidates(id) on delete cascade,
  run_id uuid not null references research_runs(id) on delete cascade,
  scoring_version text not null default 'v1',
  fit_score numeric(10,4),
  tag_overlap_score numeric(10,4),
  anchor_tag_score numeric(10,4),
  commercial_signal_score numeric(10,4),
  review_volume_score numeric(10,4),
  overlap_count integer,
  overlapping_tags text[] not null default '{}',
  overlapping_anchor_tags text[] not null default '{}',
  recommendation_count integer,
  review_count integer,
  metacritic_score integer,
  score_inputs jsonb not null default '{}'::jsonb,
  score_details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index idx_candidate_scores_candidate_id
on candidate_scores(candidate_id);

create index idx_candidate_scores_run_id
on candidate_scores(run_id);

create index idx_candidate_scores_fit_score
on candidate_scores(run_id, fit_score desc);

create unique index idx_candidate_scores_candidate_version_unique
on candidate_scores(candidate_id, scoring_version);

-- 13.1 candidate_classifications
create table candidate_classifications (
  id uuid primary key default gen_random_uuid(),
  candidate_id uuid not null references run_candidates(id) on delete cascade,
  run_id uuid not null references research_runs(id) on delete cascade,
  classification competitor_classification not null default 'unknown',
  confidence numeric(5,4),
  direct_fit_score numeric(10,4),
  audience_fit_score numeric(10,4),
  mechanic_fit_score numeric(10,4),
  commercial_benchmark_score numeric(10,4),
  reasoning text,
  use_for text,
  do_not_use_for text,
  strategic_notes text,
  positioning_notes text,
  prompt_version text,
  model_version text,
  llm_input_json jsonb not null default '{}'::jsonb,
  llm_output_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index idx_candidate_classifications_candidate_id
on candidate_classifications(candidate_id);

create index idx_candidate_classifications_run_id
on candidate_classifications(run_id);

create index idx_candidate_classifications_classification
on candidate_classifications(run_id, classification);

create unique index idx_candidate_classifications_candidate_prompt_unique
on candidate_classifications(candidate_id, prompt_version)
where prompt_version is not null;

-- 14.1 reports
create table reports (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references research_runs(id) on delete cascade,
  organization_id uuid not null references organizations(id) on delete cascade,
  report_type report_type not null,
  title text,
  content_md text,
  content_html text,
  report_json jsonb not null default '{}'::jsonb,
  generated_by text,
  model_version text,
  template_version text,
  created_by uuid references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_reports_run_id
on reports(run_id);

create index idx_reports_org_id
on reports(organization_id);

create index idx_reports_type
on reports(report_type);

create trigger reports_set_updated_at
before update on reports
for each row execute function set_updated_at();

-- 16.1 v_run_candidate_summary
create or replace view v_run_candidate_summary as
select
  rc.id as candidate_id,
  rc.run_id,
  rc.organization_id,
  rc.steam_appid,
  rc.title,
  rc.steam_url,
  rc.primary_source,
  rc.pipeline_status,
  rc.is_user_required,
  rc.is_user_excluded,
  rc.is_benchmark_only,
  rc.is_shortlisted,
  rc.is_selected_for_report,
  sa.review_count,
  sa.review_summary,
  sa.release_status,
  sa.price_final_cents,
  sa.currency,
  sa.tags,
  cs.fit_score,
  cs.overlap_count,
  cs.overlapping_tags,
  cs.overlapping_anchor_tags,
  cc.classification,
  cc.confidence,
  cc.reasoning,
  cc.use_for,
  cc.do_not_use_for,
  rc.created_at,
  rc.updated_at
from run_candidates rc
left join steam_apps sa
  on sa.appid = rc.steam_appid
left join lateral (
  select *
  from candidate_scores cs
  where cs.candidate_id = rc.id
  order by cs.created_at desc
  limit 1
) cs on true
left join lateral (
  select *
  from candidate_classifications cc
  where cc.candidate_id = rc.id
  order by cc.created_at desc
  limit 1
) cc on true;

grant select on v_run_candidate_summary to public;

-- 16.2 v_selected_report_candidates
create or replace view v_selected_report_candidates as
select *
from v_run_candidate_summary
where is_selected_for_report = true
  and is_user_excluded = false
  and pipeline_status not in ('excluded_by_user', 'excluded_by_system', 'error');

-- 15.1 steam_reviews
create table steam_reviews (
  id uuid primary key default gen_random_uuid(),
  steam_appid integer not null references steam_apps(appid) on delete cascade,
  steam_review_id text,
  review_type text,
  voted_up boolean,
  language text,
  playtime_hours numeric(10,2),
  playtime_at_review_hours numeric(10,2),
  helpful_votes integer,
  funny_votes integer,
  received_for_free boolean,
  steam_purchase boolean,
  early_access boolean,
  review_text text,
  created_at_steam timestamptz,
  updated_at_steam timestamptz,
  fetched_at timestamptz not null default now(),
  raw_review_json jsonb not null default '{}'::jsonb
);

create unique index idx_steam_reviews_review_id_unique
on steam_reviews(steam_review_id)
where steam_review_id is not null;

create index idx_steam_reviews_appid
on steam_reviews(steam_appid);

create index idx_steam_reviews_voted_up
on steam_reviews(steam_appid, voted_up);

-- 15.2 candidate_review_collections
create table candidate_review_collections (
  id uuid primary key default gen_random_uuid(),
  candidate_id uuid not null references run_candidates(id) on delete cascade,
  run_id uuid not null references research_runs(id) on delete cascade,
  positive_review_count integer default 0,
  negative_review_count integer default 0,
  total_review_count integer default 0,
  collection_status text not null default 'queued',
  failure_message text,
  started_at timestamptz,
  completed_at timestamptz,
  raw_collection_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index idx_candidate_review_collections_candidate_id
on candidate_review_collections(candidate_id);

create index idx_candidate_review_collections_run_id
on candidate_review_collections(run_id);

-- 15.3 candidate_review_insights
create table candidate_review_insights (
  id uuid primary key default gen_random_uuid(),
  candidate_id uuid not null references run_candidates(id) on delete cascade,
  run_id uuid not null references research_runs(id) on delete cascade,
  praise_themes text[] not null default '{}',
  complaint_themes text[] not null default '{}',
  friction_points text[] not null default '{}',
  positioning_language text[] not null default '{}',
  pricing_sentiment text,
  opportunities text[] not null default '{}',
  summary text,
  prompt_version text,
  model_version text,
  llm_input_json jsonb not null default '{}'::jsonb,
  llm_output_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index idx_candidate_review_insights_candidate_id
on candidate_review_insights(candidate_id);

create index idx_candidate_review_insights_run_id
on candidate_review_insights(run_id);

-- 15.4 run_review_rollups
create table run_review_rollups (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references research_runs(id) on delete cascade,
  shared_praise_themes text[] not null default '{}',
  shared_complaint_themes text[] not null default '{}',
  shared_friction_points text[] not null default '{}',
  audience_expectations text[] not null default '{}',
  positioning_opportunities text[] not null default '{}',
  summary text,
  prompt_version text,
  model_version text,
  llm_input_json jsonb not null default '{}'::jsonb,
  llm_output_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index idx_run_review_rollups_run_id
on run_review_rollups(run_id);

-- 20.1 RLS helper function
create or replace function user_is_org_member(org_id uuid)
returns boolean
language sql
security definer
stable
as $$
  select exists (
    select 1
    from organization_members om
    where om.organization_id = org_id
      and om.user_id = auth.uid()
  );
$$;

-- 20. RLS policies
alter table organizations enable row level security;
alter table organization_members enable row level security;
alter table games enable row level security;
alter table research_runs enable row level security;
alter table run_candidate_controls enable row level security;
alter table run_candidates enable row level security;
alter table candidate_discovery_evidence enable row level security;
alter table candidate_scores enable row level security;
alter table candidate_classifications enable row level security;
alter table reports enable row level security;
alter table run_events enable row level security;
alter table steam_apps enable row level security;
alter table steam_app_snapshots enable row level security;
alter table steam_reviews enable row level security;
alter table candidate_review_collections enable row level security;
alter table candidate_review_insights enable row level security;
alter table run_review_rollups enable row level security;

create policy "Members can view their organizations"
on organizations
for select
using (
  exists (
    select 1
    from organization_members om
    where om.organization_id = organizations.id
      and om.user_id = auth.uid()
  )
);

create policy "Org members can insert organizations"
on organizations
for insert
with check (
  exists (
    select 1
    from organization_members om
    where om.organization_id = organizations.id
      and om.user_id = auth.uid()
  )
);

create policy "Org members can update organizations"
on organizations
for update
using (
  exists (
    select 1
    from organization_members om
    where om.organization_id = organizations.id
      and om.user_id = auth.uid()
  )
)
with check (
  exists (
    select 1
    from organization_members om
    where om.organization_id = organizations.id
      and om.user_id = auth.uid()
  )
);

create policy "Org members can delete organizations"
on organizations
for delete
using (
  exists (
    select 1
    from organization_members om
    where om.organization_id = organizations.id
      and om.user_id = auth.uid()
  )
);

-- Helper macro for repeated org-scoped policies
create or replace view org_scoped_tables as
select oid, relname from pg_class where false;
