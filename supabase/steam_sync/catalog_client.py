from __future__ import annotations

import json
import os
import time
from typing import Any, Iterator

import requests


class SteamCatalogError(Exception):
    pass


class SteamRateLimitError(SteamCatalogError):
    pass


class SteamCatalogClient:
    BASE_URL = 'https://api.steampowered.com/IStoreService/GetAppList/v1/'
    USER_AGENT = 'steam-gtm-cache-sync/1.0'

    def __init__(self, api_key: str | None = None, timeout: int = 60, max_retries: int = 4):
        self.api_key = api_key or os.getenv('STEAM_API_KEY')
        if not self.api_key:
            raise RuntimeError('STEAM_API_KEY must be set in the environment')

        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update(
            {
                'User-Agent': self.USER_AGENT,
                'Accept': 'application/json',
            }
        )

    def iter_apps(
        self,
        *,
        if_modified_since: int | None = None,
        last_appid: int | None = None,
        max_results: int = 50000,
    ) -> Iterator[dict[str, Any]]:
        next_appid = last_appid
        while True:
            payload = {
                'include_games': True,
                'include_dlc': True,
                'include_software': True,
                'include_videos': True,
                'include_hardware': True,
                'max_results': max_results,
            }
            if if_modified_since is not None:
                payload['if_modified_since'] = int(if_modified_since)
            if next_appid is not None:
                payload['last_appid'] = int(next_appid)

            response = self._request(params={'key': self.api_key, 'input_json': json.dumps(payload)})
            apps = response.get('response', {}).get('apps')
            if not isinstance(apps, list):
                raise SteamCatalogError('Unexpected Steam catalog response structure')

            if not apps:
                break

            for raw_app in apps:
                yield self._normalize_app(raw_app)

            last = apps[-1]
            last_appid = last.get('appid')
            if last_appid is None:
                break
            if len(apps) < max_results:
                break
            next_appid = last_appid

    def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        last_exception: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.get(self.BASE_URL, params=params, timeout=self.timeout)
                if response.status_code == 429:
                    retry_after = self._get_retry_after(response, attempt)
                    if attempt == self.max_retries:
                        raise SteamRateLimitError('Steam catalog rate limited after retries')
                    time.sleep(retry_after)
                    continue
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else {}
            except requests.exceptions.RequestException as exc:
                last_exception = exc
                if hasattr(exc, 'response') and exc.response is not None and exc.response.status_code == 429:
                    if attempt == self.max_retries:
                        raise SteamRateLimitError('Steam catalog rate limited after retries') from exc
                    time.sleep(self._get_retry_after(exc.response, attempt))
                    continue
                if attempt == self.max_retries:
                    raise SteamCatalogError('Steam catalog request failed') from exc
                time.sleep(min(self._backoff_seconds(attempt), self.timeout))
            except ValueError as exc:
                raise SteamCatalogError('Unable to parse Steam catalog response as JSON') from exc
        raise SteamCatalogError('Steam catalog request failed') from last_exception

    def _get_retry_after(self, response: requests.Response, attempt: int) -> float:
        header_value = response.headers.get('Retry-After')
        if header_value and header_value.isdigit():
            return min(max(int(header_value), 1), int(self.timeout))
        return min(self._backoff_seconds(attempt), self.timeout)

    def _backoff_seconds(self, attempt: int) -> float:
        return min(120.0, 2.0 ** attempt)

    def _normalize_app(self, raw_app: dict[str, Any]) -> dict[str, Any]:
        appid = raw_app.get('appid')
        if isinstance(appid, str) and appid.isdigit():
            appid = int(appid)
        return {
            'appid': int(appid) if appid is not None else None,
            'name': raw_app.get('name') or None,
            'catalog_last_modified': self._safe_int(raw_app.get('last_modified')),
            'catalog_price_change_number': self._safe_int(raw_app.get('price_change_number')),
            'last_catalog_checked_at': None,
            'raw_catalog_json': raw_app,
        }

    def _safe_int(self, value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
