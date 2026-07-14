-- Adds fields and synchronization tables for the Steam data cache and weekly sync workflow.

alter table steam_apps
add column if not exists catalog_last_modified bigint,
add column if not exists catalog_price_change_number bigint,
add column if not exists last_catalog_checked_at timestamptz,
add column if not exists last_detail_checked_at timestamptz,
add column if not exists last_successfully_fetched_at timestamptz,
add column if not exists next_refresh_at timestamptz,
add column if not exists fetch_status text not null default 'catalog_only',
add column if not exists fetch_error text,
add column if not exists consecutive_fetch_failures integer not null default 0,
add column if not exists is_available boolean,
add column if not exists header_image_url text,
add column if not exists supports_windows boolean,
add column if not exists supports_mac boolean,
add column if not exists supports_linux boolean,
add column if not exists discount_percent integer,
add column if not exists country_code text not null default 'us',
add column if not exists language_code text not null default 'english',
add column if not exists content_hash text,
add column if not exists data_completeness_score integer,
add column if not exists enrichment_priority integer not null default 100,
add column if not exists enrichment_reason text;

alter table steam_apps
add constraint steam_apps_fetch_status_check
check (fetch_status in ('catalog_only', 'queued', 'fetching', 'enriched', 'not_found', 'unavailable', 'failed', 'rate_limited', 'skipped_non_game'))
;

alter table steam_apps
add constraint steam_apps_discount_percent_check
check (discount_percent is null or (discount_percent >= 0 and discount_percent <= 100));

alter table steam_apps
add constraint steam_apps_data_completeness_score_check
check (data_completeness_score is null or (data_completeness_score >= 0 and data_completeness_score <= 100));

alter table steam_app_snapshots
add column if not exists price_initial_cents integer,
add column if not exists discount_percent integer,
add column if not exists release_date date,
add column if not exists is_available boolean,
add column if not exists content_hash text;

create table if not exists steam_sync_runs (
    id uuid primary key default gen_random_uuid(),

    sync_type text not null,
    status text not null default 'queued',

    country_code text not null default 'us',
    language_code text not null default 'english',

    started_at timestamptz,
    completed_at timestamptz,
    failed_at timestamptz,

    catalog_records_received integer not null default 0,
    catalog_records_inserted integer not null default 0,
    catalog_records_updated integer not null default 0,

    detail_records_attempted integer not null default 0,
    detail_records_enriched integer not null default 0,
    detail_records_skipped integer not null default 0,
    detail_records_failed integer not null default 0,
    detail_records_rate_limited integer not null default 0,

    snapshots_inserted integer not null default 0,

    last_processed_appid integer,
    failure_message text,

    config jsonb not null default '{}'::jsonb,
    summary jsonb not null default '{}'::jsonb,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table steam_sync_runs
add constraint steam_sync_runs_sync_type_check
check (sync_type in ('catalog', 'details', 'weekly', 'manual_app', 'retry_failures'));

alter table steam_sync_runs
add constraint steam_sync_runs_status_check
check (status in ('queued', 'running', 'completed', 'completed_with_errors', 'failed', 'cancelled'));

create index if not exists idx_steam_sync_runs_status
on steam_sync_runs(status);

create index if not exists idx_steam_sync_runs_created_at
on steam_sync_runs(created_at desc);

create trigger steam_sync_runs_set_updated_at
before update on steam_sync_runs
for each row execute function set_updated_at();

create table if not exists steam_sync_items (
    id uuid primary key default gen_random_uuid(),

    sync_run_id uuid not null references steam_sync_runs(id) on delete cascade,

    appid integer not null,

    operation text not null default 'details',
    status text not null default 'queued',

    attempt_count integer not null default 0,

    started_at timestamptz,
    completed_at timestamptz,

    response_status integer,
    error_code text,
    error_message text,

    next_retry_at timestamptz,

    details jsonb not null default '{}'::jsonb,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    unique(sync_run_id, appid, operation)
);

alter table steam_sync_items
add constraint steam_sync_items_operation_check
check (operation in ('catalog', 'details', 'page_signals', 'snapshot'));

alter table steam_sync_items
add constraint steam_sync_items_status_check
check (status in ('queued', 'running', 'completed', 'skipped', 'failed', 'rate_limited', 'retry_pending'));

create index if not exists idx_steam_sync_items_run_status
on steam_sync_items(sync_run_id, status);

create index if not exists idx_steam_sync_items_appid
on steam_sync_items(appid);

create index if not exists idx_steam_sync_items_retry
on steam_sync_items(status, next_retry_at)
where status = 'retry_pending';

create trigger steam_sync_items_set_updated_at
before update on steam_sync_items
for each row execute function set_updated_at();

-- Service role access for Steam sync administrative tables.
grant select, insert, update, delete on public.steam_sync_runs to service_role;
grant select, insert, update, delete on public.steam_sync_items to service_role;
