from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path('.env'))


class SupabaseClient:
    def __init__(self, url: str | None = None, key: str | None = None):
        self.url = (url or os.getenv('SUPABASE_URL') or '').rstrip('/')
        self.key = key or os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_ANON_KEY')

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

    def request(self, method: str, path: str, params: dict[str, Any] | None = None, json_body: Any | None = None, headers: dict[str, str] | None = None) -> Any:
        request_headers = {**self.headers, **(headers or {})}
        response = requests.request(method, self._url(path), params=params, json=json_body, headers=request_headers)
        response.raise_for_status()
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

    def insert(self, table: str, records: Any, upsert: bool = False, on_conflict: str | None = None, returning: str = 'representation') -> Any:
        params: dict[str, str] = {}
        if on_conflict:
            params['on_conflict'] = on_conflict
        headers = {}
        if returning:
            headers['Prefer'] = f'return={returning}'
        if upsert:
            prefer = headers.get('Prefer', '')
            headers['Prefer'] = f'{prefer}, resolution=merge-duplicates' if prefer else 'resolution=merge-duplicates'
        return self.request('POST', table, params=params or None, json_body=records, headers=headers)

    def update(self, table: str, key_filters: dict[str, str], updates: dict[str, Any], returning: str = 'representation') -> Any:
        params = {**key_filters}
        headers = {'Prefer': f'return={returning}'} if returning else {}
        return self.request('PATCH', table, params=params, json_body=updates, headers=headers)

    def delete(self, table: str, filters: dict[str, str]) -> Any:
        return self.request('DELETE', table, params=filters)
