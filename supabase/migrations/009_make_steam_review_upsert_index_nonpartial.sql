-- PostgREST bulk upserts use ON CONFLICT (steam_review_id), which requires a
-- non-partial unique index that PostgreSQL can infer from the column list.
-- PostgreSQL unique indexes still permit multiple NULL values by default.

drop index if exists public.idx_steam_reviews_review_id_unique;

create unique index idx_steam_reviews_review_id_unique
on public.steam_reviews(steam_review_id);
