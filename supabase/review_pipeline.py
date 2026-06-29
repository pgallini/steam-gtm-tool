from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import requests
from openai import OpenAI

from .client import SupabaseClient
from .research_run_service import addRunEvent, updateResearchRunStatus


client = SupabaseClient()

APP_REVIEWS_URL = 'https://store.steampowered.com/appreviews/{appid}'


REVIEW_INSIGHT_SCHEMA = {
    'name': 'steam_gtm_review_insight',
    'schema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'praise_themes': {'type': 'array', 'items': {'type': 'string'}},
            'complaint_themes': {'type': 'array', 'items': {'type': 'string'}},
            'friction_points': {'type': 'array', 'items': {'type': 'string'}},
            'positioning_language': {'type': 'array', 'items': {'type': 'string'}},
            'pricing_sentiment': {'type': 'string'},
            'opportunities': {'type': 'array', 'items': {'type': 'string'}},
            'summary': {'type': 'string'},
        },
        'required': ['praise_themes', 'complaint_themes', 'friction_points', 'positioning_language', 'pricing_sentiment', 'opportunities', 'summary'],
    },
    'strict': True,
}


REVIEW_ROLLUP_SCHEMA = {
    'name': 'steam_gtm_review_rollup',
    'schema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'shared_praise_themes': {'type': 'array', 'items': {'type': 'string'}},
            'shared_complaint_themes': {'type': 'array', 'items': {'type': 'string'}},
            'shared_friction_points': {'type': 'array', 'items': {'type': 'string'}},
            'audience_expectations': {'type': 'array', 'items': {'type': 'string'}},
            'positioning_opportunities': {'type': 'array', 'items': {'type': 'string'}},
            'summary': {'type': 'string'},
        },
        'required': ['shared_praise_themes', 'shared_complaint_themes', 'shared_friction_points', 'audience_expectations', 'positioning_opportunities', 'summary'],
    },
    'strict': True,
}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == '':
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def openai_model_name() -> str:
    return os.getenv('STEAM_GTM_OPENAI_MODEL') or os.getenv('OPENAI_MODEL') or 'gpt-4o-mini'


def llm_is_configured() -> bool:
    return bool(os.getenv('OPENAI_API_KEY'))


def as_hours(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return round(float(value) / 60.0, 2)
    except (TypeError, ValueError):
        return None


def one(table: str, filters: dict[str, str], select: str = '*') -> dict[str, Any] | None:
    rows = client.select(table, select, filters)
    return rows[0] if rows else None


def fetch_reviews_page(appid: int, *, review_type: str, cursor: str = '*', language: str = 'english', num_per_page: int = 20) -> dict[str, Any]:
    response = requests.get(
        APP_REVIEWS_URL.format(appid=appid),
        params={
            'json': 1,
            'filter': 'recent',
            'language': language,
            'review_type': review_type,
            'purchase_type': 'all',
            'num_per_page': num_per_page,
            'cursor': cursor,
        },
        headers={'User-Agent': 'steam-gtm-research-prototype/0.1'},
        timeout=45,
    )
    response.raise_for_status()
    return response.json()


def normalize_review(appid: int, review_type: str, raw: dict[str, Any]) -> dict[str, Any]:
    author = raw.get('author') or {}
    return {
        'steam_appid': appid,
        'steam_review_id': str(raw.get('recommendationid')) if raw.get('recommendationid') is not None else None,
        'review_type': review_type,
        'voted_up': raw.get('voted_up'),
        'language': raw.get('language'),
        'playtime_hours': as_hours(author.get('playtime_forever')),
        'playtime_at_review_hours': as_hours(author.get('playtime_at_review')),
        'helpful_votes': as_int(raw.get('votes_up')),
        'funny_votes': as_int(raw.get('votes_funny')),
        'received_for_free': raw.get('received_for_free'),
        'steam_purchase': raw.get('steam_purchase'),
        'early_access': raw.get('written_during_early_access'),
        'review_text': raw.get('review'),
        'created_at_steam': datetime.fromtimestamp(raw['timestamp_created'], UTC).isoformat() if raw.get('timestamp_created') else None,
        'updated_at_steam': datetime.fromtimestamp(raw['timestamp_updated'], UTC).isoformat() if raw.get('timestamp_updated') else None,
        'raw_review_json': raw,
    }


def upsert_review(payload: dict[str, Any]) -> None:
    review_id = payload.get('steam_review_id')
    if review_id:
        existing = one('steam_reviews', {'steam_review_id': f'eq.{review_id}'})
        if existing:
            client.update('steam_reviews', {'id': f"eq.{existing['id']}"}, payload, returning='minimal')
            return
    client.insert('steam_reviews', payload, returning='minimal')


def collect_reviews_for_candidate(candidate: dict[str, Any], *, max_pages: int, language: str, num_per_page: int) -> dict[str, Any]:
    appid = as_int(candidate.get('steam_appid'))
    if not appid:
        return {'candidate_id': candidate.get('id'), 'positive': 0, 'negative': 0, 'total': 0, 'status': 'skipped'}

    collection = client.insert(
        'candidate_review_collections',
        {
            'candidate_id': candidate['id'],
            'run_id': candidate['run_id'],
            'collection_status': 'running',
            'started_at': utc_now_iso(),
            'raw_collection_json': {'appid': appid, 'max_pages': max_pages, 'language': language},
        },
        returning='representation',
    )
    collection_row = collection[0] if isinstance(collection, list) else collection

    counts = {'positive': 0, 'negative': 0}
    try:
        for review_type in ('positive', 'negative'):
            cursor = '*'
            for _page in range(max_pages):
                data = fetch_reviews_page(appid, review_type=review_type, cursor=cursor, language=language, num_per_page=num_per_page)
                reviews = data.get('reviews') or []
                if not reviews:
                    break
                for raw in reviews:
                    upsert_review(normalize_review(appid, review_type, raw))
                    counts[review_type] += 1
                next_cursor = data.get('cursor')
                if not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor

        total = counts['positive'] + counts['negative']
        client.update(
            'candidate_review_collections',
            {'id': f"eq.{collection_row['id']}"},
            {
                'positive_review_count': counts['positive'],
                'negative_review_count': counts['negative'],
                'total_review_count': total,
                'collection_status': 'completed',
                'completed_at': utc_now_iso(),
                'raw_collection_json': {'appid': appid, 'max_pages': max_pages, 'language': language, 'counts': counts},
            },
            returning='minimal',
        )
        return {'candidate_id': candidate['id'], **counts, 'total': total, 'status': 'completed'}
    except Exception as exc:
        client.update(
            'candidate_review_collections',
            {'id': f"eq.{collection_row['id']}"},
            {'collection_status': 'failed', 'failure_message': str(exc), 'completed_at': utc_now_iso()},
            returning='minimal',
        )
        raise


THEME_KEYWORDS = {
    'progression_depth': ['progress', 'build', 'upgrade', 'level', 'skill', 'unlock'],
    'combat_feel': ['combat', 'fight', 'attack', 'boss', 'weapon', 'battle'],
    'content_volume': ['content', 'short', 'hours', 'replay', 'ending', 'long'],
    'difficulty_balance': ['hard', 'easy', 'difficulty', 'grind', 'punishing', 'balance'],
    'atmosphere_tone': ['atmosphere', 'story', 'music', 'art', 'style', 'horror', 'funny'],
    'technical_quality': ['bug', 'crash', 'performance', 'fps', 'controller', 'save'],
    'value_price': ['price', 'worth', 'sale', 'value', 'expensive', 'cheap'],
}


def classify_themes(texts: list[str]) -> list[str]:
    counter: Counter[str] = Counter()
    combined = '\n'.join(texts).lower()
    for theme, keywords in THEME_KEYWORDS.items():
        for keyword in keywords:
            counter[theme] += len(re.findall(rf'\b{re.escape(keyword)}\w*\b', combined))
    return [theme for theme, _count in counter.most_common(6) if _count > 0]


def sample_phrases(texts: list[str], limit: int = 6) -> list[str]:
    phrases: list[str] = []
    for text in texts:
        clean = ' '.join((text or '').split())
        if len(clean) < 40:
            continue
        phrases.append(clean[:180])
        if len(phrases) >= limit:
            break
    return phrases


def compact_reviews(texts: list[str], limit: int = 18, max_chars: int = 700) -> list[str]:
    compact: list[str] = []
    for text in texts[:limit]:
        clean = ' '.join((text or '').split())
        if clean:
            compact.append(clean[:max_chars])
    return compact


def llm_build_candidate_insight(candidate: dict[str, Any], positives: list[str], negatives: list[str], model: str) -> dict[str, Any]:
    system_prompt = """
You are a senior go-to-market strategist for PC/Steam games.

Analyze recent Steam reviews for one competitor. Extract practical GTM insights only from the supplied reviews. Do not browse.

Focus on repeat praise drivers, repeat complaint/risk drivers, friction points, player language worth borrowing/avoiding, pricing/value sentiment, and positioning opportunities for a similar game.
Be concise and specific.
""".strip()
    payload = {
        'candidate': {
            'id': candidate.get('id'),
            'title': candidate.get('title'),
            'steam_appid': candidate.get('steam_appid'),
        },
        'positive_reviews': compact_reviews(positives),
        'negative_reviews': compact_reviews(negatives),
    }
    response = OpenAI().responses.create(
        model=model,
        input=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
        ],
        text={
            'format': {
                'type': 'json_schema',
                'name': REVIEW_INSIGHT_SCHEMA['name'],
                'schema': REVIEW_INSIGHT_SCHEMA['schema'],
                'strict': True,
            }
        },
    )
    return json.loads(response.output_text)


def build_candidate_insight(candidate: dict[str, Any]) -> dict[str, Any] | None:
    appid = as_int(candidate.get('steam_appid'))
    if not appid:
        return None
    reviews = client.select('steam_reviews', '*', {'steam_appid': f'eq.{appid}', 'order': 'fetched_at.desc', 'limit': '80'})
    positives = [r.get('review_text') or '' for r in reviews if r.get('voted_up') is True]
    negatives = [r.get('review_text') or '' for r in reviews if r.get('voted_up') is False]
    if not positives and not negatives:
        return None

    model = openai_model_name()
    llm_output: dict[str, Any] | None = None
    prompt_version = 'rule_based_reviews_v1'
    model_version = 'rule_based'
    try:
        if llm_is_configured():
            llm_output = llm_build_candidate_insight(candidate, positives, negatives, model)
            prompt_version = 'llm_review_insight_v1'
            model_version = model
    except Exception as exc:
        addRunEvent(candidate['run_id'], 'review_analysis', 'llm_candidate_review_insight_failed', f"OpenAI review insight failed for {candidate.get('title')}; falling back to rule-based", {'model': model, 'error': str(exc)})

    if llm_output:
        praise = llm_output.get('praise_themes') or []
        complaints = llm_output.get('complaint_themes') or []
        positioning_language = llm_output.get('positioning_language') or []
        opportunities = llm_output.get('opportunities') or []
        friction_points = llm_output.get('friction_points') or complaints[:5]
        pricing_sentiment = llm_output.get('pricing_sentiment') or None
        summary = llm_output.get('summary') or f"LLM review insight for {candidate.get('title')}."
    else:
        praise = classify_themes(positives)
        complaints = classify_themes(negatives)
        positioning_language = sample_phrases(positives + negatives)
        opportunities = []
        if 'technical_quality' in complaints:
            opportunities.append('Message stability, polish, and quality-of-life clearly if the product can credibly own that space.')
        if 'content_volume' in complaints:
            opportunities.append('Set expectations around scope, replayability, and post-launch content.')
        if 'difficulty_balance' in complaints:
            opportunities.append('Clarify difficulty, onboarding, and player-control options on the Steam page.')
        if not opportunities:
            opportunities.append('Use competitor review language to sharpen Steam page promises and risk mitigation.')
        friction_points = complaints[:5]
        pricing_sentiment = 'value/price mentioned' if 'value_price' in praise or 'value_price' in complaints else None
        summary = f"Rule-based review scan for {candidate.get('title')}: {len(positives)} positive and {len(negatives)} negative recent reviews analyzed."

    payload = {
        'candidate_id': candidate['id'],
        'run_id': candidate['run_id'],
        'praise_themes': praise,
        'complaint_themes': complaints,
        'friction_points': friction_points,
        'positioning_language': positioning_language,
        'pricing_sentiment': pricing_sentiment,
        'opportunities': opportunities,
        'summary': summary,
        'prompt_version': prompt_version,
        'model_version': model_version,
        'llm_input_json': {'positive_count': len(positives), 'negative_count': len(negatives)},
        'llm_output_json': llm_output or {'praise_themes': praise, 'complaint_themes': complaints, 'opportunities': opportunities},
    }
    response = client.insert('candidate_review_insights', payload, returning='representation')
    return response[0] if isinstance(response, list) else response


def llm_rollup_review_insights(insights: list[dict[str, Any]], model: str) -> dict[str, Any]:
    system_prompt = """
You are a senior go-to-market strategist for PC/Steam games.

Synthesize per-competitor Steam review insights into cross-game GTM takeaways. Do not browse. Use only the supplied structured insights.
Focus on common praise drivers, common risks/frictions, audience expectations, and positioning opportunities.
""".strip()
    payload = {
        'candidate_review_insights': [
            {
                'summary': row.get('summary'),
                'praise_themes': row.get('praise_themes') or [],
                'complaint_themes': row.get('complaint_themes') or [],
                'friction_points': row.get('friction_points') or [],
                'positioning_language': row.get('positioning_language') or [],
                'opportunities': row.get('opportunities') or [],
            }
            for row in insights[:12]
        ]
    }
    response = OpenAI().responses.create(
        model=model,
        input=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
        ],
        text={
            'format': {
                'type': 'json_schema',
                'name': REVIEW_ROLLUP_SCHEMA['name'],
                'schema': REVIEW_ROLLUP_SCHEMA['schema'],
                'strict': True,
            }
        },
    )
    return json.loads(response.output_text)


def rollup_review_insights(run_id: str) -> dict[str, Any] | None:
    insights = client.select('candidate_review_insights', '*', {'run_id': f'eq.{run_id}', 'order': 'created_at.desc'})
    if not insights:
        return None
    model = openai_model_name()
    llm_output: dict[str, Any] | None = None
    prompt_version = 'rule_based_reviews_v1'
    model_version = 'rule_based'
    try:
        if llm_is_configured():
            llm_output = llm_rollup_review_insights(insights, model)
            prompt_version = 'llm_review_rollup_v1'
            model_version = model
    except Exception as exc:
        addRunEvent(run_id, 'review_analysis', 'llm_review_rollup_failed', 'OpenAI review rollup failed; falling back to rule-based', {'model': model, 'error': str(exc)})

    if llm_output:
        shared_praise_themes = llm_output.get('shared_praise_themes') or []
        shared_complaint_themes = llm_output.get('shared_complaint_themes') or []
        shared_friction_points = llm_output.get('shared_friction_points') or []
        audience_expectations = llm_output.get('audience_expectations') or []
        positioning_opportunities = llm_output.get('positioning_opportunities') or []
        summary = llm_output.get('summary') or f'LLM rollup across {len(insights)} candidate review insight records.'
    else:
        praise = Counter(theme for row in insights for theme in (row.get('praise_themes') or []))
        complaints = Counter(theme for row in insights for theme in (row.get('complaint_themes') or []))
        opportunities = []
        for row in insights:
            opportunities.extend(row.get('opportunities') or [])
        shared_praise_themes = [theme for theme, _ in praise.most_common(8)]
        shared_complaint_themes = [theme for theme, _ in complaints.most_common(8)]
        shared_friction_points = [theme for theme, _ in complaints.most_common(8)]
        audience_expectations = [theme for theme, _ in (praise + complaints).most_common(8)]
        positioning_opportunities = list(dict.fromkeys(opportunities))[:10]
        summary = f'Rule-based rollup across {len(insights)} candidate review insight records.'
    payload = {
        'run_id': run_id,
        'shared_praise_themes': shared_praise_themes,
        'shared_complaint_themes': shared_complaint_themes,
        'shared_friction_points': shared_friction_points,
        'audience_expectations': audience_expectations,
        'positioning_opportunities': positioning_opportunities,
        'summary': summary,
        'prompt_version': prompt_version,
        'model_version': model_version,
        'llm_input_json': {'insight_count': len(insights)},
        'llm_output_json': llm_output or {'insight_count': len(insights)},
    }
    response = client.insert('run_review_rollups', payload, returning='representation')
    return response[0] if isinstance(response, list) else response


def generate_review_report(run_id: str, rollup: dict[str, Any] | None) -> dict[str, Any] | None:
    run = one('research_runs', {'id': f'eq.{run_id}'})
    if not run or not rollup:
        return None
    lines = [f"# Steam Review Insights Report: {run.get('name') or run_id}", '', rollup.get('summary') or '', '']
    lines.append('## Shared Praise Themes')
    lines.extend([f'- {theme}' for theme in rollup.get('shared_praise_themes') or []] or ['_No shared praise themes found._'])
    lines.append('')
    lines.append('## Shared Complaint / Friction Themes')
    lines.extend([f'- {theme}' for theme in rollup.get('shared_complaint_themes') or []] or ['_No shared complaint themes found._'])
    lines.append('')
    lines.append('## Positioning Opportunities')
    lines.extend([f'- {item}' for item in rollup.get('positioning_opportunities') or []] or ['_No positioning opportunities found._'])
    payload = {
        'run_id': run_id,
        'organization_id': run['organization_id'],
        'report_type': 'review_insights_report',
        'title': f"Steam Review Insights Report: {run.get('name') or run_id}",
        'content_md': '\n'.join(lines),
        'report_json': rollup,
        'generated_by': 'review_pipeline.generate_review_report',
        'template_version': rollup.get('prompt_version') or 'review_report_v1',
    }
    response = client.insert('reports', payload, returning='representation')
    return response[0] if isinstance(response, list) else response


def runReviewPipeline(run_id: str) -> dict[str, Any]:
    run = one('research_runs', {'id': f'eq.{run_id}'})
    if not run:
        raise ValueError(f'Research run {run_id} not found')
    config = run.get('run_config') or {}
    max_pages = as_int(config.get('review_max_pages'), 1)
    candidate_limit = as_int(config.get('review_candidate_limit'), 3)
    language = config.get('review_language', 'english')
    num_per_page = as_int(config.get('review_num_per_page'), 20)

    updateResearchRunStatus(run_id, 'running', current_stage='review_collection')
    addRunEvent(run_id, 'review_collection', 'stage_started', 'Review collection started', {'candidate_limit': candidate_limit, 'max_pages': max_pages})
    candidates = client.select(
        'run_candidates',
        '*',
        {'run_id': f'eq.{run_id}', 'is_selected_for_report': 'eq.true', 'is_user_excluded': 'eq.false', 'order': 'final_rank.asc.nullslast', 'limit': str(candidate_limit)},
    )
    collection_results = []
    for candidate in candidates:
        try:
            collection_results.append(collect_reviews_for_candidate(candidate, max_pages=max_pages, language=language, num_per_page=num_per_page))
        except Exception as exc:
            addRunEvent(run_id, 'review_collection', 'candidate_review_collection_failed', f"Failed to collect reviews for {candidate.get('title')}", {'error': str(exc)})

    addRunEvent(run_id, 'review_collection', 'stage_completed', 'Review collection completed', {'collections': collection_results})

    updateResearchRunStatus(run_id, 'running', current_stage='review_analysis')
    addRunEvent(run_id, 'review_analysis', 'stage_started', 'Review insight analysis started')
    insight_count = 0
    for candidate in candidates:
        insight = build_candidate_insight(candidate)
        if insight:
            insight_count += 1
    rollup = rollup_review_insights(run_id)
    report = generate_review_report(run_id, rollup)
    addRunEvent(run_id, 'review_analysis', 'stage_completed', 'Review insight analysis completed', {'insight_count': insight_count, 'rollup_id': rollup.get('id') if rollup else None, 'report_id': report.get('id') if report else None})
    return {
        'collections': collection_results,
        'insight_count': insight_count,
        'rollup_id': rollup.get('id') if rollup else None,
        'report_id': report.get('id') if report else None,
    }
