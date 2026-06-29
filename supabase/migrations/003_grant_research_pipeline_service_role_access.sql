-- Grants required by the Supabase-backed research pipeline service.

grant select, insert, update on public.run_candidates to service_role;
grant select, insert, update on public.candidate_discovery_evidence to service_role;
grant select, insert, update on public.candidate_scores to service_role;
grant select, insert, update on public.candidate_classifications to service_role;
grant select, insert, update on public.reports to service_role;
grant select, insert on public.run_events to service_role;
grant select, insert on public.steam_app_snapshots to service_role;
grant select on public.games to service_role;
