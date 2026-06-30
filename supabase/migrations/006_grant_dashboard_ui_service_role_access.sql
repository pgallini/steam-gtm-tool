-- Grants required by the dashboard/workspace UI endpoints.

grant select on public.organizations to service_role;
grant select, insert, update on public.games to service_role;
grant select on public.run_events to service_role;
