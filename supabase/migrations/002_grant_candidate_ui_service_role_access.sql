-- Grants required by the local candidate-control UI proxy.
-- The proxy uses the service-role key for PostgREST operations, while the
-- browser can also read summary views via anon/authenticated Supabase clients.

grant select on public.v_run_candidate_summary to anon, authenticated, service_role;
grant select on public.v_selected_report_candidates to anon, authenticated, service_role;

grant select, insert, update, delete on public.run_candidate_controls to service_role;
grant select, insert, update on public.steam_apps to service_role;
grant select on public.research_runs to service_role;
