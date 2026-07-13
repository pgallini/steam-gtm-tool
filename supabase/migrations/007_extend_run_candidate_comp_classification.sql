alter table run_candidates
  add column if not exists comp_exclusion_category text not null default 'uncertain',
  add column if not exists is_major_publisher boolean not null default false,
  add column if not exists major_publisher_match text,
  add column if not exists parent_company text,
  add column if not exists aaa_confidence text,
  add column if not exists aaa_reason text,
  add column if not exists is_industry_classic boolean not null default false,
  add column if not exists classic_confidence text,
  add column if not exists classic_reason text,
  add column if not exists classification_override text,
  add column if not exists classification_notes text;

create or replace view public.v_run_candidate_summary as
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
  rc.updated_at,
  rc.user_control_type,
  rc.user_control_id,
  rc.final_rank,
  rc.system_exclusion_reason,
  rc.user_exclusion_reason,
  rc.is_selected_for_report as include_in_report,
  rc.comp_exclusion_category,
  rc.is_major_publisher,
  rc.major_publisher_match,
  rc.parent_company,
  rc.aaa_confidence,
  rc.aaa_reason,
  rc.is_industry_classic,
  rc.classic_confidence,
  rc.classic_reason,
  rc.classification_override,
  rc.classification_notes,
  sa.developer,
  sa.publisher
from public.run_candidates rc
left join public.steam_apps sa
  on sa.appid = rc.steam_appid
left join lateral (
  select *
  from public.candidate_scores cs
  where cs.candidate_id = rc.id
  order by cs.created_at desc
  limit 1
) cs on true
left join lateral (
  select *
  from public.candidate_classifications cc
  where cc.candidate_id = rc.id
  order by cc.created_at desc
  limit 1
) cc on true;

grant select on public.v_run_candidate_summary to anon, authenticated, service_role;
grant select on public.v_selected_report_candidates to anon, authenticated, service_role;