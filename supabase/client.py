from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests
from requests.exceptions import HTTPError
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path('.env'))

DEFAULT_TIMEOUT_SECONDS = int(os.getenv('SUPABASE_HTTP_TIMEOUT_SECONDS', '60'))


class SupabaseClient:
    def __init__(self, url: str | None = None, key: str | None = None, timeout: int | None = None):
        self.url = (url or os.getenv('SUPABASE_URL') or '').rstrip('/')
        self.key = key or os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_ANON_KEY')
        self.timeout = timeout or DEFAULT_TIMEOUT_SECONDS

        if not self.url:
            raise RuntimeError('SUPABASE_URL must be set in the environment or .env file')
        if not self.key:
            raise RuntimeError('SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY must be set in the environment or .env file')

        self.headers = {
            'apikey': self.key,
            'Authorization': f'Bearer {self.key}',
            'Content-Type': 'application/json',
        }

    def _url(self, path: str) -> str:
        path = path.lstrip('/')
        return f'{self.url}/rest/v1/{path}'

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        headers: dict[str, str] | None = None,
        raw_response: bool = False,
    ) -> Any:
        request_headers = {**self.headers, **(headers or {})}
        response = requests.request(
            method,
            self._url(path),
            params=params,
            json=json_body,
            headers=request_headers,
            timeout=self.timeout,
        )
        if raw_response:
            return response
        try:
            response.raise_for_status()
        except HTTPError as exc:
            body = response.text
            raise RuntimeError(
                f'HTTP {response.status_code} error for {method} {self._url(path)}: {body}'
            ) from exc
        if response.text:
            try:
                return response.json()
            except ValueError:
                return response.text
        return None

    def select(self, table: str, select: str = '*', filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
        params = {'select': select}
        if filters:
            params.update(filters)
        return self.request('GET', table, params=params)

    def select_all(
        self,
        table: str,
        *,
        select: str = '*',
        filters: dict[str, str] | None = None,
        page_size: int = 1000,
        order: str | None = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        offset = 0
        while True:
            params = {'select': select}
            if filters:
                params.update(filters)
            if order:
                params['order'] = order
            headers = {'Range': f'{offset}-{offset + page_size - 1}'}
            page = self.request('GET', table, params=params, headers=headers)
            if not isinstance(page, list):
                break
            results.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return results

    def insert(
        self,
        table: str,
        records: Any,
        upsert: bool = False,
        on_conflict: str | None = None,
        returning: str = 'representation',
    ) -> Any:
        params: dict[str, str] = {}
        if on_conflict:
            params['on_conflict'] = on_conflict
        headers: dict[str, str] = {}
        if returning:
            headers['Prefer'] = f'return={returning}'
        if upsert:
            prefer = headers.get('Prefer', '')
            headers['Prefer'] = f'{prefer}, resolution=merge-duplicates' if prefer else 'resolution=merge-duplicates'
        return self.request('POST', table, params=params or None, json_body=records, headers=headers)

    def upsert_batches(
        self,
        table: str,
        records: list[dict[str, Any]],
        *,
        on_conflict: str,
        batch_size: int = 500,
        returning: str = 'minimal',
    ) -> int:
        submitted = 0
        for start in range(0, len(records), batch_size):
            batch = records[start : start + batch_size]
            submitted += self._upsert_batch(table, batch, on_conflict=on_conflict, returning=returning, start_index=start)
        return submitted
 
    def _upsert_batch(
        self,
        table: str,
        records: list[dict[str, Any]],
        *,
        on_conflict: str,
        returning: str,
        start_index: int = 0,
    ) -> int:
        if not records:
            return 0
        try:
            self.insert(table, records, upsert=True, on_conflict=on_conflict, returning=returning)
            return len(records)
        except Exception as exc:
            if len(records) == 1:
                raise RuntimeError(
                    f'Failed upsert batch for {table} at record {start_index}: {exc}. Record payload: {records[0]}'
                ) from exc
            mid = len(records) // 2
            first_half = records[:mid]
            second_half = records[mid:]
            count = self._upsert_batch(table, first_half, on_conflict=on_conflict, returning=returning, start_index=start_index)
            count += self._upsert_batch(table, second_half, on_conflict=on_conflict, returning=returning, start_index=start_index + mid)
            return count

    def update(self, table: str, key_filters: dict[str, str], updates: dict[str, Any], returning: str = 'representation') -> Any:
        params = {**key_filters}
        headers = {'Prefer': f'return={returning}'} if returning else {}
        return self.request('PATCH', table, params=params, json_body=updates, headers=headers)

    def delete(self, table: str, filters: dict[str, str]) -> Any:
        return self.request('DELETE', table, params=filters)

    def count(self, table: str, filters: dict[str, str] | None = None) -> int:
        params = {'select': 'id'}
        if filters:
            params.update(filters)
        response = self.request('GET', table, params=params, headers={'Prefer': 'count=exact'})
        if isinstance(response, list):
            return len(response)
        return 0
