from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import requests
from bs4 import BeautifulSoup

from .pipeline_logging import log_step, log_step_event


APP_DETAILS_URL = 'https://store.steampowered.com/api/appdetails'
STORE_APP_URL = 'https://store.steampowered.com/app/{appid}/'
DEFAULT_STEAM_TIMEOUT_SECONDS = 45
MAX_STEAM_FETCH_RETRIES = 4


@dataclass
class SteamFetchResult:
    appid: int
    success: bool
    status: str
    http_status: int | None
    data: dict[str, Any] | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {'success': self.success}
        if self.error is not None:
            payload['error'] = self.error
        if self.data is not None:
            payload['data'] = self.data
        return payload


@lru_cache(maxsize=1)
def steam_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            'User-Agent': 'steam-gtm-research-prototype/0.1',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    )
    session.cookies.set('birthtime', '568022401', domain='.steampowered.com')
    session.cookies.set('lastagecheckage', '1-January-1988', domain='.steampowered.com')
    session.cookies.set('mature_content', '1', domain='.steampowered.com')
    return session


def _backoff_seconds(attempt: int) -> float:
    return min(120.0, 2.0 ** attempt)


def _get_retry_after(response: requests.Response, attempt: int) -> float:
    header_value = response.headers.get('Retry-After')
    if header_value:
        try:
            wait = int(header_value)
            return max(1.0, min(wait, 120.0))
        except ValueError:
            pass
    return _backoff_seconds(attempt)


def _get_with_retry(url: str, *, params: dict[str, Any], run_id: str | None, step_key: str, appid: int) -> requests.Response | None:
    last_error: Exception | None = None
    for attempt in range(1, MAX_STEAM_FETCH_RETRIES + 1):
        try:
            response = steam_session().get(url, params=params, timeout=DEFAULT_STEAM_TIMEOUT_SECONDS)
            if response.status_code == 429:
                wait_seconds = _get_retry_after(response, attempt)
                log_step(step_key, run_id=run_id, message='Steam rate limit hit', appid=appid, attempt=attempt, wait_seconds=wait_seconds, url=url)
                if attempt == MAX_STEAM_FETCH_RETRIES:
                    raise requests.exceptions.RetryError('Steam rate limited')
                time.sleep(wait_seconds)
                continue
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as exc:
            last_error = exc
            if isinstance(exc, requests.exceptions.RetryError):
                break
            wait_seconds = _get_retry_after(getattr(exc, 'response', None) or requests.Response(), attempt)
            log_step(step_key, run_id=run_id, message='Steam request retrying after error', appid=appid, attempt=attempt, wait_seconds=wait_seconds, error=str(exc), url=url)
            if attempt == MAX_STEAM_FETCH_RETRIES:
                break
            time.sleep(wait_seconds)
    if last_error is not None:
        log_step(step_key, run_id=run_id, message='Steam request exhausted retries', appid=appid, error=str(last_error), url=url)
    return None


def fetch_app_details_result(appid: int, *, country: str = 'us', language: str = 'english', run_id: str | None = None) -> SteamFetchResult:
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
            result = SteamFetchResult(appid=appid, success=False, status='steam_rate_limited', http_status=None, data=None, error='rate_limited_or_failed')
        else:
            http_status = response.status_code
            try:
                payload = response.json()
            except ValueError as exc:
                log_step('03_get_app_details', run_id=run_id, message='Steam app details invalid JSON', appid=appid, error=str(exc))
                return SteamFetchResult(appid=appid, success=False, status='steam_invalid_json', http_status=http_status, data=None, error='invalid_json')
            app_payload = payload.get(str(appid), {'success': False})
            if not isinstance(app_payload, dict):
                app_payload = {'success': False}
            success = bool(app_payload.get('success', False))
            status = 'success' if success else 'steam_success_false'
            result = SteamFetchResult(appid=appid, success=success, status=status, http_status=http_status, data=app_payload.get('data'), error=None if success else app_payload.get('error') or 'steam_success_false')
        log_step(
            '03_get_app_details',
            run_id=run_id,
            message='Fetched Steam app details',
            appid=appid,
            success=result.success,
            status=result.status,
            http_status=result.http_status,
        )
        log_step_event('03_get_app_details', 'completed', run_id=run_id, message='Completed Steam app details fetch', appid=appid, success=result.success, status=result.status)
        return result
    except Exception as exc:
        log_step('03_get_app_details', run_id=run_id, message='Steam app details fetch failed', appid=appid, error=str(exc))
        log_step_event('03_get_app_details', 'completed', run_id=run_id, message='Steam app details fetch failed', appid=appid, error=str(exc))
        return SteamFetchResult(appid=appid, success=False, status='steam_http_error', http_status=None, data=None, error=str(exc))


def fetch_app_details(appid: int, *, country: str = 'us', language: str = 'english', run_id: str | None = None) -> dict[str, Any]:
    return fetch_app_details_result(appid, country=country, language=language, run_id=run_id).to_dict()


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


# Existing page signal extraction helpers below remain unchanged.

def clean_text(value: str | None) -> str:
    if not value:
        return ''
    return re.sub(r'\s+', ' ', value).strip()


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
        'header_image': data.get('header_image'),
        'platforms': data.get('platforms'),
        'raw': data,
    }
