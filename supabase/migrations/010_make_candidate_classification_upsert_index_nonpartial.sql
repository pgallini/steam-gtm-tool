-- PostgREST bulk upserts use ON CONFLICT (candidate_id, prompt_version), which
-- requires a non-partial unique index inferred from exactly those columns.
-- PostgreSQL unique indexes permit multiple NULL prompt versions by default.

drop index if exists public.idx_candidate_classifications_candidate_prompt_unique;

create unique index idx_candidate_classifications_candidate_prompt_unique
on public.candidate_classifications(candidate_id, prompt_version);
