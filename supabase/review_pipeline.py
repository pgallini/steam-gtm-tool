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
from .research_run_service import addRunEvent, addRunProgressEvent, updateResearchRunStatus
from .pipeline_logging import log_step, log_step_event


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
            'feature_expectations': {'type': 'array', 'items': {'type': 'string'}},
            'positioning_language': {'type': 'array', 'items': {'type': 'string'}},
            'pricing_sentiment': {'type': 'string'},
            'opportunities': {'type': 'array', 'items': {'type': 'string'}},
            'risks_for_similar_game': {'type': 'array', 'items': {'type': 'string'}},
            'recommended_actions': {'type': 'array', 'items': {'type': 'string'}},
            'summary': {'type': 'string'},
        },
        'required': ['praise_themes', 'complaint_themes', 'friction_points', 'feature_expectations', 'positioning_language', 'pricing_sentiment', 'opportunities', 'risks_for_similar_game', 'recommended_actions', 'summary'],
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
            'product_implications': {'type': 'array', 'items': {'type': 'string'}},
            'messaging_recommendations': {'type': 'array', 'items': {'type': 'string'}},
            'research_next_steps': {'type': 'array', 'items': {'type': 'string'}},
            'summary': {'type': 'string'},
        },
        'required': ['shared_praise_themes', 'shared_complaint_themes', 'shared_friction_points', 'audience_expectations', 'positioning_opportunities', 'product_implications', 'messaging_recommendations', 'research_next_steps', 'summary'],
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
    log_step_event('09_fetch_tier1_reviews', 'started', run_id=candidate['run_id'], message='Started review collection for candidate', candidate_id=candidate['id'], appid=appid, max_pages=max_pages, language=language, num_per_page=num_per_page)
    log_step('09_fetch_tier1_reviews', run_id=candidate['run_id'], message='Started review collection for candidate', candidate_id=candidate['id'], appid=appid, max_pages=max_pages, language=language, num_per_page=num_per_page)

    counts = {'positive': 0, 'negative': 0}
    try:
        for review_type in ('positive', 'negative'):
            cursor = '*'
            for _page in range(max_pages):
                addRunProgressEvent(
                    candidate['run_id'],
                    'review_collection',
                    counts['positive'] + counts['negative'],
                    unit='reviews',
                    message='Collecting Tier 1 reviews',
                    details={'candidate_id': candidate['id'], 'appid': appid, 'review_type': review_type, 'review_count': counts[review_type]},
                )
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
        log_step_event('09_fetch_tier1_reviews', 'completed', run_id=candidate['run_id'], message='Completed review collection for candidate', candidate_id=candidate['id'], appid=appid, positive=counts['positive'], negative=counts['negative'], total=total)
        log_step('09_fetch_tier1_reviews', run_id=candidate['run_id'], message='Completed review collection for candidate', candidate_id=candidate['id'], appid=appid, positive=counts['positive'], negative=counts['negative'], total=total)
        return {'candidate_id': candidate['id'], **counts, 'total': total, 'status': 'completed'}
    except Exception as exc:
        client.update(
            'candidate_review_collections',
            {'id': f"eq.{collection_row['id']}"},
            {'collection_status': 'failed', 'failure_message': str(exc), 'completed_at': utc_now_iso()},
            returning='minimal',
        )
        log_step_event('09_fetch_tier1_reviews', 'completed', run_id=candidate['run_id'], message='Review collection failed for candidate', candidate_id=candidate['id'], appid=appid, error=str(exc))
        log_step('09_fetch_tier1_reviews', run_id=candidate['run_id'], message='Review collection failed for candidate', candidate_id=candidate['id'], appid=appid, error=str(exc))
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


def compact_reviews(texts: list[str], limit: int = 50, max_chars: int = 1000) -> list[str]:
    compact: list[str] = []
    for text in texts[:limit]:
        clean = ' '.join((text or '').split())
        if clean:
            compact.append(clean[:max_chars])
    return compact


def review_quality_score(review: dict[str, Any]) -> float:
    raw = review.get('raw_review_json') or {}
    weighted = raw.get('weighted_vote_score') or 0
    try:
        weighted_score = float(weighted)
    except (TypeError, ValueError):
        weighted_score = 0.0
    text = review.get('review_text') or ''
    return float(as_int(review.get('helpful_votes'))) + weighted_score + min(len(text) / 500.0, 2.0)


def llm_build_candidate_insight(candidate: dict[str, Any], positives: list[str], negatives: list[str], model: str) -> dict[str, Any]:
    system_prompt = """
You are a senior go-to-market strategist for PC/Steam games.

Analyze recent Steam reviews for one competitor. Extract practical GTM insights only from the supplied reviews. Do not browse.

Focus on repeat praise drivers, repeat complaint/risk drivers, friction points, player language worth borrowing/avoiding, pricing/value sentiment, and positioning opportunities for a similar game.
Also extract category feature expectations, risks for a similar game, and recommended marketing/product actions.
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
    log_step_event('10_summarize_tier1_reviews', 'started', run_id=candidate['run_id'], message='Started candidate review summarization', candidate_id=candidate['id'], appid=appid)
    reviews = client.select('steam_reviews', '*', {'steam_appid': f'eq.{appid}', 'order': 'fetched_at.desc', 'limit': '80'})
    positive_rows = sorted([r for r in reviews if r.get('voted_up') is True and r.get('review_text')], key=review_quality_score, reverse=True)
    negative_rows = sorted([r for r in reviews if r.get('voted_up') is False and r.get('review_text')], key=review_quality_score, reverse=True)
    positives = [r.get('review_text') or '' for r in positive_rows]
    negatives = [r.get('review_text') or '' for r in negative_rows]
    if not positives and not negatives:
        log_step_event('10_summarize_tier1_reviews', 'completed', run_id=candidate['run_id'], message='No reviews found to summarize', candidate_id=candidate['id'], appid=appid)
        log_step('10_summarize_tier1_reviews', run_id=candidate['run_id'], message='No reviews found to summarize', candidate_id=candidate['id'], appid=appid)
        return None
    addRunProgressEvent(
        candidate['run_id'],
        'review_analysis',
        0,
        unit='games',
        message='Summarizing Tier 1 reviews',
        details={'candidate_id': candidate['id'], 'appid': appid, 'positive_review_count': len(positives), 'negative_review_count': len(negatives)},
    )

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
        feature_expectations = llm_output.get('feature_expectations') or []
        risks_for_similar_game = llm_output.get('risks_for_similar_game') or friction_points[:5]
        recommended_actions = llm_output.get('recommended_actions') or opportunities[:5]
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
        feature_expectations = [theme.replace('_', ' ') for theme in (praise + complaints)[:8]]
        risks_for_similar_game = [theme.replace('_', ' ') for theme in complaints[:6]]
        recommended_actions = opportunities[:6]
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
        'llm_input_json': {'positive_count': len(positives), 'negative_count': len(negatives), 'max_positive': 50, 'max_negative': 50},
        'llm_output_json': llm_output
        or {
            'praise_themes': praise,
            'complaint_themes': complaints,
            'feature_expectations': feature_expectations,
            'positioning_opportunities': opportunities,
            'risks_for_similar_game': risks_for_similar_game,
            'recommended_actions': recommended_actions,
        },
    }
    response = client.insert('candidate_review_insights', payload, returning='representation')
    log_step_event('10_summarize_tier1_reviews', 'completed', run_id=candidate['run_id'], message='Completed candidate review summarization', candidate_id=candidate['id'], appid=appid, positive_review_count=len(positives), negative_review_count=len(negatives), prompt_version=prompt_version, model_version=model_version)
    log_step('10_summarize_tier1_reviews', run_id=candidate['run_id'], message='Generated candidate review insight', candidate_id=candidate['id'], appid=appid, positive_review_count=len(positives), negative_review_count=len(negatives), prompt_version=prompt_version, model_version=model_version)
    addRunProgressEvent(
        candidate['run_id'],
        'review_analysis',
        1,
        unit='games',
        message='Summarizing Tier 1 reviews',
        details={'candidate_id': candidate['id'], 'appid': appid, 'positive_review_count': len(positives), 'negative_review_count': len(negatives)},
    )
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
                'details': row.get('llm_output_json') or {},
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
    log_step_event('11_llm_rollup_review_insights', 'started', run_id=run_id, message='Started review rollup generation', insight_count=len(insights))
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
        product_implications = llm_output.get('product_implications') or []
        messaging_recommendations = llm_output.get('messaging_recommendations') or []
        research_next_steps = llm_output.get('research_next_steps') or []
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
        product_implications = [f'Plan around recurring {theme.replace("_", " ")} expectations.' for theme in audience_expectations[:6]]
        messaging_recommendations = positioning_opportunities[:6]
        research_next_steps = ['Compare Steam page positioning across Tier 1 direct comps.', 'Review negative-review friction points before final page copy.', 'Validate price/value language against selected benchmarks.']
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
        'llm_output_json': llm_output
        or {
            'insight_count': len(insights),
            'product_implications': product_implications,
            'messaging_recommendations': messaging_recommendations,
            'research_next_steps': research_next_steps,
        },
    }
    response = client.insert('run_review_rollups', payload, returning='representation')
    log_step_event('11_llm_rollup_review_insights', 'completed', run_id=run_id, message='Completed review rollup generation', insight_count=len(insights), prompt_version=prompt_version, model_version=model_version)
    log_step('11_llm_rollup_review_insights', run_id=run_id, message='Generated review rollup', insight_count=len(insights), prompt_version=prompt_version, model_version=model_version)
    return response[0] if isinstance(response, list) else response


def latest_review_classifications(run_id: str) -> dict[str, dict[str, Any]]:
    rows = client.select('candidate_classifications', '*', {'run_id': f'eq.{run_id}', 'order': 'created_at.desc'})
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        candidate_id = str(row.get('candidate_id'))
        if candidate_id and candidate_id not in latest:
            latest[candidate_id] = row
    return latest


def tier1_direct_candidate_ids(run_id: str) -> set[str]:
    candidate_ids: set[str] = set()
    for candidate_id, row in latest_review_classifications(run_id).items():
        output = row.get('llm_output_json') or {}
        confidence = float(row.get('confidence') or 0)
        direct = float(row.get('direct_fit_score') or 0)
        if row.get('classification') == 'direct_comp' and (output.get('priority_tier') == 'Tier 1' or (direct >= 80 and confidence >= 0.8)):
            candidate_ids.add(candidate_id)
    return candidate_ids


def candidate_lookup(run_id: str) -> dict[str, dict[str, Any]]:
    rows = client.select('run_candidates', '*', {'run_id': f'eq.{run_id}'})
    return {str(row.get('id')): row for row in rows}


def clean_md(value: Any) -> str:
    return ' '.join(str(value or '').split())


def list_lines(items: list[Any], fallback: str) -> list[str]:
    return [f'- {clean_md(item)}' for item in items if clean_md(item)] or [fallback]


def generate_review_report(run_id: str, rollup: dict[str, Any] | None) -> dict[str, Any] | None:
    run = one('research_runs', {'id': f'eq.{run_id}'})
    if not run or not rollup:
        return None
    log_step_event('12_generate_review_insights_report', 'started', run_id=run_id, message='Started review insights report generation')
    insights = client.select('candidate_review_insights', '*', {'run_id': f'eq.{run_id}', 'order': 'created_at.asc'})
    candidates = candidate_lookup(run_id)
    rollup_details = rollup.get('llm_output_json') or {}
    lines = [f"# Tier 1 Steam Review Insights: {run.get('name') or run_id}", '']
    lines.append('This report summarizes positive and negative Steam reviews for Tier 1 direct comps, then rolls those insights up into category expectations and positioning opportunities.')
    lines.append('')
    lines.append('## Games Analyzed')
    lines.append('')
    for insight in insights:
        candidate = candidates.get(str(insight.get('candidate_id'))) or {}
        lines.append(f"- **{clean_md(candidate.get('title') or insight.get('candidate_id'))}** (`{candidate.get('steam_appid') or 'unknown appid'}`)")
    if not insights:
        lines.append('_No per-game review insights were generated._')
    lines.append('')
    lines.append('## Cross-Game Takeaways')
    lines.append('')
    lines.append(rollup.get('summary') or '')
    lines.append('')
    lines.append('## Shared Praise Themes')
    lines.extend([f'- {theme}' for theme in rollup.get('shared_praise_themes') or []] or ['_No shared praise themes found._'])
    lines.append('')
    lines.append('## Shared Complaint / Friction Themes')
    lines.extend([f'- {theme}' for theme in rollup.get('shared_complaint_themes') or []] or ['_No shared complaint themes found._'])
    lines.append('')
    lines.append('## Positioning Opportunities')
    lines.extend([f'- {item}' for item in rollup.get('positioning_opportunities') or []] or ['_No positioning opportunities found._'])
    lines.append('')
    lines.append('## Product Implications')
    lines.extend(list_lines(rollup_details.get('product_implications') or [], '_No product implications found._'))
    lines.append('')
    lines.append('## Messaging Recommendations')
    lines.extend(list_lines(rollup_details.get('messaging_recommendations') or [], '_No messaging recommendations found._'))
    lines.append('')
    lines.append('## Recommended Research Next Steps')
    lines.extend(list_lines(rollup_details.get('research_next_steps') or [], '_No research next steps found._'))
    for insight in insights:
        candidate = candidates.get(str(insight.get('candidate_id'))) or {}
        details = insight.get('llm_output_json') or {}
        lines.append('')
        lines.append('---')
        lines.append('')
        lines.append(f"## {clean_md(candidate.get('title') or insight.get('candidate_id'))}")
        lines.append('')
        lines.append(clean_md(insight.get('summary')))
        lines.append('')
        lines.append('### Praise Themes')
        lines.extend(list_lines(insight.get('praise_themes') or [], '_No praise themes found._'))
        lines.append('')
        lines.append('### Complaint Themes')
        lines.extend(list_lines(insight.get('complaint_themes') or [], '_No complaint themes found._'))
        lines.append('')
        lines.append('### Feature Expectations')
        lines.extend(list_lines(details.get('feature_expectations') or [], '_No feature expectations found._'))
        lines.append('')
        lines.append('### Positioning Opportunities')
        lines.extend(list_lines(insight.get('opportunities') or details.get('positioning_opportunities') or [], '_No positioning opportunities found._'))
        lines.append('')
        lines.append('### Risks for Similar Games')
        lines.extend(list_lines(details.get('risks_for_similar_game') or [], '_No risks found._'))
        lines.append('')
        lines.append('### Recommended Actions')
        lines.extend(list_lines(details.get('recommended_actions') or [], '_No recommended actions found._'))
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
    report = response[0] if isinstance(response, list) else response
    log_step_event('12_generate_review_insights_report', 'completed', run_id=run_id, message='Completed review insights report generation', report_id=report.get('id') if report else None)
    log_step('12_generate_review_insights_report', run_id=run_id, message='Generated review insights report', report_id=report.get('id') if report else None)
    return report


def runReviewPipeline(run_id: str) -> dict[str, Any]:
    run = one('research_runs', {'id': f'eq.{run_id}'})
    if not run:
        raise ValueError(f'Research run {run_id} not found')
    config = run.get('run_config') or {}
    max_pages = as_int(config.get('review_max_pages'), 3)
    candidate_limit = as_int(config.get('review_candidate_limit'), 50)
    language = config.get('review_language', 'english')
    num_per_page = as_int(config.get('review_num_per_page'), 100)

    updateResearchRunStatus(run_id, 'running', current_stage='review_collection')
    addRunEvent(run_id, 'review_collection', 'stage_started', 'Review collection started', {'candidate_limit': candidate_limit, 'max_pages': max_pages})
    log_step_event('09_fetch_tier1_reviews', 'started', run_id=run_id, message='Started Tier 1 review collection stage', candidate_limit=candidate_limit, max_pages=max_pages)
    selected_candidates = client.select(
        'run_candidates',
        '*',
        {'run_id': f'eq.{run_id}', 'is_selected_for_report': 'eq.true', 'order': 'final_rank.asc.nullslast'},
    )
    tier1_ids = tier1_direct_candidate_ids(run_id)
    tier1_candidates = [candidate for candidate in selected_candidates if str(candidate.get('id')) in tier1_ids]
    candidates = (tier1_candidates or selected_candidates)[:candidate_limit]
    collection_results = []
    for index, candidate in enumerate(candidates, start=1):
        try:
            collection_results.append(collect_reviews_for_candidate(candidate, max_pages=max_pages, language=language, num_per_page=num_per_page))
        except Exception as exc:
            addRunEvent(run_id, 'review_collection', 'candidate_review_collection_failed', f"Failed to collect reviews for {candidate.get('title')}", {'error': str(exc)})
        addRunProgressEvent(
            run_id,
            'review_collection',
            index,
            len(candidates),
            unit='games',
            message='Collecting Tier 1 reviews',
            details={'candidate_id': candidate.get('id'), 'title': candidate.get('title')},
        )

    addRunEvent(run_id, 'review_collection', 'stage_completed', 'Review collection completed', {'collections': collection_results, 'processed_count': len(candidates), 'unit': 'games'})
    log_step_event('09_fetch_tier1_reviews', 'completed', run_id=run_id, message='Completed Tier 1 review collection stage', collections=len(collection_results))

    updateResearchRunStatus(run_id, 'running', current_stage='review_analysis')
    addRunEvent(run_id, 'review_analysis', 'stage_started', 'Review insight analysis started')
    log_step_event('10_summarize_tier1_reviews', 'started', run_id=run_id, message='Started Tier 1 review summarization stage')
    insight_count = 0
    for index, candidate in enumerate(candidates, start=1):
        insight = build_candidate_insight(candidate)
        if insight:
            insight_count += 1
        addRunProgressEvent(
            run_id,
            'review_analysis',
            index,
            len(candidates),
            unit='games',
            message='Summarizing Tier 1 reviews',
            details={'candidate_id': candidate.get('id'), 'title': candidate.get('title'), 'insight_count': insight_count},
        )

    rollup = rollup_review_insights(run_id)
    report = generate_review_report(run_id, rollup)
    addRunEvent(run_id, 'review_analysis', 'stage_completed', 'Review insight analysis completed', {'insight_count': insight_count, 'rollup_id': rollup.get('id') if rollup else None, 'report_id': report.get('id') if report else None, 'processed_count': len(candidates), 'unit': 'games'})
    log_step_event('10_summarize_tier1_reviews', 'completed', run_id=run_id, message='Completed Tier 1 review summarization stage', insight_count=insight_count)
    log_step_event('11_llm_rollup_review_insights', 'started', run_id=run_id, message='Started review rollup stage')
    log_step_event('11_llm_rollup_review_insights', 'completed', run_id=run_id, message='Completed review rollup stage', rollup_id=rollup.get('id') if rollup else None)
    log_step_event('12_generate_review_insights_report', 'started', run_id=run_id, message='Started review insights report stage')
    log_step('12_generate_review_insights_report', run_id=run_id, message='Completed review pipeline', insight_count=insight_count, rollup_id=rollup.get('id') if rollup else None, report_id=report.get('id') if report else None)
    log_step_event('12_generate_review_insights_report', 'completed', run_id=run_id, message='Completed review insights report stage', report_id=report.get('id') if report else None)
    return {
        'collections': collection_results,
        'insight_count': insight_count,
        'rollup_id': rollup.get('id') if rollup else None,
        'report_id': report.get('id') if report else None,
    }
