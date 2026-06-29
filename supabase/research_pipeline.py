from __future__ import annotations

import json
import math
import os
from datetime import UTC, datetime
from typing import Any

from openai import OpenAI

from .client import SupabaseClient
from .research_run_service import addRunEvent, prepareRunCandidates, updateResearchRunStatus
from .review_pipeline import runReviewPipeline
from .steam_store import fetch_app_details, fetch_page_signals, normalize_app_details
from .steam_utils import canonical_steam_url


client = SupabaseClient()

VALID_CLASSIFICATIONS = {
    'direct_comp',
    'adjacent_comp',
    'audience_comp',
    'mechanic_comp',
    'commercial_benchmark',
    'emerging_comp',
    'low_fit',
    'noise',
    'unknown',
}


LLM_CLASSIFICATION_SCHEMA = {
    'name': 'steam_gtm_candidate_classifications',
    'schema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'candidates': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'additionalProperties': False,
                    'properties': {
                        'candidate_id': {'type': 'string'},
                        'classification': {'type': 'string', 'enum': sorted(VALID_CLASSIFICATIONS)},
                        'confidence': {'type': 'number', 'minimum': 0, 'maximum': 1},
                        'direct_fit_score': {'type': 'number', 'minimum': 0, 'maximum': 100},
                        'audience_fit_score': {'type': 'number', 'minimum': 0, 'maximum': 100},
                        'mechanic_fit_score': {'type': 'number', 'minimum': 0, 'maximum': 100},
                        'commercial_benchmark_score': {'type': 'number', 'minimum': 0, 'maximum': 100},
                        'reasoning': {'type': 'string'},
                        'use_for': {'type': 'string'},
                        'do_not_use_for': {'type': 'string'},
                        'strategic_notes': {'type': 'string'},
                        'positioning_notes': {'type': 'string'},
                    },
                    'required': [
                        'candidate_id',
                        'classification',
                        'confidence',
                        'direct_fit_score',
                        'audience_fit_score',
                        'mechanic_fit_score',
                        'commercial_benchmark_score',
                        'reasoning',
                        'use_for',
                        'do_not_use_for',
                        'strategic_notes',
                        'positioning_notes',
                    ],
                },
            },
        },
        'required': ['candidates'],
    },
    'strict': True,
}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def one(table: str, filters: dict[str, str], select: str = '*') -> dict[str, Any] | None:
    rows = client.select(table, select, filters)
    return rows[0] if rows else None


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == '':
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def text_list(items: Any, key: str = 'description') -> list[str]:
    if not isinstance(items, list):
        return []
    values: list[str] = []
    for item in items:
        if isinstance(item, dict) and item.get(key):
            values.append(str(item[key]))
        elif isinstance(item, str):
            values.append(item)
    return values


def normalize_release_status(detail: dict[str, Any]) -> str | None:
    release = detail.get('release_date') or {}
    if isinstance(release, dict) and release.get('coming_soon') is True:
        return 'upcoming'
    if detail.get('success') is False:
        return 'fetch_failed'
    if detail.get('type'):
        return 'released'
    return None


def steam_app_payload(appid: int, detail: dict[str, Any], page_signals: dict[str, Any]) -> dict[str, Any]:
    price = detail.get('price_overview') or {}
    release = detail.get('release_date') or {}
    recommendations = detail.get('recommendations') or {}
    metacritic = detail.get('metacritic') or {}
    basic = page_signals.get('basic_info') or {}
    tags = page_signals.get('tags') or []

    name = detail.get('name') or basic.get('page_title') or f'Steam App {appid}'
    tag_names = [str(tag.get('name')) for tag in tags if isinstance(tag, dict) and tag.get('name')]
    tag_ids = [int(tag.get('tagid')) for tag in tags if isinstance(tag, dict) and str(tag.get('tagid') or '').isdigit()]

    return {
        'appid': appid,
        'name': name,
        'app_type': detail.get('type'),
        'developer': ', '.join(text_list(detail.get('developers'), key='')) if detail.get('developers') else None,
        'publisher': ', '.join(text_list(detail.get('publishers'), key='')) if detail.get('publishers') else None,
        'release_date_text': release.get('date') if isinstance(release, dict) else None,
        'release_status': normalize_release_status(detail),
        'is_free': detail.get('is_free'),
        'price_initial_cents': price.get('initial') if isinstance(price, dict) else None,
        'price_final_cents': price.get('final') if isinstance(price, dict) else None,
        'currency': price.get('currency') if isinstance(price, dict) else None,
        'review_summary': basic.get('review_summary'),
        'recent_review_summary': basic.get('recent_review_summary'),
        'review_count': recommendations.get('total') if isinstance(recommendations, dict) else None,
        'metacritic_score': metacritic.get('score') if isinstance(metacritic, dict) else None,
        'short_description': detail.get('short_description'),
        'genres': text_list(detail.get('genres')),
        'categories': text_list(detail.get('categories')),
        'tags': tag_names,
        'tag_ids': tag_ids,
        'last_fetched_at': utc_now_iso(),
        'raw_appdetails_json': detail,
        'raw_page_signals_json': page_signals,
    }


def snapshot_payload(app_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        'appid': app_payload['appid'],
        'fetch_source': 'steam_pipeline',
        'name': app_payload.get('name'),
        'review_summary': app_payload.get('review_summary'),
        'recent_review_summary': app_payload.get('recent_review_summary'),
        'review_count': app_payload.get('review_count'),
        'price_final_cents': app_payload.get('price_final_cents'),
        'currency': app_payload.get('currency'),
        'release_status': app_payload.get('release_status'),
        'tags': app_payload.get('tags') or [],
        'tag_ids': app_payload.get('tag_ids') or [],
        'raw_json': {
            'appdetails': app_payload.get('raw_appdetails_json') or {},
            'page_signals': app_payload.get('raw_page_signals_json') or {},
        },
    }


def upsert_enriched_steam_app(appid: int, *, country: str = 'us', language: str = 'english') -> dict[str, Any]:
    raw_detail = fetch_app_details(appid, country=country, language=language)
    detail = normalize_app_details(appid, raw_detail)
    try:
        page_signals = fetch_page_signals(appid, country=country, language=language)
    except Exception as exc:
        existing = one('steam_apps', {'appid': f'eq.{appid}'}) or {}
        page_signals = existing.get('raw_page_signals_json') or {'appid': appid, 'error': str(exc), 'tags': [], 'basic_info': {}}

    payload = steam_app_payload(appid, detail, page_signals)
    response = client.insert('steam_apps', payload, upsert=True, on_conflict='appid', returning='representation')
    app = response[0] if isinstance(response, list) else response
    client.insert('steam_app_snapshots', snapshot_payload(payload), returning='minimal')
    return app or payload


def get_run_and_game(run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    run = one('research_runs', {'id': f'eq.{run_id}'})
    if not run:
        raise ValueError(f'Research run {run_id} not found')
    game = one('games', {'id': f"eq.{run['game_id']}"})
    if not game:
        raise ValueError(f"Game {run['game_id']} for run {run_id} not found")
    return run, game


def existing_candidate_by_appid(run_id: str, appid: int) -> dict[str, Any] | None:
    return one('run_candidates', {'run_id': f'eq.{run_id}', 'steam_appid': f'eq.{appid}'})


def add_discovered_candidate(run: dict[str, Any], appid: int, rank: int, source_payload: dict[str, Any]) -> dict[str, Any] | None:
    existing = existing_candidate_by_appid(run['id'], appid)
    app = one('steam_apps', {'appid': f'eq.{appid}'}) or {'name': f'Steam App {appid}'}
    if existing:
        candidate = existing
    else:
        payload = {
            'run_id': run['id'],
            'organization_id': run['organization_id'],
            'steam_appid': appid,
            'title': app.get('name') or f'Steam App {appid}',
            'steam_url': canonical_steam_url(appid),
            'primary_source': 'steam_more_like_this',
            'pipeline_status': 'discovered',
            'discovery_rank': rank,
            'raw_candidate_json': source_payload,
        }
        response = client.insert('run_candidates', payload, returning='representation')
        candidate = response[0] if isinstance(response, list) else response

    if candidate:
        evidence = {
            'candidate_id': candidate['id'],
            'run_id': run['id'],
            'source': 'steam_more_like_this',
            'query': f"seed:{source_payload.get('seed_appid')}",
            'source_rank': rank,
            'source_score': None,
            'evidence_notes': 'Discovered from Steam More Like This carousel.',
            'raw_evidence_json': source_payload,
        }
        client.insert('candidate_discovery_evidence', evidence, returning='minimal')
    return candidate


def enrich_run(run_id: str) -> dict[str, Any]:
    run, game = get_run_and_game(run_id)
    config = run.get('run_config') or {}
    country = config.get('country', 'us')
    language = config.get('language', 'english')
    discovery_limit = as_int(config.get('discovery_max_more_like_this'), 10)

    updateResearchRunStatus(run_id, 'running', current_stage='enrichment')
    addRunEvent(run_id, 'enrichment', 'stage_started', 'Steam enrichment started')

    enriched_appids: set[int] = set()
    seed_appid = game.get('steam_appid')
    if seed_appid:
        upsert_enriched_steam_app(as_int(seed_appid), country=country, language=language)
        enriched_appids.add(as_int(seed_appid))

    candidates = client.select('run_candidates', '*', {'run_id': f'eq.{run_id}', 'order': 'created_at.asc'})
    for candidate in candidates:
        appid = candidate.get('steam_appid')
        if not appid:
            continue
        appid = as_int(appid)
        if appid in enriched_appids:
            continue
        try:
            upsert_enriched_steam_app(appid, country=country, language=language)
            enriched_appids.add(appid)
            if candidate.get('pipeline_status') == 'discovered':
                client.update('run_candidates', {'id': f"eq.{candidate['id']}"}, {'pipeline_status': 'enriched'}, returning='minimal')
        except Exception as exc:
            addRunEvent(run_id, 'enrichment', 'candidate_enrichment_failed', f'Failed to enrich app {appid}', {'error': str(exc)})

    discovered = 0
    if seed_appid and discovery_limit > 0:
        seed_app = one('steam_apps', {'appid': f'eq.{as_int(seed_appid)}'}) or {}
        signals = seed_app.get('raw_page_signals_json') or {}
        for rank, appid in enumerate((signals.get('more_like_this_appids') or [])[:discovery_limit], start=1):
            appid = as_int(appid)
            if not appid or appid == as_int(seed_appid):
                continue
            try:
                if appid not in enriched_appids:
                    upsert_enriched_steam_app(appid, country=country, language=language)
                    enriched_appids.add(appid)
                add_discovered_candidate(run, appid, rank, {'seed_appid': as_int(seed_appid), 'source': 'more_like_this'})
                discovered += 1
            except Exception as exc:
                addRunEvent(run_id, 'discovery', 'more_like_this_candidate_failed', f'Failed to add MLT app {appid}', {'error': str(exc)})

    addRunEvent(run_id, 'enrichment', 'stage_completed', 'Steam enrichment completed', {'enriched_app_count': len(enriched_appids), 'discovered_count': discovered})
    return {'enriched_app_count': len(enriched_appids), 'discovered_count': discovered}


def weighted_tag_score(seed_tags: list[str], candidate_tags: list[str]) -> tuple[int, list[str]]:
    seed_norm = [tag.lower() for tag in seed_tags]
    candidate_norm = {tag.lower() for tag in candidate_tags}
    score = 0
    overlaps: list[str] = []
    for index, tag in enumerate(seed_norm):
        if tag in candidate_norm:
            weight = max(1, 20 - index)
            score += weight
            overlaps.append(seed_tags[index])
    return score, overlaps


def score_run(run_id: str) -> dict[str, Any]:
    run, game = get_run_and_game(run_id)
    config = run.get('run_config') or {}
    min_reviews = as_int(run.get('min_review_count'), 1000)
    shortlist_limit = as_int(config.get('shortlist_limit'), 12)
    scoring_version = config.get('scoring_version') or 'db_weighted_tags_v1'

    updateResearchRunStatus(run_id, 'running', current_stage='scoring')
    addRunEvent(run_id, 'scoring', 'stage_started', 'Candidate scoring started')

    seed_appid = as_int(game.get('steam_appid'))
    seed_app = one('steam_apps', {'appid': f'eq.{seed_appid}'}) if seed_appid else None
    seed_tags = (seed_app or {}).get('tags') or config.get('anchor_tags') or []

    candidates = client.select('run_candidates', '*', {'run_id': f'eq.{run_id}'})
    scored: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get('is_user_excluded'):
            client.update('run_candidates', {'id': f"eq.{candidate['id']}"}, {'pipeline_status': 'excluded_by_user'}, returning='minimal')
            continue
        appid = candidate.get('steam_appid')
        app = one('steam_apps', {'appid': f'eq.{as_int(appid)}'}) if appid else None
        candidate_tags = (app or {}).get('tags') or []
        tag_score, overlaps = weighted_tag_score(seed_tags, candidate_tags)
        review_count = as_int((app or {}).get('review_count'))
        review_volume_score = min(100.0, math.log10(max(review_count, 1)) * 25.0)
        commercial_signal_score = min(100.0, review_volume_score + (10.0 if (app or {}).get('price_final_cents') else 0.0))
        anchor_tag_score = min(100.0, float(tag_score) * 2.5)
        fit_score = min(100.0, anchor_tag_score * 0.7 + commercial_signal_score * 0.3)
        if candidate.get('is_user_required'):
            fit_score = max(fit_score, 75.0)
        if candidate.get('is_benchmark_only'):
            commercial_signal_score = max(commercial_signal_score, 60.0)

        score_payload = {
            'candidate_id': candidate['id'],
            'run_id': run_id,
            'scoring_version': scoring_version,
            'fit_score': round(fit_score, 4),
            'tag_overlap_score': round(min(100.0, float(tag_score) * 2.0), 4),
            'anchor_tag_score': round(anchor_tag_score, 4),
            'commercial_signal_score': round(commercial_signal_score, 4),
            'review_volume_score': round(review_volume_score, 4),
            'overlap_count': len(overlaps),
            'overlapping_tags': overlaps,
            'overlapping_anchor_tags': [tag for tag in overlaps if tag in (config.get('anchor_tags') or seed_tags[:5])],
            'recommendation_count': review_count,
            'review_count': review_count,
            'metacritic_score': (app or {}).get('metacritic_score'),
            'score_inputs': {'seed_tags': seed_tags, 'candidate_tags': candidate_tags, 'min_reviews': min_reviews},
            'score_details': {'rule': 'weighted tag overlap plus commercial/review signal'},
        }
        client.insert('candidate_scores', score_payload, upsert=True, on_conflict='candidate_id,scoring_version', returning='minimal')
        scored.append({'candidate': candidate, 'score': fit_score, 'commercial': commercial_signal_score})

    scored.sort(key=lambda item: (item['candidate'].get('is_user_required') is True, item['score'], item['commercial']), reverse=True)
    for rank, item in enumerate(scored, start=1):
        candidate = item['candidate']
        selected = rank <= shortlist_limit or candidate.get('is_user_required') or candidate.get('is_benchmark_only')
        updates = {
            'pipeline_status': 'selected_for_report' if selected else 'scored',
            'is_shortlisted': rank <= shortlist_limit or candidate.get('is_user_required'),
            'is_selected_for_report': selected,
            'final_rank': rank,
        }
        client.update('run_candidates', {'id': f"eq.{candidate['id']}"}, updates, returning='minimal')

    addRunEvent(run_id, 'scoring', 'stage_completed', 'Candidate scoring completed', {'scored_count': len(scored), 'shortlist_limit': shortlist_limit})
    return {'scored_count': len(scored), 'shortlist_limit': shortlist_limit}


def classify_from_scores(summary: dict[str, Any]) -> dict[str, Any]:
    fit = float(summary.get('fit_score') or 0)
    review_count = as_int(summary.get('review_count'))
    overlap_count = as_int(summary.get('overlap_count'))
    if summary.get('is_user_excluded'):
        classification = 'noise'
        reason = 'User explicitly excluded this candidate.'
    elif summary.get('is_benchmark_only'):
        classification = 'commercial_benchmark'
        reason = 'Marked benchmark-only by the user; useful for commercial comparison.'
    elif fit >= 75 and overlap_count >= 2:
        classification = 'direct_comp'
        reason = 'High fit score with meaningful tag overlap and/or user-required status.'
    elif fit >= 55:
        classification = 'adjacent_comp'
        reason = 'Moderate fit with enough overlap to inform positioning.'
    elif review_count >= 5000:
        classification = 'commercial_benchmark'
        reason = 'Large commercial signal, even if direct fit is moderate or low.'
    elif fit >= 35:
        classification = 'mechanic_comp'
        reason = 'Some overlap suggests it may be useful for mechanics or feature reference.'
    else:
        classification = 'low_fit'
        reason = 'Low current fit score and limited shared signal.'
    return {'classification': classification, 'reason': reason}


def openai_model_name() -> str:
    return os.getenv('STEAM_GTM_OPENAI_MODEL') or os.getenv('OPENAI_MODEL') or 'gpt-4o-mini'


def llm_is_configured() -> bool:
    return bool(os.getenv('OPENAI_API_KEY'))


def compact_candidate_for_llm(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'candidate_id': row.get('candidate_id'),
        'title': row.get('title'),
        'steam_appid': row.get('steam_appid'),
        'primary_source': row.get('primary_source'),
        'user_control_type': row.get('user_control_type'),
        'is_user_required': row.get('is_user_required'),
        'is_benchmark_only': row.get('is_benchmark_only'),
        'review_count': row.get('review_count'),
        'review_summary': row.get('review_summary'),
        'release_status': row.get('release_status'),
        'tags': (row.get('tags') or [])[:20],
        'fit_score': row.get('fit_score'),
        'overlap_count': row.get('overlap_count'),
        'overlapping_tags': row.get('overlapping_tags') or [],
        'overlapping_anchor_tags': row.get('overlapping_anchor_tags') or [],
    }


def llm_classify_rows(rows: list[dict[str, Any]], model: str) -> dict[str, dict[str, Any]]:
    system_prompt = """
You are a senior go-to-market strategist for PC/Steam games.

Classify each candidate game for competitor research. Do not browse. Use only the structured data.

Classification guidance:
- direct_comp: close audience, core fantasy, tone, and gameplay loop.
- adjacent_comp: related audience/systems, but not the same core promise.
- audience_comp: likely shares audience, but gameplay/core promise differs.
- mechanic_comp: useful for mechanics/systems reference, not overall positioning.
- commercial_benchmark: useful for pricing/scope/review-volume benchmarks.
- emerging_comp: promising but early/upcoming/low data.
- low_fit: weak relevance.
- noise: should be ignored or user-excluded.
- unknown: not enough evidence.

Important: user-required games should not automatically become direct comps; benchmark-only games should usually be commercial_benchmark unless they are also truly direct.
Keep reasoning concise and practical.
""".strip()
    payload = {'candidates': [compact_candidate_for_llm(row) for row in rows]}
    response = OpenAI().responses.create(
        model=model,
        input=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
        ],
        text={
            'format': {
                'type': 'json_schema',
                'name': LLM_CLASSIFICATION_SCHEMA['name'],
                'schema': LLM_CLASSIFICATION_SCHEMA['schema'],
                'strict': True,
            }
        },
    )
    parsed = json.loads(response.output_text)
    return {str(item['candidate_id']): item for item in parsed.get('candidates') or []}


def classify_run(rule_id: str, run_id: str) -> dict[str, Any]:
    updateResearchRunStatus(run_id, 'running', current_stage='classification')
    addRunEvent(run_id, 'classification', 'stage_started', 'Candidate classification started')
    rows = client.select('v_run_candidate_summary', '*', {'run_id': f'eq.{run_id}'})
    model = openai_model_name()
    prompt_version = rule_id
    model_version = 'rule_based'
    llm_results: dict[str, dict[str, Any]] = {}
    if llm_is_configured() and rows:
        try:
            llm_results = llm_classify_rows(rows, model)
            missing_rows = [row for row in rows if str(row['candidate_id']) not in llm_results]
            for missing_row in missing_rows:
                try:
                    llm_results.update(llm_classify_rows([missing_row], model))
                except Exception as retry_exc:
                    addRunEvent(
                        run_id,
                        'classification',
                        'llm_single_candidate_classification_failed',
                        f"OpenAI single-candidate retry failed for {missing_row.get('title')}",
                        {'model': model, 'candidate_id': missing_row.get('candidate_id'), 'error': str(retry_exc)},
                    )
            prompt_version = 'llm_comp_classification_v1'
            model_version = model
            addRunEvent(run_id, 'classification', 'llm_classification_completed', 'OpenAI classification completed', {'model': model, 'classified_count': len(llm_results)})
        except Exception as exc:
            addRunEvent(run_id, 'classification', 'llm_classification_failed', 'OpenAI classification failed; falling back to rule-based classification', {'model': model, 'error': str(exc)})

    count = 0
    for row in rows:
        llm_result = llm_results.get(str(row['candidate_id']))
        if llm_result:
            result = {
                'classification': llm_result.get('classification') if llm_result.get('classification') in VALID_CLASSIFICATIONS else 'unknown',
                'reason': llm_result.get('reasoning') or '',
                'confidence': llm_result.get('confidence'),
                'direct_fit_score': llm_result.get('direct_fit_score'),
                'audience_fit_score': llm_result.get('audience_fit_score'),
                'mechanic_fit_score': llm_result.get('mechanic_fit_score'),
                'commercial_benchmark_score': llm_result.get('commercial_benchmark_score'),
                'use_for': llm_result.get('use_for'),
                'do_not_use_for': llm_result.get('do_not_use_for'),
                'strategic_notes': llm_result.get('strategic_notes'),
                'positioning_notes': llm_result.get('positioning_notes'),
            }
        else:
            result = classify_from_scores(row)
        payload = {
            'candidate_id': row['candidate_id'],
            'run_id': run_id,
            'classification': result['classification'],
            'confidence': result.get('confidence') if result.get('confidence') is not None else 0.65,
            'direct_fit_score': result.get('direct_fit_score') if result.get('direct_fit_score') is not None else row.get('fit_score') or 0,
            'audience_fit_score': result.get('audience_fit_score') if result.get('audience_fit_score') is not None else row.get('fit_score') or 0,
            'mechanic_fit_score': result.get('mechanic_fit_score') if result.get('mechanic_fit_score') is not None else min(100, float(row.get('fit_score') or 0) + 10),
            'commercial_benchmark_score': result.get('commercial_benchmark_score') if result.get('commercial_benchmark_score') is not None else min(100, as_int(row.get('review_count')) / 1000),
            'reasoning': result['reason'],
            'use_for': result.get('use_for') or 'Initial automated triage for competitor research.',
            'do_not_use_for': result.get('do_not_use_for') or 'Do not treat as final strategic judgment without review.',
            'strategic_notes': result.get('strategic_notes'),
            'positioning_notes': result.get('positioning_notes'),
            'prompt_version': prompt_version,
            'model_version': model_version if llm_result else 'rule_based',
            'llm_input_json': row,
            'llm_output_json': llm_result or result,
        }
        existing = one(
            'candidate_classifications',
            {'candidate_id': f"eq.{row['candidate_id']}", 'prompt_version': f'eq.{payload["prompt_version"]}'},
        )
        if existing:
            client.update('candidate_classifications', {'id': f"eq.{existing['id']}"}, payload, returning='minimal')
        else:
            client.insert('candidate_classifications', payload, returning='minimal')
        if row.get('pipeline_status') != 'excluded_by_user':
            client.update('run_candidates', {'id': f"eq.{row['candidate_id']}"}, {'pipeline_status': 'classified'}, returning='minimal')
        count += 1
    addRunEvent(run_id, 'classification', 'stage_completed', 'Candidate classification completed', {'classified_count': count, 'model_version': model_version, 'prompt_version': prompt_version})
    return {'classified_count': count, 'model_version': model_version, 'prompt_version': prompt_version}


def generate_competitor_report(run_id: str) -> dict[str, Any]:
    run, _game = get_run_and_game(run_id)
    updateResearchRunStatus(run_id, 'running', current_stage='report_generation')
    rows = client.select('v_run_candidate_summary', '*', {'run_id': f'eq.{run_id}', 'order': 'fit_score.desc.nullslast'})
    selected = [row for row in rows if row.get('is_selected_for_report') and not row.get('is_user_excluded')]
    lines = [f"# Competitor Research Report: {run.get('name') or run_id}", '', '## Executive Summary', '']
    lines.append(f'Generated from {len(rows)} candidates; {len(selected)} selected for report.')
    lines.append('')
    lines.append('## Selected Candidates')
    lines.append('')
    lines.append('| Game | Classification | Fit | Reviews | Why / Use |')
    lines.append('|---|---|---:|---:|---|')
    for row in selected:
        url = row.get('steam_url') or ''
        game = f"[{row.get('title')}]({url})" if url else row.get('title')
        lines.append(
            f"| {game} | {row.get('classification') or ''} | {row.get('fit_score') or ''} | {row.get('review_count') or 0} | {row.get('reasoning') or ''} |"
        )
    lines.append('')
    lines.append('## Candidate Notes')
    lines.append('')
    for row in rows:
        lines.append(f"- **{row.get('title')}** — status `{row.get('pipeline_status')}`, classification `{row.get('classification') or 'unclassified'}`.")

    markdown = '\n'.join(lines)
    payload = {
        'run_id': run_id,
        'organization_id': run['organization_id'],
        'report_type': 'competitor_report',
        'title': f"Competitor Research Report: {run.get('name') or run_id}",
        'content_md': markdown,
        'report_json': {'selected_candidate_count': len(selected), 'candidate_count': len(rows), 'rows': rows},
        'generated_by': 'research_pipeline.generate_competitor_report',
        'template_version': 'db_markdown_v1',
    }
    response = client.insert('reports', payload, returning='representation')
    report = response[0] if isinstance(response, list) else response
    addRunEvent(run_id, 'report_generation', 'stage_completed', 'Competitor report generated', {'report_id': report.get('id') if report else None})
    return {'report_id': report.get('id') if report else None, 'selected_candidate_count': len(selected), 'candidate_count': len(rows)}


def runResearchPipeline(run_id: str) -> dict[str, Any]:
    started = utc_now_iso()
    addRunEvent(run_id, 'intake', 'pipeline_started', 'Full research pipeline started')
    try:
        prepare_result = prepareRunCandidates(run_id)
        enrich_result = enrich_run(run_id)
        score_result = score_run(run_id)
        classify_result = classify_run('rule_based_v1', run_id)
        report_result = generate_competitor_report(run_id)
        review_result = runReviewPipeline(run_id)
        client.update(
            'research_runs',
            {'id': f'eq.{run_id}'},
            {'status': 'completed', 'current_stage': 'completed', 'started_at': started, 'completed_at': utc_now_iso(), 'failure_message': None},
            returning='minimal',
        )
        addRunEvent(run_id, 'completed', 'pipeline_completed', 'Full research pipeline completed')
        return {
            'status': 'completed',
            'prepare': prepare_result,
            'enrichment': enrich_result,
            'scoring': score_result,
            'classification': classify_result,
            'report': report_result,
            'reviews': review_result,
        }
    except Exception as exc:
        client.update(
            'research_runs',
            {'id': f'eq.{run_id}'},
            {'status': 'failed', 'failure_message': str(exc), 'failed_at': utc_now_iso()},
            returning='minimal',
        )
        addRunEvent(run_id, 'completed', 'pipeline_failed', 'Full research pipeline failed', {'error': str(exc)})
        raise
