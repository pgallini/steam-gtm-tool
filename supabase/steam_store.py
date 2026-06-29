from __future__ import annotations

import html
import json
import re
import time
from typing import Any

import requests
from bs4 import BeautifulSoup


APP_DETAILS_URL = 'https://store.steampowered.com/api/appdetails'
STORE_APP_URL = 'https://store.steampowered.com/app/{appid}/'


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


def fetch_app_details(appid: int, *, country: str = 'us', language: str = 'english') -> dict[str, Any]:
    response = steam_session().get(
        APP_DETAILS_URL,
        params={'appids': str(appid), 'cc': country, 'l': language},
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json().get(str(appid), {'success': False})
    if not isinstance(payload, dict):
        return {'success': False}
    return payload


def fetch_store_page(appid: int, *, country: str = 'us', language: str = 'english') -> str:
    response = steam_session().get(
        STORE_APP_URL.format(appid=appid),
        params={'cc': country, 'l': language},
        timeout=45,
    )
    response.raise_for_status()
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


def fetch_page_signals(appid: int, *, country: str = 'us', language: str = 'english') -> dict[str, Any]:
    html_text = fetch_store_page(appid, country=country, language=language)
    soup = BeautifulSoup(html_text, 'html.parser')
    rich_tags = extract_rich_tags_from_scripts(html_text)
    return {
        'appid': appid,
        'basic_info': extract_basic_page_info(soup),
        'tags': rich_tags or extract_tags(soup),
        'more_like_this_appids': extract_more_like_this_appids(soup),
        'linked_apps': extract_linked_apps(soup, appid),
        'fetched_at_unix': int(time.time()),
    }


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
