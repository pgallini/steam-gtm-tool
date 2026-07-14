from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .config import SteamSyncConfig
from ..client import SupabaseClient


class SteamSyncRepository:
    def __init__(self, client: SupabaseClient | None = None):
        self.client = client or SupabaseClient()

    def _utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_sync_run(
        self,
        sync_type: str,
        country_code: str,
        language_code: str,
        config: dict[str, Any] | None = None,
        status: str = 'queued',
    ) -> dict[str, Any]:
        started_at = self._utc_now_iso() if status == 'running' else None
        payload = {
            'sync_type': sync_type,
            'status': status,
            'country_code': country_code,
            'language_code': language_code,
            'config': config or {},
            'started_at': started_at,
            'completed_at': None,
            'failed_at': None,
            'summary': {},
        }
        response = self.client.insert('steam_sync_runs', payload, returning='representation')
        return response[0] if isinstance(response, list) else response

    def get_sync_run(self, run_id: str) -> dict[str, Any] | None:
        rows = self.client.select('steam_sync_runs', '*', {'id': f'eq.{run_id}'})
        return rows[0] if rows else None

    def update_sync_run(self, run_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        updates['updated_at'] = self._utc_now_iso()
        response = self.client.update('steam_sync_runs', {'id': f'eq.{run_id}'}, updates, returning='representation')
        if isinstance(response, list):
            return response[0] if response else None
        return response

    def complete_sync_run(self, run_id: str, summary: dict[str, Any] | None = None, status: str = 'completed') -> dict[str, Any] | None:
        updates = {'status': status, 'completed_at': self._utc_now_iso(), 'summary': summary or {}}
        return self.update_sync_run(run_id, updates)

    def fail_sync_run(self, run_id: str, failure_message: str) -> dict[str, Any] | None:
        updates = {'status': 'failed', 'failed_at': self._utc_now_iso(), 'failure_message': failure_message}
        return self.update_sync_run(run_id, updates)

    def create_sync_items(self, sync_run_id: str, appids: list[int], operation: str = 'details') -> list[dict[str, Any]]:
        if not appids:
            return []
        payloads = [
            {
                'sync_run_id': sync_run_id,
                'appid': appid,
                'operation': operation,
                'status': 'queued',
                'attempt_count': 0,
            }
            for appid in appids
        ]
        response = self.client.insert('steam_sync_items', payloads, returning='representation')
        return response if isinstance(response, list) else [response]

    def update_sync_item(self, item_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        updates['updated_at'] = self._utc_now_iso()
        response = self.client.update('steam_sync_items', {'id': f'eq.{item_id}'}, updates, returning='representation')
        if isinstance(response, list):
            return response[0] if response else None
        return response

    def get_sync_items(self, sync_run_id: str, statuses: list[str]) -> list[dict[str, Any]]:
        if not statuses:
            return []
        status_filter = f'in.({','.join(statuses)})'
        return self.client.select('steam_sync_items', '*', {'sync_run_id': f'eq.{sync_run_id}', 'status': status_filter})

    def get_existing_appids(self, appids: list[int]) -> set[int]:
        if not appids:
            return set()
        filter_value = f'in.({','.join(str(appid) for appid in appids)})'
        rows = self.client.select('steam_apps', 'appid', {'appid': filter_value})
        return {int(row['appid']) for row in rows if row.get('appid') is not None}

    def get_max_catalog_last_modified(self) -> int | None:
        rows = self.client.select('steam_apps', 'catalog_last_modified', {'order': 'catalog_last_modified.desc', 'limit': '1'})
        if not rows:
            return None
        value = rows[0].get('catalog_last_modified')
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def get_app_by_appid(self, appid: int) -> dict[str, Any] | None:
        rows = self.client.select('steam_apps', '*', {'appid': f'eq.{appid}'})
        return rows[0] if rows else None

    def upsert_steam_app(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        response = self.client.insert('steam_apps', payload, upsert=True, on_conflict='appid', returning='representation')
        if isinstance(response, list):
            return response[0] if response else None
        return response

    def upsert_catalog_apps(self, records: list[dict[str, Any]], existing_appids: set[int] | None = None) -> int:
        existing_appids = existing_appids or set()
        sanitized = []
        for record in records:
            appid = int(record['appid'])
            item: dict[str, Any] = {'appid': appid}
            name = str(record.get('name')).strip() if record.get('name') is not None else ''
            if name:
                item['name'] = name
            elif appid not in existing_appids:
                item['name'] = f'Steam App {appid}'
            if record.get('catalog_last_modified') is not None:
                item['catalog_last_modified'] = int(record['catalog_last_modified'])
            if record.get('catalog_price_change_number') is not None:
                item['catalog_price_change_number'] = int(record['catalog_price_change_number'])
            item['last_catalog_checked_at'] = self._utc_now_iso()
            sanitized.append(item)
        return self.client.upsert_batches('steam_apps', sanitized, on_conflict='appid', batch_size=500)

    def get_explicitly_referenced_appids(self) -> list[int]:
        appids = set()
        games = self.client.select('games', 'steam_appid', {'steam_appid': 'not.is.null'})
        for row in games:
            if row.get('steam_appid') is not None:
                appids.add(int(row['steam_appid']))

        candidate_appids = self.client.select('run_candidates', 'steam_appid', {'steam_appid': 'not.is.null'})
        for row in candidate_appids:
            if row.get('steam_appid') is not None:
                appids.add(int(row['steam_appid']))
        return sorted(appids)

    def get_active_reference_appids(self) -> list[int]:
        active_runs = self.client.select('research_runs', 'id', {'status': 'in.(draft,queued,running,needs_review)'})
        active_ids = [row['id'] for row in active_runs if row.get('id')]
        if not active_ids:
            return []
        filter_value = f'in.({','.join(active_ids)})'
        candidates = self.client.select('run_candidates', 'steam_appid', {'steam_appid': 'not.is.null', 'run_id': filter_value})
        appids = {int(row['steam_appid']) for row in candidates if row.get('steam_appid') is not None}
        return sorted(appids)

    def get_latest_snapshot(self, appid: int) -> dict[str, Any] | None:
        rows = self.client.select('steam_app_snapshots', '*', {'appid': f'eq.{appid}', 'order': 'fetched_at.desc', 'limit': '1'})
        return rows[0] if rows else None

    def get_incomplete_sync_items(self, run_id: str) -> list[dict[str, Any]]:
        return self.client.select('steam_sync_items', '*', {'sync_run_id': f'eq.{run_id}', 'status': 'in.(queued,running,retry_pending)'})
 
    def get_latest_sync_run(self, sync_type: str | None = None) -> dict[str, Any] | None:
        filters = {'order': 'created_at.desc', 'limit': '1'}
        if sync_type:
            filters['sync_type'] = f'eq.{sync_type}'
        rows = self.client.select('steam_sync_runs', '*', filters)
        return rows[0] if rows else None
 
    def get_active_sync_runs(self) -> list[dict[str, Any]]:
        return self.client.select('steam_sync_runs', '*', {'status': 'eq.running', 'order': 'created_at.desc'})
 
    def get_sync_items_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return self.client.select('steam_sync_items', '*', {'sync_run_id': f'eq.{run_id}', 'order': 'created_at.asc'})
 
    def insert_snapshot(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        response = self.client.insert('steam_app_snapshots', payload, returning='representation')
        if isinstance(response, list):
            return response[0] if response else None
        return response

    def get_stale_games_for_enrichment(self, threshold_iso: str, limit: int) -> list[dict[str, Any]]:
        return self.client.select_all(
            'steam_apps',
            select='appid,app_type,last_successfully_fetched_at,fetch_status,next_refresh_at,enrichment_priority,catalog_last_modified',
            filters={
                'app_type': 'eq.game',
                'or': f'(last_successfully_fetched_at.is.null,last_successfully_fetched_at.lt.{threshold_iso})',
                'fetch_status': 'not.in.(skipped_non_game,unavailable)',
            },
            order='last_successfully_fetched_at.asc,appid.asc',
        )[:limit]
 
    def get_force_games_for_enrichment(self, limit: int) -> list[dict[str, Any]]:
        return self.client.select_all(
            'steam_apps',
            select='appid,app_type,last_successfully_fetched_at,fetch_status,next_refresh_at,enrichment_priority,catalog_last_modified',
            filters={
                'app_type': 'eq.game',
                'fetch_status': 'not.in.(skipped_non_game,unavailable)',
            },
            order='last_successfully_fetched_at.asc,appid.asc',
        )[:limit]
 
    def get_unclassified_catalog_apps(self, limit: int) -> list[dict[str, Any]]:
        return self.client.select_all(
            'steam_apps',
            select='appid,app_type,fetch_status,catalog_last_modified,enrichment_priority',
            filters={
                'app_type': 'is.null',
                'fetch_status': 'eq.catalog_only',
            },
            order='enrichment_priority.asc,catalog_last_modified.desc,appid.asc',
        )[:limit]

    def get_retryable_failed_apps(self, now_iso: str, limit: int) -> list[dict[str, Any]]:
        return self.client.select_all(
            'steam_apps',
            select='appid,fetch_status,next_refresh_at,consecutive_fetch_failures',
            filters={
                'fetch_status': 'in.(failed,rate_limited)',
                'next_refresh_at': f'lte.{now_iso}',
            },
            order='next_refresh_at.asc,appid.asc',
        )[:limit]

    def mark_app_fetching(self, appid: int) -> dict[str, Any] | None:
        updates = {
            'fetch_status': 'fetching',
            'last_detail_checked_at': self._utc_now_iso(),
        }
        return self._update_app(appid, updates)

    def mark_app_enriched(self, appid: int, updates: dict[str, Any]) -> dict[str, Any] | None:
        updates = {
            **updates,
            'fetch_status': 'enriched',
            'last_detail_checked_at': self._utc_now_iso(),
            'last_successfully_fetched_at': self._utc_now_iso(),
            'consecutive_fetch_failures': 0,
            'fetch_error': None,
        }
        return self._update_app(appid, updates)

    def mark_app_skipped_non_game(self, appid: int, updates: dict[str, Any] | None = None) -> dict[str, Any] | None:
        payload = {'fetch_status': 'skipped_non_game', 'last_detail_checked_at': self._utc_now_iso()}
        if updates:
            payload.update(updates)
        return self._update_app(appid, payload)

    def mark_app_failed(self, appid: int, error: str, next_retry_at: str | None = None) -> dict[str, Any] | None:
        app = self.client.select('steam_apps', 'consecutive_fetch_failures', {'appid': f'eq.{appid}'})
        failures = int(app[0].get('consecutive_fetch_failures', 0)) if app else 0
        payload = {
            'fetch_status': 'failed',
            'fetch_error': error,
            'consecutive_fetch_failures': failures + 1,
            'last_detail_checked_at': self._utc_now_iso(),
        }
        if next_retry_at is not None:
            payload['next_refresh_at'] = next_retry_at
        return self._update_app(appid, payload)

    def _update_app(self, appid: int, updates: dict[str, Any]) -> dict[str, Any] | None:
        response = self.client.update('steam_apps', {'appid': f'eq.{appid}'}, updates, returning='representation')
        if isinstance(response, list):
            return response[0] if response else None
        return response
