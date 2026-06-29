-- Grants required by the Supabase-backed Steam review collection and insights pipeline.

grant select, insert, update on public.steam_reviews to service_role;
grant select, insert, update on public.candidate_review_collections to service_role;
grant select, insert, update on public.candidate_review_insights to service_role;
grant select, insert, update on public.run_review_rollups to service_role;
