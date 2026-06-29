import re
from typing import Optional, Tuple

STEAM_STORE_BASE = 'https://store.steampowered.com/app/'

STEAM_URL_PATTERNS = [
    re.compile(r'https?://store\.steampowered\.com/app/(?P<appid>\d+)(?:/|$)'),
    re.compile(r'https?://steamcommunity\.com/app/(?P<appid>\d+)(?:/|$)'),
]


def canonical_steam_url(appid: int) -> str:
    return f'{STEAM_STORE_BASE}{appid}/'


def resolve_steam_appid(value: str | int | None) -> Tuple[Optional[int], Optional[str]]:
    if value is None:
        return None, None

    text = str(value).strip()
    if not text:
        return None, None

    if text.isdigit():
        return int(text), canonical_steam_url(int(text))

    for regex in STEAM_URL_PATTERNS:
        match = regex.search(text)
        if match:
            appid = int(match.group('appid'))
            return appid, canonical_steam_url(appid)

    # Some Steam URLs include query strings and a trailing slash.
    match = re.search(r'/app/(\d+)', text)
    if match:
        appid = int(match.group(1))
        return appid, canonical_steam_url(appid)

    return None, None
