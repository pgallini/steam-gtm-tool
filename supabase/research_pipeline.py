from __future__ import annotations

import json
import html
import math
import os
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from bs4 import BeautifulSoup
from openai import OpenAI

from .client import SupabaseClient
from .pipeline_logging import log_step, log_step_event
from .research_run_service import addRunEvent, addRunProgressEvent, markCandidateSetChangedAfterApproval, prepareRunCandidates, updateResearchRunStatus
from .review_pipeline import runReviewPipeline
from .comp_classification import classify_comp_metadata
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


DISCOVERY_STRATEGY_SCHEMA = {
    'name': 'steam_tag_discovery_strategy',
    'schema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'anchor_tags': {'type': 'array', 'items': {'type': 'string'}},
            'supporting_tags': {'type': 'array', 'items': {'type': 'string'}},
            'broad_tags': {'type': 'array', 'items': {'type': 'string'}},
            'search_queries': {'type': 'array', 'items': {'type': 'string'}},
            'reasoning_summary': {'type': 'string'},
        },
        'required': ['anchor_tags', 'supporting_tags', 'broad_tags', 'search_queries', 'reasoning_summary'],
    },
    'strict': True,
}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_model_json(output_text: str, *, context: str) -> dict[str, Any]:
    text = (output_text or '').strip()
    if not text:
        raise ValueError(f'{context} returned empty JSON output')
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start_candidates = [index for index in (text.find('{'), text.find('[')) if index != -1]
        if not start_candidates:
            raise
        start = min(start_candidates)
        end = max(text.rfind('}'), text.rfind(']'))
        if end <= start:
            raise
        parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError(f'{context} returned non-object JSON')
    return parsed


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


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == '':
        return default
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'y', 'on'}
    return bool(value)
 
 
def parse_iso_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
 
 
def is_cached_steam_app_fresh(app: dict[str, Any], *, country: str, language: str, stale_after_days: int = 7) -> bool:
    if not app:
        return False
    if app.get('fetch_status') != 'enriched':
        return False
    if str(app.get('country_code', '')).lower() != country.lower():
        return False
    if str(app.get('language_code', '')).lower() != language.lower():
        return False
    last_fetched = app.get('last_successfully_fetched_at') or app.get('last_fetched_at')
    if not last_fetched:
        return False
    fetched_at = parse_iso_timestamp(last_fetched)
    if not fetched_at:
        return False
    return datetime.now(UTC) - fetched_at <= timedelta(days=stale_after_days)
 
 
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


def steam_app_payload(appid: int, detail: dict[str, Any], page_signals: dict[str, Any], *, country: str, language: str) -> dict[str, Any]:
    price = detail.get('price_overview') or {}
    release = detail.get('release_date') or {}
    recommendations = detail.get('recommendations') or {}
    metacritic = detail.get('metacritic') or {}
    basic = page_signals.get('basic_info') or {}
    tags = page_signals.get('tags') or []

    name = detail.get('name') or basic.get('page_title') or f'Steam App {appid}'
    tag_names = [str(tag.get('name')) for tag in tags if isinstance(tag, dict) and tag.get('name')]
    tag_ids = [int(tag.get('tagid')) for tag in tags if isinstance(tag, dict) and str(tag.get('tagid') or '').isdigit()]

    platforms = detail.get('platforms') or {}
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
        'discount_percent': price.get('discount_percent') if isinstance(price, dict) else None,
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
        'last_successfully_fetched_at': utc_now_iso(),
        'fetch_status': 'enriched',
        'is_available': bool(detail.get('success')),
        'header_image_url': detail.get('header_image'),
        'supports_windows': bool(platforms.get('windows')),
        'supports_mac': bool(platforms.get('mac')),
        'supports_linux': bool(platforms.get('linux')),
        'country_code': country,
        'language_code': language,
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


def upsert_enriched_steam_app(appid: int, *, country: str = 'us', language: str = 'english', run_id: str | None = None) -> dict[str, Any]:
    existing = one('steam_apps', {'appid': f'eq.{appid}'}) or {}
    if is_cached_steam_app_fresh(existing, country=country, language=language):
        last_timestamp = existing.get('last_successfully_fetched_at') or existing.get('last_fetched_at')
        parsed_timestamp = parse_iso_timestamp(last_timestamp)
        cache_age_days = (datetime.now(UTC) - parsed_timestamp).days if parsed_timestamp else None
        log_step(
            'steam_cache',
            run_id=run_id,
            message='Using fresh cached Steam app details',
            appid=appid,
            data_source='cache',
            last_successfully_fetched_at=existing.get('last_successfully_fetched_at'),
            cache_age_days=cache_age_days,
        )
        return existing
 
    raw_detail = fetch_app_details(appid, country=country, language=language, run_id=run_id)
    detail = normalize_app_details(appid, raw_detail)
    if not detail.get('success') and existing:
        last_timestamp = existing.get('last_successfully_fetched_at') or existing.get('last_fetched_at')
        parsed_timestamp = parse_iso_timestamp(last_timestamp)
        cache_age_days = (datetime.now(UTC) - parsed_timestamp).days if parsed_timestamp else None
        log_step(
            'steam_cache',
            run_id=run_id,
            message='Falling back to stale cached Steam app details after live fetch failed',
            appid=appid,
            data_source='stale_cache_fallback',
            last_successfully_fetched_at=existing.get('last_successfully_fetched_at'),
            cache_age_days=cache_age_days,
        )
        return existing
 
    try:
        page_signals = fetch_page_signals(appid, country=country, language=language, run_id=run_id)
    except Exception as exc:
        page_signals = existing.get('raw_page_signals_json') or {'appid': appid, 'error': str(exc), 'tags': [], 'basic_info': {}}
 
    payload = steam_app_payload(appid, detail, page_signals, country=country, language=language)
    response = client.insert('steam_apps', payload, upsert=True, on_conflict='appid', returning='representation')
    app = response[0] if isinstance(response, list) else response
    client.insert('steam_app_snapshots', snapshot_payload(payload), returning='minimal')
    return app or payload


def seed_tags_from_signals(signals: dict[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, tag in enumerate(signals.get('tags') or [], start=1):
        if isinstance(tag, str):
            normalized.append({'name': tag, 'rank': index, 'tagid': None, 'count': None, 'browseable': None})
        elif isinstance(tag, dict) and tag.get('name'):
            normalized.append(
                {
                    'name': str(tag.get('name')),
                    'rank': index,
                    'tagid': tag.get('tagid'),
                    'count': tag.get('count'),
                    'browseable': tag.get('browseable'),
                }
            )
    return normalized


def build_fallback_discovery_strategy(seed_tags: list[dict[str, Any]], max_queries: int) -> dict[str, Any]:
    names = [str(tag['name']) for tag in seed_tags if tag.get('name')]
    anchor_tags = names[:4]
    queries = list(anchor_tags)
    if len(anchor_tags) >= 2:
        queries.append(f'{anchor_tags[0]} {anchor_tags[1]}')
    if len(anchor_tags) >= 3:
        queries.append(f'{anchor_tags[0]} {anchor_tags[2]}')
    if len(anchor_tags) >= 4:
        queries.append(f'{anchor_tags[0]} {anchor_tags[1]} {anchor_tags[3]}')
    return {
        'anchor_tags': anchor_tags,
        'supporting_tags': names[4:10],
        'broad_tags': [],
        'search_queries': queries[:max_queries],
        'reasoning_summary': 'Fallback strategy used because OpenAI discovery strategy was unavailable.',
    }


def get_llm_discovery_strategy(
    *,
    model: str,
    seed_name: str,
    seed_appid: int,
    seed_tags: list[dict[str, Any]],
    max_anchor_tags: int,
    max_supporting_tags: int,
    max_queries: int,
) -> dict[str, Any]:
    if not llm_is_configured():
        return build_fallback_discovery_strategy(seed_tags, max_queries)

    payload = {
        'task': 'Create a Steam competitor discovery strategy from the seed game Steam tags.',
        'seed_game': {'name': seed_name, 'appid': seed_appid, 'steam_tags': seed_tags},
        'instructions': [
            'Classify tags based on strategic usefulness for competitor discovery.',
            'Anchor tags should be distinctive gameplay, audience, genre, or fantasy signals.',
            'Supporting tags add useful context but should usually be combined with anchors.',
            'Broad tags are too generic to drive discovery by themselves.',
            'Do not invent tags. Use anchor/supporting/broad tags only from the supplied Steam tags.',
            'Search queries may combine supplied tags into concise Steam-search-friendly phrases.',
            'Prefer queries that will find strategically similar games, not only thematically similar games.',
            'Avoid relying only on mood/aesthetic tags if mechanical tags are present.',
        ],
        'limits': {
            'max_anchor_tags': max_anchor_tags,
            'max_supporting_tags': max_supporting_tags,
            'max_search_queries': max_queries,
        },
    }
    response = OpenAI().responses.create(
        model=model,
        input=[
            {'role': 'system', 'content': 'You are a Steam market research strategist who discovers comparable PC games from Steam tag profiles.'},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
        ],
        text={
            'format': {
                'type': 'json_schema',
                'name': DISCOVERY_STRATEGY_SCHEMA['name'],
                'schema': DISCOVERY_STRATEGY_SCHEMA['schema'],
                'strict': True,
            }
        },
    )
    strategy = parse_model_json(response.output_text, context='Discovery strategy')
    strategy['anchor_tags'] = (strategy.get('anchor_tags') or [])[:max_anchor_tags]
    strategy['supporting_tags'] = (strategy.get('supporting_tags') or [])[:max_supporting_tags]
    strategy['search_queries'] = (strategy.get('search_queries') or [])[:max_queries]
    return strategy


def add_default_anchor_queries(strategy: dict[str, Any], max_queries: int) -> list[str]:
    queries: list[str] = []
    for tag in strategy.get('anchor_tags') or []:
        if tag not in queries:
            queries.append(tag)
    for query in strategy.get('search_queries') or []:
        if query not in queries:
            queries.append(query)
    return queries[:max_queries]


def tag_id_for_name(seed_tags: list[dict[str, Any]], tag_name: str) -> int | None:
    for tag in seed_tags:
        if tag.get('name') == tag_name and str(tag.get('tagid') or '').isdigit():
            return int(tag['tagid'])
    return None


def _format_supabase_array_contains(values: list[Any]) -> str:
    cleaned = [str(value) for value in values if value is not None]
    return '{' + ','.join(cleaned) + '}' if cleaned else '{}'


def _search_cached_steam_by_tag_ids(tag_ids: list[int], *, max_results: int) -> list[dict[str, Any]]:
    """Search the local steam_apps catalog for matches by Steam tag IDs."""
    cleaned_ids = [int(tag_id) for tag_id in tag_ids if isinstance(tag_id, int) and tag_id > 0]
    if not cleaned_ids:
        return []
    filters = {
        'tag_ids': f'cs.{_format_supabase_array_contains(cleaned_ids)}',
        'or': '(app_type.eq.game,app_type.is.null)',
        'order': 'review_count.desc.nullslast,appid.asc',
        'limit': str(max_results),
    }
    rows = client.select('steam_apps', 'appid,name,steam_url', filters)
    return [
        {
            'appid': as_int(row.get('appid')),
            'title': row.get('name'),
            'source_url': row.get('steam_url'),
        }
        for row in rows
        if row.get('appid')
    ]


def _search_cached_steam_by_text(query: str, *, max_results: int) -> list[dict[str, Any]]:
    """Search the local steam_apps catalog by name and tags using cached data only."""
    query_text = (query or '').strip()
    if not query_text:
        return []
    escaped = query_text.replace(')', '').replace('(', '').replace('{', '').replace('}', '').replace(',', ' ').replace('"', '').replace("'", '')
    name_rows = client.select(
        'steam_apps',
        'appid,name,steam_url',
        {
            'name': f'ilike.*{escaped}*',
            'or': '(app_type.eq.game,app_type.is.null)',
            'order': 'review_count.desc.nullslast,appid.asc',
            'limit': str(max_results),
        },
    )
    tag_rows = client.select(
        'steam_apps',
        'appid,name,steam_url',
        {
            'tags': f'cs.{{{escaped}}}',
            'or': '(app_type.eq.game,app_type.is.null)',
            'order': 'review_count.desc.nullslast,appid.asc',
            'limit': str(max_results),
        },
    )
    combined: dict[int, dict[str, Any]] = {}
    for row in name_rows + tag_rows:
        appid = as_int(row.get('appid'))
        if not appid:
            continue
        combined[appid] = {
            'appid': appid,
            'title': row.get('name'),
            'source_url': row.get('steam_url'),
        }
    return list(combined.values())[:max_results]


def search_steam_by_tag_ids(tag_ids: list[int], *, max_results: int) -> list[dict[str, Any]]:
    return _search_cached_steam_by_tag_ids(tag_ids, max_results=max_results)


def search_steam_text(query: str, *, max_results: int, country: str, language: str) -> list[dict[str, Any]]:
    return _search_cached_steam_by_text(query, max_results=max_results)


def tag_id_for_name(seed_tags: list[dict[str, Any]], tag_name: str) -> int | None:
    for tag in seed_tags:
        if tag.get('name') == tag_name and str(tag.get('tagid') or '').isdigit():
            return int(tag['tagid'])
    return None




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


def persist_comp_classification(candidate: dict[str, Any], app: dict[str, Any] | None) -> dict[str, Any]:
    classification = classify_comp_metadata(candidate, app)
    client.update('run_candidates', {'id': f"eq.{candidate['id']}"}, classification, returning='minimal')
    return classification


def add_discovered_candidate(run: dict[str, Any], appid: int, rank: int, source_payload: dict[str, Any]) -> dict[str, Any] | None:
    existing = existing_candidate_by_appid(run['id'], appid)
    app = one('steam_apps', {'appid': f'eq.{appid}'}) or {'name': f'Steam App {appid}'}
    source = source_payload.get('candidate_source') or 'steam_more_like_this'
    if existing:
        candidate = existing
    else:
        payload = {
            'run_id': run['id'],
            'organization_id': run['organization_id'],
            'steam_appid': appid,
            'title': app.get('name') or f'Steam App {appid}',
            'steam_url': canonical_steam_url(appid),
            'primary_source': source,
            'pipeline_status': 'discovered',
            'discovery_rank': rank,
            'raw_candidate_json': source_payload,
        }
        response = client.insert('run_candidates', payload, returning='representation')
        candidate = response[0] if isinstance(response, list) else response

    if candidate:
        log_step('02_discover_candidates', run_id=run['id'], message='Added discovered candidate', appid=appid, source=source, rank=rank, title=candidate.get('title'))
        evidence = {
            'candidate_id': candidate['id'],
            'run_id': run['id'],
            'source': source,
            'query': source_payload.get('query') or ', '.join(source_payload.get('tags') or []) or f"seed:{source_payload.get('seed_appid')}",
            'source_rank': rank,
            'source_score': None,
            'evidence_notes': source_payload.get('evidence_notes') or 'Discovered by automated Steam candidate discovery.',
            'raw_evidence_json': source_payload,
        }
        client.insert('candidate_discovery_evidence', evidence, returning='minimal')
        persist_comp_classification(candidate, app)
    return candidate


def add_tag_discovery_results(
    run: dict[str, Any],
    *,
    seed_tags: list[dict[str, Any]],
    tag_names: list[str],
    rank_offset: int,
    max_results: int,
    source: str,
    country: str,
    language: str,
) -> int:
    tag_ids: list[int] = []
    for tag_name in tag_names:
        tag_id = tag_id_for_name(seed_tags, tag_name)
        if tag_id is None:
            addRunEvent(run['id'], 'discovery', 'tag_id_missing', f'No Steam tag ID found for {tag_name}', {'tag': tag_name})
            return 0
        tag_ids.append(tag_id)

    added = 0
    for rank, result in enumerate(search_steam_by_tag_ids(tag_ids, max_results=max_results), start=rank_offset):
        appid = as_int(result.get('appid'))
        if not appid:
            continue
        try:
            upsert_enriched_steam_app(appid, country=country, language=language)
            add_discovered_candidate(
                run,
                appid,
                rank,
                {
                    'candidate_source': source,
                    'source_method': 'steam_tag_id_search',
                    'tags': tag_names,
                    'tag_ids': tag_ids,
                    'source_url': result.get('source_url'),
                    'title': result.get('title'),
                    'evidence_notes': 'Discovered from Steam tag ID search.',
                },
            )
            added += 1
        except Exception as exc:
            addRunEvent(run['id'], 'discovery', 'tag_candidate_failed', f'Failed to add tag-discovered app {appid}', {'error': str(exc), 'tags': tag_names})
    return added


def add_text_discovery_results(
    run: dict[str, Any],
    *,
    query: str,
    rank_offset: int,
    max_results: int,
    country: str,
    language: str,
) -> int:
    added = 0
    for rank, result in enumerate(search_steam_text(query, max_results=max_results, country=country, language=language), start=rank_offset):
        appid = as_int(result.get('appid'))
        if not appid:
            continue
        try:
            upsert_enriched_steam_app(appid, country=country, language=language)
            add_discovered_candidate(
                run,
                appid,
                rank,
                {
                    'candidate_source': 'tag_search',
                    'source_method': 'steam_text_search',
                    'query': query,
                    'source_url': result.get('source_url'),
                    'title': result.get('title'),
                    'evidence_notes': 'Discovered from Steam text search generated by the discovery strategy.',
                },
            )
            added += 1
        except Exception as exc:
            addRunEvent(run['id'], 'discovery', 'text_search_candidate_failed', f'Failed to add text-search app {appid}', {'error': str(exc), 'query': query})
    return added

def extract_more_like_this_appids(soup: BeautifulSoup) -> list[int]:
    appids: list[int] = []
    for element in soup.select('[data-featuretarget="storeitems-carousel"]'):
        raw_props = element.get('data-props')
        if not raw_props:
            continue
        try:
            props = json.loads(html.unescape(raw_props))
        except Exception:
            continue
        if props.get('title', '').lower() != 'more like this':
            continue
        for appid in props.get('appIDs', []):
            if isinstance(appid, int):
                appids.append(appid)
    return appids


def enrich_run(run_id: str) -> dict[str, Any]:
    require_prior_stage(run_id, 'enrichment', 'discovery', 'Known Games & Guidance')
    run, game = get_run_and_game(run_id)
    config = run.get('run_config') or {}
    country = config.get('country', 'us')
    language = config.get('language', 'english')
    discovery_limit = as_int(config.get('discovery_max_more_like_this'), 50)
    max_anchor_tags = as_int(config.get('discovery_max_anchor_tags'), 6)
    max_supporting_tags = as_int(config.get('discovery_max_supporting_tags'), 8)
    max_search_queries = as_int(config.get('discovery_max_search_queries'), 12)
    max_results_per_search = as_int(config.get('discovery_max_results_per_search'), 50)
    discovery_sleep = float(config.get('discovery_sleep_seconds') or 0)

    updateResearchRunStatus(run_id, 'running', current_stage='enrichment')
    addRunEvent(run_id, 'enrichment', 'stage_started', 'Steam enrichment started')
    log_step_event('02_discover_candidates', 'started', run_id=run_id, message='Started candidate discovery and enrichment', seed_appid=game.get('steam_appid'))

    enriched_appids: set[int] = set()
    games_processed = 0
    seed_appid = game.get('steam_appid')
    if seed_appid:
        upsert_enriched_steam_app(as_int(seed_appid), country=country, language=language, run_id=run_id)
        enriched_appids.add(as_int(seed_appid))
        games_processed += 1
        addRunProgressEvent(
            run_id,
            'enrichment',
            games_processed,
            unit='games',
            message='Discovering and enriching candidates',
            details={'appid': as_int(seed_appid), 'title': game.get('title')},
        )

    candidates = client.select('run_candidates', '*', {'run_id': f'eq.{run_id}', 'order': 'created_at.asc'})
    for candidate in candidates:
        appid = candidate.get('steam_appid')
        if not appid:
            continue
        appid = as_int(appid)
        if appid in enriched_appids:
            continue
        try:
            upsert_enriched_steam_app(appid, country=country, language=language, run_id=run_id)
            enriched_appids.add(appid)
            app = one('steam_apps', {'appid': f'eq.{appid}'}) or {'appid': appid}
            persist_comp_classification(candidate, app)
            if candidate.get('pipeline_status') == 'discovered':
                client.update('run_candidates', {'id': f"eq.{candidate['id']}"}, {'pipeline_status': 'enriched'}, returning='minimal')
            games_processed += 1
            addRunProgressEvent(
                run_id,
                'enrichment',
                games_processed,
                unit='games',
                message='Discovering and enriching candidates',
                details={'candidate_id': candidate['id'], 'appid': appid, 'title': candidate.get('title')},
            )
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
                    upsert_enriched_steam_app(appid, country=country, language=language, run_id=run_id)
                    enriched_appids.add(appid)
                add_discovered_candidate(
                    run,
                    appid,
                    rank,
                    {
                        'seed_appid': as_int(seed_appid),
                        'source': 'more_like_this',
                        'candidate_source': 'steam_more_like_this',
                        'evidence_notes': 'Discovered from Steam More Like This carousel.',
                    },
                )
                discovered += 1
                games_processed += 1
                addRunProgressEvent(
                    run_id,
                    'enrichment',
                    games_processed,
                    unit='games',
                    message='Discovering and enriching candidates',
                    details={'appid': appid, 'source': 'more_like_this', 'rank': rank},
                )
            except Exception as exc:
                addRunEvent(run_id, 'discovery', 'more_like_this_candidate_failed', f'Failed to add MLT app {appid}', {'error': str(exc)})

        seed_tags = seed_tags_from_signals(signals)
        try:
            strategy = get_llm_discovery_strategy(
                model=openai_model_name(),
                seed_name=seed_app.get('name') or game.get('title') or f'Steam App {seed_appid}',
                seed_appid=as_int(seed_appid),
                seed_tags=seed_tags,
                max_anchor_tags=max_anchor_tags,
                max_supporting_tags=max_supporting_tags,
                max_queries=max_search_queries,
            )
        except Exception as exc:
            addRunEvent(run_id, 'discovery', 'llm_discovery_strategy_failed', 'OpenAI discovery strategy failed; falling back to seed tag order', {'error': str(exc)})
            strategy = build_fallback_discovery_strategy(seed_tags, max_search_queries)

        client.update(
            'research_runs',
            {'id': f'eq.{run_id}'},
            {
                'discovery_strategy_version': 'llm_tag_strategy_v1' if llm_is_configured() else 'fallback_seed_tags_v1',
                'run_config': {**config, 'discovery_strategy': strategy},
            },
            returning='minimal',
        )
        addRunEvent(run_id, 'discovery', 'discovery_strategy_ready', 'Candidate discovery strategy ready', strategy)

        rank_offset = discovery_limit + 1
        anchor_tags = strategy.get('anchor_tags') or []
        for tag_name in anchor_tags:
            added_count = add_tag_discovery_results(
                run,
                seed_tags=seed_tags,
                tag_names=[tag_name],
                rank_offset=rank_offset,
                max_results=max_results_per_search,
                source='tag_search',
                country=country,
                language=language,
            )
            discovered += added_count
            games_processed += added_count
            if added_count:
                addRunProgressEvent(
                    run_id,
                    'enrichment',
                    games_processed,
                    unit='games',
                    message='Discovering and enriching candidates',
                    details={'query': tag_name, 'source': 'tag_search', 'added_count': added_count, 'discovered_count': discovered},
                )
            rank_offset += max_results_per_search
            if discovery_sleep > 0:
                time.sleep(discovery_sleep)

        for left_index in range(len(anchor_tags)):
            for right_index in range(left_index + 1, len(anchor_tags)):
                tag_pair = [anchor_tags[left_index], anchor_tags[right_index]]
                added_count = add_tag_discovery_results(
                    run,
                    seed_tags=seed_tags,
                    tag_names=tag_pair,
                    rank_offset=rank_offset,
                    max_results=max_results_per_search,
                    source='tag_combination_search',
                    country=country,
                    language=language,
                )
                discovered += added_count
                games_processed += added_count
                if added_count:
                    addRunProgressEvent(
                        run_id,
                        'enrichment',
                        games_processed,
                        unit='games',
                        message='Discovering and enriching candidates',
                        details={'query': ' + '.join(tag_pair), 'source': 'tag_combination_search', 'added_count': added_count, 'discovered_count': discovered},
                    )
                rank_offset += max_results_per_search
                if discovery_sleep > 0:
                    time.sleep(discovery_sleep)

        for query in add_default_anchor_queries(strategy, max_search_queries):
            added_count = add_text_discovery_results(
                run,
                query=query,
                rank_offset=rank_offset,
                max_results=max_results_per_search,
                country=country,
                language=language,
            )
            discovered += added_count
            games_processed += added_count
            if added_count:
                addRunProgressEvent(
                    run_id,
                    'enrichment',
                    games_processed,
                    unit='games',
                    message='Discovering and enriching candidates',
                    details={'query': query, 'source': 'text_search', 'added_count': added_count, 'discovered_count': discovered},
                )
            rank_offset += max_results_per_search
            if discovery_sleep > 0:
                time.sleep(discovery_sleep)

    addRunEvent(run_id, 'enrichment', 'stage_completed', 'Steam enrichment completed', {'enriched_app_count': len(enriched_appids), 'discovered_count': discovered})
    addRunEvent(run_id, 'enrichment', 'stage_completed', 'Discover and enrich candidates completed', {'processed_count': games_processed, 'unit': 'games', 'enriched_app_count': len(enriched_appids), 'discovered_count': discovered})
    log_step_event('02_discover_candidates', 'completed', run_id=run_id, message='Completed candidate discovery and enrichment', enriched_app_count=len(enriched_appids), discovered_count=discovered)
    return {'enriched_app_count': len(enriched_appids), 'discovered_count': discovered}


def candidate_filter_result(
    candidate: dict[str, Any],
    app: dict[str, Any] | None,
    *,
    min_reviews: int,
    include_upcoming: bool,
    include_free: bool,
    include_failed: bool,
    include_low_review_strategic: bool,
) -> tuple[bool, str]:
    if candidate.get('is_user_required') or candidate.get('is_benchmark_only'):
        return True, 'kept_user_controlled'
    if not app:
        return include_failed, 'fetch_failed'
    if app.get('release_status') == 'fetch_failed':
        return include_failed, 'fetch_failed'
    if app.get('app_type') and app.get('app_type') != 'game':
        return False, 'not_game'
    if app.get('is_free') is True and not include_free:
        return False, 'free_excluded'
    if app.get('release_status') == 'upcoming':
        return include_upcoming or include_low_review_strategic, 'kept_upcoming' if include_upcoming else 'below_min_reviews_upcoming'
    review_count = as_int(app.get('review_count'))
    if review_count < min_reviews:
        return include_low_review_strategic and bool(candidate.get('is_user_required')), f'below_min_reviews_{min_reviews}'
    return True, 'kept'


def weighted_tag_score(seed_tags: list[str], candidate_tags: list[str]) -> tuple[int, list[str]]:
    seed_normalized = [tag.lower() for tag in seed_tags]
    candidate_normalized = {tag.lower() for tag in candidate_tags}
    score = 0
    overlaps: list[str] = []

    for index, tag in enumerate(seed_normalized):
        if tag in candidate_normalized:
            weight = max(1, 20 - index)
            score += weight
            overlaps.append(seed_tags[index])

    return score, overlaps


def shortlist_reason_payload(reasons: list[str]) -> dict[str, Any]:
    return {'shortlist_reasons': reasons}


def score_run(run_id: str) -> dict[str, Any]:
    require_prior_stage(run_id, 'scoring', 'enrichment', 'Discover and Enrich Candidates')
    run, game = get_run_and_game(run_id)
    config = run.get('run_config') or {}
    min_reviews = as_int(run.get('min_review_count'), 1000)
    max_candidates = as_int(config.get('shortlist_max_candidates'), as_int(config.get('shortlist_limit'), 150))
    top_fit = as_int(config.get('shortlist_top_fit'), 60)
    top_reviewed = as_int(config.get('shortlist_top_reviewed'), 30)
    top_anchor = as_int(config.get('shortlist_top_anchor'), 80)
    scoring_version = config.get('scoring_version') or 'db_weighted_tags_v1'
    include_upcoming = as_bool(run.get('include_upcoming'), True)
    include_low_review_strategic = as_bool(run.get('include_low_review_strategic_candidates'), True)
    include_free = as_bool(config.get('include_free_candidates'), False)
    include_failed = as_bool(config.get('include_failed_candidates'), False)

    updateResearchRunStatus(run_id, 'running', current_stage='scoring')
    addRunEvent(run_id, 'scoring', 'stage_started', 'Candidate scoring started')
    log_step_event('04_filter_candidates', 'started', run_id=run_id, message='Started candidate filtering')
    log_step_event('05_score_more_like_this', 'started', run_id=run_id, message='Started candidate scoring')
    log_step_event('06_shortlist_candidates', 'started', run_id=run_id, message='Started candidate shortlisting')

    seed_appid = as_int(game.get('steam_appid'))
    seed_app = one('steam_apps', {'appid': f'eq.{seed_appid}'}) if seed_appid else None
    seed_tags = (seed_app or {}).get('tags') or config.get('anchor_tags') or []

    candidates = client.select('run_candidates', '*', {'run_id': f'eq.{run_id}'})
    scored: list[dict[str, Any]] = []
    filtered_count = 0
    for index, candidate in enumerate(candidates, start=1):
        if candidate.get('is_user_excluded'):
            client.update('run_candidates', {'id': f"eq.{candidate['id']}"}, {'pipeline_status': 'excluded_by_user'}, returning='minimal')
            addRunProgressEvent(
                run_id,
                'scoring',
                index,
                len(candidates),
                unit='games',
                message='Filtering, scoring, and shortlisting candidates',
                details={'candidate_id': candidate['id'], 'title': candidate.get('title'), 'status': 'excluded_by_user'},
            )
            continue
        appid = candidate.get('steam_appid')
        app = one('steam_apps', {'appid': f'eq.{as_int(appid)}'}) if appid else None
        keep, filter_reason = candidate_filter_result(
            candidate,
            app,
            min_reviews=min_reviews,
            include_upcoming=include_upcoming,
            include_free=include_free,
            include_failed=include_failed,
            include_low_review_strategic=include_low_review_strategic,
        )
        if not keep:
            client.update(
                'run_candidates',
                {'id': f"eq.{candidate['id']}"},
                {
                    'pipeline_status': 'filtered_out',
                    'system_exclusion_reason': filter_reason,
                    'is_shortlisted': False,
                    'is_selected_for_report': False,
                },
                returning='minimal',
            )
            filtered_count += 1
            log_step('04_filter_candidates', run_id=run_id, message='Filtered candidate out', candidate_id=candidate['id'], appid=appid, reason=filter_reason)
            addRunProgressEvent(
                run_id,
                'scoring',
                index,
                len(candidates),
                unit='games',
                message='Filtering, scoring, and shortlisting candidates',
                details={'candidate_id': candidate['id'], 'title': candidate.get('title'), 'status': 'filtered_out', 'reason': filter_reason},
            )
            continue
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
            'score_details': {'rule': 'weighted tag overlap plus commercial/review signal', 'filter_reason': filter_reason},
        }
        client.insert('candidate_scores', score_payload, upsert=True, on_conflict='candidate_id,scoring_version', returning='minimal')
        log_step('05_score_more_like_this', run_id=run_id, message='Scored candidate', candidate_id=candidate['id'], appid=appid, fit_score=round(fit_score, 4), review_count=review_count, overlap_count=len(overlaps))
        addRunProgressEvent(
            run_id,
            'scoring',
            index,
            len(candidates),
            unit='games',
            message='Filtering, scoring, and shortlisting candidates',
            details={'candidate_id': candidate['id'], 'title': candidate.get('title'), 'fit_score': round(fit_score, 4)},
        )
        scored.append(
            {
                'candidate': candidate,
                'score': fit_score,
                'commercial': commercial_signal_score,
                'review_count': review_count,
                'overlap_count': len(overlaps),
                'anchor_overlap_count': len(score_payload['overlapping_anchor_tags']),
                'shortlist_reasons': [],
            }
        )

    selected_by_id: dict[str, dict[str, Any]] = {}

    def add_lane(items: list[dict[str, Any]], reason: str, limit: int) -> None:
        for item in items[:limit]:
            candidate_id = item['candidate']['id']
            selected = selected_by_id.setdefault(candidate_id, item)
            if reason not in selected['shortlist_reasons']:
                selected['shortlist_reasons'].append(reason)
                log_step('06_shortlist_candidates', run_id=run_id, message='Added shortlist lane reason', candidate_id=candidate_id, appid=item['candidate'].get('steam_appid'), reason=reason)

    by_fit = sorted(scored, key=lambda item: (item['score'], item['overlap_count'], item['review_count']), reverse=True)
    by_reviews = sorted(scored, key=lambda item: (item['review_count'], item['score'], item['overlap_count']), reverse=True)
    by_anchor = sorted(
        [item for item in scored if item['anchor_overlap_count'] > 0],
        key=lambda item: (item['anchor_overlap_count'], item['score'], item['review_count']),
        reverse=True,
    )
    add_lane(by_fit, 'top_fit_score', top_fit)
    add_lane(by_reviews, 'top_review_count', top_reviewed)
    add_lane(by_anchor, 'anchor_tag_match', top_anchor)
    for item in scored:
        if item['candidate'].get('is_user_required'):
            selected = selected_by_id.setdefault(item['candidate']['id'], item)
            if 'user_required' not in selected['shortlist_reasons']:
                selected['shortlist_reasons'].append('user_required')
                log_step('06_shortlist_candidates', run_id=run_id, message='Promoted user-required candidate', candidate_id=item['candidate']['id'], appid=item['candidate'].get('steam_appid'))
        if item['candidate'].get('is_benchmark_only'):
            selected = selected_by_id.setdefault(item['candidate']['id'], item)
            if 'benchmark_only' not in selected['shortlist_reasons']:
                selected['shortlist_reasons'].append('benchmark_only')
                log_step('06_shortlist_candidates', run_id=run_id, message='Promoted benchmark-only candidate', candidate_id=item['candidate']['id'], appid=item['candidate'].get('steam_appid'))

    selected_items = sorted(
        selected_by_id.values(),
        key=lambda item: (item['score'], item['overlap_count'], item['review_count']),
        reverse=True,
    )[:max_candidates]
    selected_ids = {item['candidate']['id'] for item in selected_items}
    ranked_items = selected_items + [item for item in by_fit if item['candidate']['id'] not in selected_ids]

    for rank, item in enumerate(ranked_items, start=1):
        candidate = item['candidate']
        selected = candidate['id'] in selected_ids
        updates = {
            'pipeline_status': 'selected_for_report' if selected else 'scored',
            'is_shortlisted': selected and not candidate.get('is_benchmark_only'),
            'is_selected_for_report': selected,
            'final_rank': rank,
            'system_exclusion_reason': None,
            'raw_candidate_json': {**(candidate.get('raw_candidate_json') or {}), **shortlist_reason_payload(item.get('shortlist_reasons') or [])},
        }
        client.update('run_candidates', {'id': f"eq.{candidate['id']}"}, updates, returning='minimal')

    addRunEvent(
        run_id,
        'scoring',
        'stage_completed',
        'Candidate scoring completed',
        {'processed_count': len(candidates), 'unit': 'games', 'scored_count': len(scored), 'filtered_count': filtered_count, 'selected_count': len(selected_items), 'shortlist_lanes': {'top_fit': top_fit, 'top_reviewed': top_reviewed, 'top_anchor': top_anchor}},
    )
    log_step_event('04_filter_candidates', 'completed', run_id=run_id, message='Completed candidate filtering', filtered_count=filtered_count)
    log_step_event('05_score_more_like_this', 'completed', run_id=run_id, message='Completed candidate scoring', scored_count=len(scored))
    log_step('06_shortlist_candidates', run_id=run_id, message='Shortlist complete', scored_count=len(scored), filtered_count=filtered_count, selected_count=len(selected_items), shortlist_max_candidates=max_candidates)
    log_step_event('06_shortlist_candidates', 'completed', run_id=run_id, message='Completed candidate shortlisting', selected_count=len(selected_items), shortlist_max_candidates=max_candidates)
    return {'scored_count': len(scored), 'filtered_count': filtered_count, 'selected_count': len(selected_items), 'shortlist_max_candidates': max_candidates}


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


def priority_tier_for_classification(row: dict[str, Any], result: dict[str, Any]) -> str:
    classification = result.get('classification') or row.get('classification') or ''
    direct = float(result.get('direct_fit_score') or row.get('fit_score') or 0)
    audience = float(result.get('audience_fit_score') or row.get('fit_score') or 0)
    commercial = float(result.get('commercial_benchmark_score') or 0)
    confidence = float(result.get('confidence') or 0)
    review_count = as_int(row.get('review_count'))

    if classification == 'direct_comp' and direct >= 80 and confidence >= 0.8:
        return 'Tier 1'
    if classification == 'direct_comp':
        return 'Tier 2'
    if classification == 'adjacent_comp' and (audience >= 70 or commercial >= 70):
        return 'Tier 2'
    if classification in {'audience_comp', 'mechanic_comp', 'commercial_benchmark', 'emerging_comp'} and (commercial >= 80 or review_count >= 100000):
        return 'Context'
    if classification in {'low_fit', 'noise'}:
        return 'Ignore / Context Only'
    return 'Tier 3'


def disagreement_flag_for_classification(row: dict[str, Any], result: dict[str, Any]) -> str:
    code_score = float(row.get('fit_score') or 0)
    direct = float(result.get('direct_fit_score') or 0)
    commercial = float(result.get('commercial_benchmark_score') or 0)
    if code_score >= 75 and direct <= 30:
        return 'High tag overlap, low strategic fit'
    if code_score <= 35 and direct >= 75:
        return 'Low tag overlap, high strategic fit'
    if commercial >= 90 and direct <= 30:
        return 'High commercial signal, low direct fit'
    return ''


def monetization_flag_for_classification(row: dict[str, Any]) -> str:
    if row.get('price_final_cents') == 0:
        return 'Free-to-play / weak pricing benchmark'
    if as_int(row.get('review_count')) == 0:
        return 'No recommendation signal'
    return ''


def strategic_note_for_classification(row: dict[str, Any], result: dict[str, Any], priority_tier: str, disagreement_flag: str) -> str:
    classification = result.get('classification') or row.get('classification') or ''
    if classification == 'direct_comp':
        return 'Use as a primary comp for positioning, feature expectations, Steam page framing, and review mining.'
    if classification == 'adjacent_comp':
        return 'Use as a secondary comp for audience overlap, feature inspiration, scope, and commercial context.'
    if classification == 'audience_comp':
        return 'Use to understand broader audience behavior, but avoid treating it as a direct creative or pricing comp.'
    if classification == 'mechanic_comp':
        return 'Use to study specific systems or mechanics, not overall positioning.'
    if classification == 'commercial_benchmark':
        return 'Use for commercial scale/context only; avoid using it for direct positioning.'
    if classification == 'emerging_comp':
        return 'Use as market context or early signal, but validate manually before treating it as a core benchmark.'
    if classification in {'low_fit', 'noise'}:
        if disagreement_flag:
            return 'Useful as an example of why raw tag overlap can mislead; otherwise deprioritize.'
        return 'Deprioritize for this comp set.'
    return f'Review manually. Suggested priority: {priority_tier}'


def enhance_classification(row: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    priority_tier = priority_tier_for_classification(row, result)
    disagreement_flag = disagreement_flag_for_classification(row, result)
    monetization_flag = monetization_flag_for_classification(row)
    strategic_note = strategic_note_for_classification(row, result, priority_tier, disagreement_flag)
    return {
        'priority_tier': priority_tier,
        'disagreement_flag': disagreement_flag,
        'monetization_flag': monetization_flag,
        'strategic_note': strategic_note,
    }


CLASSIFICATION_LABELS = {
    'direct_comp': 'Direct Comp',
    'adjacent_comp': 'Adjacent Comp',
    'audience_comp': 'Audience Comp',
    'mechanic_comp': 'Mechanic Comp',
    'commercial_benchmark': 'Commercial Benchmark',
    'emerging_comp': 'Aspirational / Market Context',
    'low_fit': 'Low Fit / Noise',
    'noise': 'Low Fit / Noise',
    'unknown': 'Unknown',
}


def latest_classifications_by_candidate(run_id: str) -> dict[str, dict[str, Any]]:
    rows = client.select('candidate_classifications', '*', {'run_id': f'eq.{run_id}', 'order': 'created_at.desc'})
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        candidate_id = str(row.get('candidate_id'))
        if candidate_id and candidate_id not in latest:
            latest[candidate_id] = row
    return latest


def report_rows_with_classifications(run_id: str) -> list[dict[str, Any]]:
    rows = client.select('v_run_candidate_summary', '*', {'run_id': f'eq.{run_id}', 'order': 'fit_score.desc.nullslast'})
    classifications = latest_classifications_by_candidate(run_id)
    merged: list[dict[str, Any]] = []
    for row in rows:
        classification = classifications.get(str(row.get('candidate_id'))) or {}
        llm_output = classification.get('llm_output_json') or {}
        result = {
            'classification': classification.get('classification') or row.get('classification') or 'unknown',
            'confidence': classification.get('confidence') or row.get('confidence'),
            'direct_fit_score': classification.get('direct_fit_score') or row.get('fit_score') or 0,
            'audience_fit_score': classification.get('audience_fit_score') or row.get('fit_score') or 0,
            'mechanic_fit_score': classification.get('mechanic_fit_score') or 0,
            'commercial_benchmark_score': classification.get('commercial_benchmark_score') or 0,
        }
        enhancement = {
            'priority_tier': llm_output.get('priority_tier'),
            'disagreement_flag': llm_output.get('disagreement_flag'),
            'monetization_flag': llm_output.get('monetization_flag'),
            'strategic_note': llm_output.get('strategic_note'),
        }
        if not enhancement['priority_tier']:
            enhancement = enhance_classification(row, result)
        merged.append({**row, **result, **enhancement, 'reasoning': classification.get('reasoning') or row.get('reasoning'), 'strategic_notes': classification.get('strategic_notes')})
    return merged


def clean_md(value: Any) -> str:
    return ' '.join(str(value or '').split()).replace('|', '\\|')


def sort_report_rows(rows: list[dict[str, Any]], *keys: str) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: tuple(float(row.get(key) or 0) for key in keys), reverse=True)


def comp_md_table(rows: list[dict[str, Any]], max_rows: int = 10) -> list[str]:
    lines = ['| Game | Bucket | Direct | Audience | Mechanic | Commercial | Reviews | Why it matters |', '|---|---|---:|---:|---:|---:|---:|---|']
    for row in rows[:max_rows]:
        title = clean_md(row.get('title'))
        url = row.get('steam_url') or ''
        game = f'[{title}]({url})' if url else title
        lines.append(
            '| '
            + ' | '.join(
                [
                    game,
                    CLASSIFICATION_LABELS.get(row.get('classification'), clean_md(row.get('classification'))),
                    str(round(float(row.get('direct_fit_score') or 0))),
                    str(round(float(row.get('audience_fit_score') or 0))),
                    str(round(float(row.get('mechanic_fit_score') or 0))),
                    str(round(float(row.get('commercial_benchmark_score') or 0))),
                    str(as_int(row.get('review_count'))),
                    clean_md(row.get('reasoning') or row.get('strategic_note')),
                ]
            )
            + ' |'
        )
    return lines


def comp_bullets(rows: list[dict[str, Any]], max_rows: int = 8) -> list[str]:
    lines: list[str] = []
    for row in rows[:max_rows]:
        lines.append(
            f"- **{clean_md(row.get('title'))}** — {CLASSIFICATION_LABELS.get(row.get('classification'), clean_md(row.get('classification')))}; "
            f"direct fit {round(float(row.get('direct_fit_score') or 0))}, commercial {round(float(row.get('commercial_benchmark_score') or 0))}. "
            f"{clean_md(row.get('strategic_notes') or row.get('strategic_note') or row.get('reasoning'))}"
        )
    return lines


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
    batch_size = max(1, as_int(os.getenv('STEAM_GTM_LLM_CLASSIFICATION_BATCH_SIZE'), 25))
    results: dict[str, dict[str, Any]] = {}

    def classify_batch(batch_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        payload = {'candidates': [compact_candidate_for_llm(row) for row in batch_rows]}
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
        parsed = parse_model_json(response.output_text, context='Classification results')
        return {str(item['candidate_id']): item for item in parsed.get('candidates') or []}

    for start in range(0, len(rows), batch_size):
        batch_rows = rows[start:start + batch_size]
        try:
            results.update(classify_batch(batch_rows))
            continue
        except Exception:
            for batch_row in batch_rows:
                try:
                    results.update(classify_batch([batch_row]))
                except Exception:
                    continue
    return results


def classify_run(rule_id: str, run_id: str) -> dict[str, Any]:
    require_prior_stage(run_id, 'classification', 'scoring', 'Filter, Score & Shortlist Candidates')
    updateResearchRunStatus(run_id, 'running', current_stage='classification')
    addRunEvent(run_id, 'classification', 'stage_started', 'Candidate classification started')
    log_step_event('07_llm_classify_comps', 'started', run_id=run_id, message='Started competitor classification')
    rows = client.select('v_run_candidate_summary', '*', {'run_id': f'eq.{run_id}'})
    for row in rows:
        try:
            appid = as_int(row.get('steam_appid'))
            app = one('steam_apps', {'appid': f'eq.{appid}'}) if appid else None
            persist_comp_classification({'id': row['candidate_id'], **row}, app)
        except Exception as exc:
            addRunEvent(
                run_id,
                'classification',
                'comp_classification_refresh_failed',
                f"Failed to refresh AAA / Classic metadata for {row.get('title')}",
                {'candidate_id': row.get('candidate_id'), 'steam_appid': row.get('steam_appid'), 'error': str(exc)},
            )
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
    for index, row in enumerate(rows, start=1):
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
        enhancement = enhance_classification(row, result)
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
            'strategic_notes': result.get('strategic_notes') or enhancement['strategic_note'],
            'positioning_notes': result.get('positioning_notes'),
            'prompt_version': prompt_version,
            'model_version': model_version if llm_result else 'rule_based',
            'llm_input_json': row,
            'llm_output_json': {**(llm_result or result), **enhancement},
        }
        existing = one(
            'candidate_classifications',
            {'candidate_id': f"eq.{row['candidate_id']}", 'prompt_version': f'eq.{payload["prompt_version"]}'},
        )
        if existing:
            client.update('candidate_classifications', {'id': f"eq.{existing['id']}"}, payload, returning='minimal')
        else:
            client.insert('candidate_classifications', payload, returning='minimal')
        log_step('07_llm_classify_comps', run_id=run_id, message='Classified candidate', candidate_id=row['candidate_id'], classification=payload['classification'], confidence=payload['confidence'])
        if row.get('pipeline_status') != 'excluded_by_user':
            client.update('run_candidates', {'id': f"eq.{row['candidate_id']}"}, {'pipeline_status': 'classified'}, returning='minimal')
        count += 1
        addRunProgressEvent(
            run_id,
            'classification',
            index,
            len(rows),
            unit='games',
            message='Classifying candidates',
            details={'candidate_id': row['candidate_id'], 'title': row.get('title'), 'classification': payload['classification']},
        )
    addRunEvent(run_id, 'classification', 'stage_completed', 'Candidate classification completed', {'processed_count': count, 'unit': 'games', 'classified_count': count, 'model_version': model_version, 'prompt_version': prompt_version})
    log_step_event('07_llm_classify_comps', 'completed', run_id=run_id, message='Completed competitor classification', classified_count=count, model_version=model_version, prompt_version=prompt_version)
    return {'classified_count': count, 'model_version': model_version, 'prompt_version': prompt_version}


def generate_competitor_report(run_id: str) -> dict[str, Any]:
    run, _game = get_run_and_game(run_id)
    updateResearchRunStatus(run_id, 'running', current_stage='report_generation')
    log_step_event('08_generate_comp_report', 'started', run_id=run_id, message='Started competitor report generation')
    rows = report_rows_with_classifications(run_id)
    selected = [row for row in rows if row.get('is_selected_for_report')]
    tier1 = sort_report_rows([row for row in selected if row.get('priority_tier') == 'Tier 1'], 'direct_fit_score', 'commercial_benchmark_score')
    tier2 = sort_report_rows([row for row in selected if row.get('priority_tier') == 'Tier 2'], 'direct_fit_score', 'commercial_benchmark_score')
    context = sort_report_rows([row for row in selected if row.get('priority_tier') == 'Context'], 'commercial_benchmark_score', 'audience_fit_score')
    direct = sort_report_rows([row for row in rows if row.get('classification') == 'direct_comp'], 'direct_fit_score', 'audience_fit_score')
    adjacent = sort_report_rows([row for row in rows if row.get('classification') == 'adjacent_comp'], 'direct_fit_score', 'audience_fit_score')
    audience = sort_report_rows([row for row in rows if row.get('classification') == 'audience_comp'], 'audience_fit_score', 'commercial_benchmark_score')
    mechanic_commercial = sort_report_rows([row for row in rows if row.get('classification') in {'mechanic_comp', 'commercial_benchmark', 'emerging_comp'}], 'mechanic_fit_score', 'commercial_benchmark_score')
    low_fit = sort_report_rows([row for row in rows if row.get('classification') in {'low_fit', 'noise'}], 'fit_score', 'review_count')
    flagged = [row for row in rows if row.get('disagreement_flag')]
    bucket_counts: dict[str, int] = {}
    for row in rows:
        label = CLASSIFICATION_LABELS.get(row.get('classification'), clean_md(row.get('classification') or 'Unknown'))
        bucket_counts[label] = bucket_counts.get(label, 0) + 1

    lines = [f"# Steam Competitor Discovery Report: {run.get('name') or run_id}", '', '## Executive Summary', '']
    lines.append(
        f"Generated from {len(rows)} candidates; {len(selected)} selected for report. "
        "Candidates were discovered from user controls, Steam More Like This, Steam tag searches, tag-combination searches, and strategy-generated text searches where available."
    )
    lines.append('')
    lines.append('## Top Priority Comps')
    lines.append('')
    lines.extend(comp_md_table(tier1 + tier2, max_rows=10) if tier1 or tier2 else ['_No Tier 1 or Tier 2 comps identified._'])
    lines.append('')
    lines.append('## Tier 1: Direct Comps')
    lines.append('')
    lines.extend(comp_bullets(tier1, max_rows=10) if tier1 else ['_No Tier 1 comps identified._'])
    lines.append('')
    lines.append('## Tier 2: Secondary Direct + Adjacent Comps')
    lines.append('')
    lines.extend(comp_bullets(tier2, max_rows=12) if tier2 else ['_No Tier 2 comps identified._'])
    lines.append('')
    lines.append('## Bucket Breakdown')
    lines.append('')
    lines.append('| Bucket | Count |')
    lines.append('|---|---:|')
    for bucket, count in sorted(bucket_counts.items()):
        lines.append(f'| {bucket} | {count} |')
    lines.append('')
    lines.append('## Direct Comps')
    lines.append('')
    lines.extend(comp_md_table(direct, max_rows=12) if direct else ['_No direct comps identified._'])
    lines.append('')
    lines.append('## Adjacent Comps')
    lines.append('')
    lines.extend(comp_md_table(adjacent, max_rows=12) if adjacent else ['_No adjacent comps identified._'])
    lines.append('')
    lines.append('## Audience / Market Context')
    lines.append('')
    lines.extend(comp_md_table(sort_report_rows(audience + context, 'audience_fit_score', 'commercial_benchmark_score'), max_rows=20) if audience or context else ['_No audience/context comps identified._'])
    lines.append('')
    lines.append('## Mechanic / Commercial References')
    lines.append('')
    lines.extend(comp_md_table(mechanic_commercial, max_rows=12) if mechanic_commercial else ['_No mechanic/commercial references identified._'])
    lines.append('')
    lines.append('## Low-Fit / Noisy Results')
    lines.append('')
    lines.extend(comp_md_table(low_fit, max_rows=12) if low_fit else ['_No low-fit results identified._'])
    lines.append('')
    lines.append('## Important Disagreement Flags')
    lines.append('')
    if flagged:
        lines.append('These candidates show where deterministic tag/review signals and strategic classification disagree.')
        lines.append('')
        lines.extend(comp_md_table(flagged, max_rows=10))
    else:
        lines.append('_No major disagreement flags identified._')
    lines.append('')
    lines.append('## Recommended Next Research')
    lines.append('')
    lines.append('1. Pull positive and negative Steam reviews for Tier 1 direct comps.')
    lines.append('2. Summarize praise themes, complaint themes, feature expectations, and pricing/value comments.')
    lines.append('3. Compare Steam page positioning across direct comps.')
    lines.append('4. Use adjacent and mechanic comps to study specific systems, not direct positioning.')
    lines.append('5. Keep low-fit/noisy results as evidence that raw tag overlap alone is insufficient.')

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
    addRunEvent(run_id, 'report_generation', 'stage_completed', 'Competitor report generated', {'processed_count': len(rows), 'unit': 'candidates', 'report_id': report.get('id') if report else None, 'candidate_count': len(rows), 'selected_count': len(selected)})
    log_step('08_generate_comp_report', run_id=run_id, message='Generated competitor report', report_id=report.get('id') if report else None, selected_candidate_count=len(selected), candidate_count=len(rows))
    log_step_event('08_generate_comp_report', 'completed', run_id=run_id, message='Completed competitor report generation', report_id=report.get('id') if report else None)
    return {'report_id': report.get('id') if report else None, 'selected_candidate_count': len(selected), 'candidate_count': len(rows)}


def latest_event_at(run_id: str, event_type: str) -> str | None:
    events = client.select('run_events', '*', {'run_id': f'eq.{run_id}', 'event_type': f'eq.{event_type}', 'order': 'created_at.desc', 'limit': '1'})
    if not events:
        return None
    return events[0].get('created_at')


def candidateSetIsApproved(run_id: str) -> bool:
    approved_at = latest_event_at(run_id, 'candidate_set_approved')
    if not approved_at:
        return False
    changed_at = latest_event_at(run_id, 'candidate_set_changed_after_approval')
    return not changed_at or changed_at <= approved_at


def latest_stage_event_at(run_id: str, stage: str, event_type: str) -> str | None:
    events = client.select(
        'run_events',
        '*',
        {'run_id': f'eq.{run_id}', 'stage': f'eq.{stage}', 'event_type': f'eq.{event_type}', 'order': 'created_at.desc', 'limit': '1'},
    )
    if not events:
        return None
    return events[0].get('created_at')


def stage_is_complete(run_id: str, stage: str) -> bool:
    if stage == 'discovery':
        return bool(latest_stage_event_at(run_id, 'discovery', 'stage_completed') or latest_stage_event_at(run_id, 'discovery', 'known_games_guidance_skipped'))
    return bool(latest_stage_event_at(run_id, stage, 'stage_completed'))


def require_prior_stage(run_id: str, stage: str, prior_stage: str, prior_label: str) -> None:
    if not stage_is_complete(run_id, prior_stage):
        raise RuntimeError(f'{prior_label} must be completed before {stage.replace("_", " ")} can start.')


def buildCandidateUniverse(run_id: str) -> dict[str, Any]:
    """User-facing pipeline action: build and classify the candidate universe, but do not generate reports."""
    started = utc_now_iso()
    approved_before_run = candidateSetIsApproved(run_id)
    addRunEvent(run_id, 'discovery', 'candidate_universe_started', 'Build Candidate Universe started')
    try:
        prepare_result = prepareRunCandidates(run_id)
        enrich_result = enrich_run(run_id)
        score_result = score_run(run_id)
        classify_result = classify_run('rule_based_v1', run_id)
        client.update(
            'research_runs',
            {'id': f'eq.{run_id}'},
            {
                'status': 'needs_review',
                'current_stage': 'classification',
                'started_at': started,
                'failure_message': None,
            },
            returning='minimal',
        )
        if approved_before_run:
            markCandidateSetChangedAfterApproval(
                run_id,
                'classification',
                'Candidate universe was rebuilt after approval',
                {
                    'prepare_count': prepare_result.get('count'),
                    'enriched_app_count': enrich_result.get('enriched_app_count'),
                    'discovered_count': enrich_result.get('discovered_count'),
                    'scored_count': score_result.get('scored_count'),
                    'classified_count': classify_result.get('classified_count'),
                },
            )
        addRunEvent(run_id, 'classification', 'candidate_universe_completed', 'Build Candidate Universe completed; candidates are ready for review')
        return {
            'status': 'needs_review',
            'prepare': prepare_result,
            'enrichment': enrich_result,
            'scoring': score_result,
            'classification': classify_result,
        }
    except Exception as exc:
        client.update(
            'research_runs',
            {'id': f'eq.{run_id}'},
            {'status': 'failed', 'failure_message': str(exc), 'failed_at': utc_now_iso()},
            returning='minimal',
        )
        addRunEvent(run_id, 'classification', 'candidate_universe_failed', 'Build Candidate Universe failed', {'error': str(exc)})
        raise


def generateReportsForRun(run_id: str, require_approval: bool = True) -> dict[str, Any]:
    """User-facing pipeline action: generate final reports from reviewed/selected candidates."""
    if not stage_is_complete(run_id, 'classification'):
        raise RuntimeError('Candidate classification must be completed before reports can be generated.')
    if require_approval and not candidateSetIsApproved(run_id):
        raise RuntimeError('Candidate set must be approved before reports can be generated.')
    addRunEvent(run_id, 'report_generation', 'reports_started', 'Generate Reports started')
    try:
        report_result = generate_competitor_report(run_id)
        review_result = runReviewPipeline(run_id)
        client.update(
            'research_runs',
            {'id': f'eq.{run_id}'},
            {'status': 'completed', 'current_stage': 'completed', 'completed_at': utc_now_iso(), 'failure_message': None},
            returning='minimal',
        )
        addRunEvent(run_id, 'completed', 'reports_completed', 'Generate Reports completed')
        return {'status': 'completed', 'report': report_result, 'reviews': review_result}
    except Exception as exc:
        client.update(
            'research_runs',
            {'id': f'eq.{run_id}'},
            {'status': 'failed', 'failure_message': str(exc), 'failed_at': utc_now_iso()},
            returning='minimal',
        )
        addRunEvent(run_id, 'report_generation', 'reports_failed', 'Generate Reports failed', {'error': str(exc)})
        raise


def runResearchPipeline(run_id: str) -> dict[str, Any]:
    started = utc_now_iso()
    addRunEvent(run_id, 'intake', 'pipeline_started', 'Full research pipeline started')
    try:
        universe_result = buildCandidateUniverse(run_id)
        addRunEvent(run_id, 'classification', 'selected_candidates_approved', 'Selected candidates approved automatically by full pipeline admin action')
        reports_result = generateReportsForRun(run_id, require_approval=False)
        client.update(
            'research_runs',
            {'id': f'eq.{run_id}'},
            {'status': 'completed', 'current_stage': 'completed', 'started_at': started, 'completed_at': utc_now_iso(), 'failure_message': None},
            returning='minimal',
        )
        addRunEvent(run_id, 'completed', 'pipeline_completed', 'Full research pipeline completed')
        return {
            'status': 'completed',
            'universe': universe_result,
            'reports': reports_result,
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
