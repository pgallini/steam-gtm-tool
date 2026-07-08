from __future__ import annotations

import html
import json
import re
import time
from functools import lru_cache
from typing import Any

import requests
from bs4 import BeautifulSoup

from .pipeline_logging import log_step, log_step_event


APP_DETAILS_URL = 'https://store.steampowered.com/api/appdetails'
STORE_APP_URL = 'https://store.steampowered.com/app/{appid}/'
MAX_STEAM_FETCH_RETRIES = 4


def clean_text(value: str | None) -> str:
    if not value:
        return ''
    return re.sub(r'\s+', ' ', value).strip()


def steam_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            'User-Agent': 'steam-gtm-research-prototype/0.1',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    )
    # Helps with age-gated/mature store pages.
    session.cookies.set('birthtime', '568022401', domain='.steampowered.com')
    session.cookies.set('lastagecheckage', '1-January-1988', domain='.steampowered.com')
    session.cookies.set('mature_content', '1', domain='.steampowered.com')
    return session


def _backoff_seconds(attempt: int) -> float:
    return min(30.0, 2.0 ** attempt)


def _get_with_retry(url: str, *, params: dict[str, Any], run_id: str | None, step_key: str, appid: int) -> requests.Response | None:
    last_error: Exception | None = None
    for attempt in range(1, MAX_STEAM_FETCH_RETRIES + 1):
        try:
            response = steam_session().get(url, params=params, timeout=45)
            if response.status_code == 429:
                wait_seconds = _backoff_seconds(attempt)
                log_step(step_key, run_id=run_id, message='Steam rate limit hit', appid=appid, attempt=attempt, wait_seconds=wait_seconds, url=url)
                time.sleep(wait_seconds)
                continue
            response.raise_for_status()
            return response
        except Exception as exc:
            last_error = exc
            wait_seconds = _backoff_seconds(attempt)
            log_step(step_key, run_id=run_id, message='Steam request retrying after error', appid=appid, attempt=attempt, wait_seconds=wait_seconds, error=str(exc), url=url)
            time.sleep(wait_seconds)
    if last_error is not None:
        log_step(step_key, run_id=run_id, message='Steam request exhausted retries', appid=appid, error=str(last_error), url=url)
    return None


@lru_cache(maxsize=2048)
def _cached_app_details(appid: int, country: str, language: str) -> dict[str, Any]:
    response = steam_session().get(
        APP_DETAILS_URL,
        params={'appids': str(appid), 'cc': country, 'l': language},
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json().get(str(appid), {'success': False})
    return payload if isinstance(payload, dict) else {'success': False}


@lru_cache(maxsize=2048)
def _cached_store_page(appid: int, country: str, language: str) -> str:
    response = steam_session().get(
        STORE_APP_URL.format(appid=appid),
        params={'cc': country, 'l': language},
        timeout=45,
    )
    response.raise_for_status()
    return response.text


def fetch_app_details(appid: int, *, country: str = 'us', language: str = 'english', run_id: str | None = None) -> dict[str, Any]:
    try:
        log_step_event('03_get_app_details', 'started', run_id=run_id, message='Started Steam app details fetch', appid=appid, country=country, language=language)
        response = _get_with_retry(
            APP_DETAILS_URL,
            params={'appids': str(appid), 'cc': country, 'l': language},
            run_id=run_id,
            step_key='03_get_app_details',
            appid=appid,
        )
        if response is None:
            payload = {'success': False, 'error': 'rate_limited_or_failed'}
        else:
            payload = response.json().get(str(appid), {'success': False})
        if not isinstance(payload, dict):
            payload = {'success': False}
        data = payload.get('data') if payload.get('success') else None
        log_step(
            '03_get_app_details',
            run_id=run_id,
            message='Fetched Steam app details',
            appid=appid,
            success=bool(payload.get('success')),
            name=(data or {}).get('name') if isinstance(data, dict) else None,
        )
        log_step_event('03_get_app_details', 'completed', run_id=run_id, message='Completed Steam app details fetch', appid=appid, success=bool(payload.get('success')))
        return payload
    except Exception as exc:
        log_step('03_get_app_details', run_id=run_id, message='Steam app details fetch failed', appid=appid, error=str(exc))
        log_step_event('03_get_app_details', 'completed', run_id=run_id, message='Steam app details fetch failed', appid=appid, error=str(exc))
        return {'success': False, 'error': str(exc)}


def fetch_store_page(appid: int, *, country: str = 'us', language: str = 'english', run_id: str | None = None) -> str:
    response = _get_with_retry(
        STORE_APP_URL.format(appid=appid),
        params={'cc': country, 'l': language},
        run_id=run_id,
        step_key='01_extract_seed_page_signals',
        appid=appid,
    )
    if response is None:
        raise RuntimeError('Steam store page request failed after retries')
    return response.text


def extract_tags(soup: BeautifulSoup) -> list[dict[str, Any]]:
    tags: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str]] = set()

    for element in soup.select('.app_tag'):
        tag_name = clean_text(element.get_text(' '))
        if not tag_name or tag_name in {'+', 'Popular user-defined tags for this product:'}:
            continue

        tag_id = element.get('data-tagid')
        key = (tag_id, tag_name.lower())
        if key in seen:
            continue
        seen.add(key)

        tags.append(
            {
                'tagid': int(tag_id) if tag_id and tag_id.isdigit() else None,
                'name': tag_name,
                'count': None,
                'browseable': None,
            }
        )

    return tags


def extract_rich_tags_from_scripts(html_text: str) -> list[dict[str, Any]]:
    pattern = re.compile(r'\[\{"tagid":\d+,"name":.*?,"browseable":(?:true|false)\}\]', re.DOTALL)
    tag_sets: list[list[dict[str, Any]]] = []
    for match in pattern.findall(html_text):
        try:
            parsed = json.loads(match)
        except Exception:
            continue
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict) and 'tagid' in parsed[0] and 'name' in parsed[0]:
            tag_sets.append(parsed)
    if not tag_sets:
        return []
    tag_sets.sort(key=len, reverse=True)
    return tag_sets[0]


def extract_basic_page_info(soup: BeautifulSoup) -> dict[str, Any]:
    title_el = soup.select_one('.apphub_AppName')
    review_el = soup.select_one('.user_reviews_summary_row .game_review_summary')
    recent_el = soup.select_one('#review_summary_recent .game_review_summary')
    return {
        'page_title': clean_text(title_el.get_text(' ')) if title_el else None,
        'review_summary': clean_text(review_el.get_text(' ')) if review_el else None,
        'recent_review_summary': clean_text(recent_el.get_text(' ')) if recent_el else None,
    }


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


def extract_linked_apps(soup: BeautifulSoup, source_appid: int) -> list[dict[str, Any]]:
    apps_by_id: dict[int, dict[str, Any]] = {}
    for link in soup.find_all('a', href=True):
        match = re.search(r'/app/(\d+)', link['href'])
        linked_appid = int(match.group(1)) if match else None
        if not linked_appid or linked_appid == source_appid:
            continue

        name_el = link.select_one('.tab_item_name')
        name = clean_text(name_el.get_text(' ')) if name_el else ''
        if not name:
            img = link.find('img')
            if img:
                name = clean_text(img.get('alt'))
        if not name:
            name = clean_text(link.get_text(' '))

        apps_by_id.setdefault(
            linked_appid,
            {
                'appid': linked_appid,
                'name': name or None,
                'url': STORE_APP_URL.format(appid=linked_appid),
                'source_href': link['href'],
            },
        )
    return list(apps_by_id.values())


def fetch_page_signals(appid: int, *, country: str = 'us', language: str = 'english', run_id: str | None = None) -> dict[str, Any]:
    try:
        log_step_event('01_extract_seed_page_signals', 'started', run_id=run_id, message='Started Steam page signal extraction', appid=appid, country=country, language=language)
        html_text = fetch_store_page(appid, country=country, language=language, run_id=run_id)
        soup = BeautifulSoup(html_text, 'html.parser')
        rich_tags = extract_rich_tags_from_scripts(html_text)
        tags = rich_tags or extract_tags(soup)
        more_like_this_appids = extract_more_like_this_appids(soup)
        linked_apps = extract_linked_apps(soup, appid)
        signals = {
            'appid': appid,
            'basic_info': extract_basic_page_info(soup),
            'tags': tags,
            'more_like_this_appids': more_like_this_appids,
            'linked_apps': linked_apps,
            'fetched_at_unix': int(time.time()),
        }
        log_step(
            '01_extract_seed_page_signals',
            run_id=run_id,
            message='Extracted Steam page signals',
            appid=appid,
            tag_names=[tag.get('name') for tag in tags if isinstance(tag, dict) and tag.get('name')],
            tag_ids=[tag.get('tagid') for tag in tags if isinstance(tag, dict) and tag.get('tagid') is not None],
            more_like_this_appids=more_like_this_appids,
            tag_count=len(signals['tags'] or []),
            more_like_this_count=len(signals['more_like_this_appids'] or []),
            linked_app_count=len(signals['linked_apps'] or []),
            page_title=(signals.get('basic_info') or {}).get('page_title'),
        )
        log_step_event('01_extract_seed_page_signals', 'completed', run_id=run_id, message='Completed Steam page signal extraction', appid=appid, tag_count=len(tags), more_like_this_count=len(more_like_this_appids), linked_app_count=len(linked_apps))
        return signals
    except Exception as exc:
        log_step('01_extract_seed_page_signals', run_id=run_id, message='Steam page signal extraction failed', appid=appid, error=str(exc))
        log_step_event('01_extract_seed_page_signals', 'completed', run_id=run_id, message='Steam page signal extraction failed', appid=appid, error=str(exc))
        return {'appid': appid, 'basic_info': {}, 'tags': [], 'more_like_this_appids': [], 'linked_apps': [], 'fetched_at_unix': int(time.time()), 'error': str(exc)}


def normalize_app_details(appid: int, response_for_app: dict[str, Any]) -> dict[str, Any]:
    success = response_for_app.get('success', False)
    data = response_for_app.get('data') if success else None
    if not data:
        return {'appid': appid, 'success': False, 'name': None, 'type': None}
    return {
        'appid': appid,
        'success': success,
        'type': data.get('type'),
        'name': data.get('name'),
        'steam_appid': data.get('steam_appid'),
        'is_free': data.get('is_free'),
        'release_date': data.get('release_date'),
        'developers': data.get('developers'),
        'publishers': data.get('publishers'),
        'price_overview': data.get('price_overview'),
        'categories': data.get('categories'),
        'genres': data.get('genres'),
        'recommendations': data.get('recommendations'),
        'metacritic': data.get('metacritic'),
        'short_description': data.get('short_description'),
        'raw': data,
    }
