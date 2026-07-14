from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import SteamSyncConfig
from .catalog_client import SteamCatalogClient, SteamRateLimitError
from .logging import logger_for
from .repository import SteamSyncRepository
from .selection import select_appids_for_enrichment
from .snapshots import make_content_hash, snapshot_payload, should_create_snapshot
from ..steam_store import fetch_app_details_result, fetch_page_signals, normalize_app_details


class SteamSyncService:
    def __init__(self, config: SteamSyncConfig | None = None, repository: SteamSyncRepository | None = None):
        self.config = config or SteamSyncConfig.load_from_environment()
        self.repo = repository or SteamSyncRepository()
        self.catalog_client = SteamCatalogClient(timeout=60, max_retries=self.config.max_retries)
        self.logger = logger_for('steam_sync')

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _is_game_type(self, app_type: str | None) -> bool:
        return isinstance(app_type, str) and app_type.lower() == 'game'

    def _compute_next_retry_at(self, failures: int) -> str:
        if failures <= 1:
            delay = timedelta(hours=24)
        elif failures == 2:
            delay = timedelta(days=3)
        elif failures == 3:
            delay = timedelta(days=7)
        else:
            delay = timedelta(days=30)
        return (datetime.now(timezone.utc) + delay).isoformat()

    def _normalize_app_payload(
        self,
        detail: dict[str, Any],
        page_signals: dict[str, Any],
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = existing or {}
        release = detail.get('release_date') or {}
        price = detail.get('price_overview') or {}
        recommendations = detail.get('recommendations') or {}
        metacritic = detail.get('metacritic') or {}
        basic = page_signals.get('basic_info') or {}
        tags = page_signals.get('tags') or []
        tag_names = [str(tag.get('name')) for tag in tags if isinstance(tag, dict) and tag.get('name')]
        tag_ids = [int(tag.get('tagid')) for tag in tags if isinstance(tag, dict) and str(tag.get('tagid') or '').isdigit()]
        if not tag_names and existing.get('tags'):
            tag_names = existing.get('tags') or []
            tag_ids = existing.get('tag_ids') or []

        payload: dict[str, Any] = {
            'appid': detail.get('appid'),
            'name': detail.get('name') or basic.get('page_title') or existing.get('name'),
            'app_type': detail.get('type'),
            'developer': '; '.join(detail.get('developers') or []) if detail.get('developers') else None,
            'publisher': '; '.join(detail.get('publishers') or []) if detail.get('publishers') else None,
            'release_date_text': release.get('date') if isinstance(release, dict) else None,
            'release_date': self._parse_release_date(release),
            'release_status': self._normalize_release_status(detail),
            'is_free': detail.get('is_free'),
            'price_initial_cents': price.get('initial') if isinstance(price, dict) else None,
            'price_final_cents': price.get('final') if isinstance(price, dict) else None,
            'discount_percent': price.get('discount_percent') if isinstance(price, dict) else None,
            'currency': price.get('currency') if isinstance(price, dict) else None,
            'review_summary': basic.get('review_summary'),
            'recent_review_summary': basic.get('recent_review_summary'),
            'review_count': recommendations.get('total') if isinstance(recommendations, dict) else None,
            'metacritic_score': metacritic.get('score') if isinstance(metacritic, dict) else None,
            'short_description': detail.get('short_description'),
            'genres': [str(item.get('description')) for item in detail.get('genres') or [] if isinstance(item, dict) and item.get('description')],
            'categories': [str(item.get('description')) for item in detail.get('categories') or [] if isinstance(item, dict) and item.get('description')],
            'tags': tag_names,
            'tag_ids': tag_ids,
            'last_detail_checked_at': self._now_iso(),
            'last_successfully_fetched_at': self._now_iso(),
            'next_refresh_at': (datetime.now(timezone.utc) + timedelta(days=self.config.stale_after_days)).isoformat(),
            'is_available': True,
            'header_image_url': detail.get('header_image') or existing.get('header_image_url'),
            'supports_windows': bool(detail.get('platforms', {}).get('windows')) if isinstance(detail.get('platforms'), dict) else False,
            'supports_mac': bool(detail.get('platforms', {}).get('mac')) if isinstance(detail.get('platforms'), dict) else False,
            'supports_linux': bool(detail.get('platforms', {}).get('linux')) if isinstance(detail.get('platforms'), dict) else False,
            'country_code': self.config.country,
            'language_code': self.config.language,
            'raw_appdetails_json': detail,
            'raw_page_signals_json': page_signals,
        }
        payload['content_hash'] = make_content_hash(payload)
        payload['data_completeness_score'] = self._compute_completeness_score(payload)
        return payload

    def _normalize_release_status(self, detail: dict[str, Any]) -> str | None:
        release = detail.get('release_date') or {}
        if isinstance(release, dict) and release.get('coming_soon') is True:
            return 'upcoming'
        if detail.get('success') is False:
            return 'fetch_failed'
        if detail.get('type'):
            return 'released'
        return 'unknown'
 
    def _parse_release_date(self, release: Any) -> str | None:
        if not isinstance(release, dict):
            return None
        date_text = release.get('date')
        if not isinstance(date_text, str):
            return None
        cleaned = date_text.strip()
        lowered = cleaned.lower()
        ambiguous_terms = ['coming soon', 'to be announced', 'tba', 'early access', 'q1', 'q2', 'q3', 'q4', 'spring', 'summer', 'fall', 'autumn', 'winter']
        if any(term in lowered for term in ambiguous_terms):
            return None
        if lowered.isdigit() and len(lowered) == 4:
            return None
        parse_formats = ['%d %b, %Y', '%b %d, %Y', '%B %d, %Y', '%d %B, %Y']
        for fmt in parse_formats:
            try:
                return datetime.strptime(cleaned, fmt).date().isoformat()
            except ValueError:
                continue
        return None
 
    def _compute_completeness_score(self, payload: dict[str, Any]) -> int:
        score = 0
        if payload.get('name'):
            score += 5
        if payload.get('app_type'):
            score += 10
        if payload.get('developer'):
            score += 10
        if payload.get('publisher'):
            score += 10
        if payload.get('release_status') or payload.get('release_date_text'):
            score += 10
        if payload.get('is_free') is not None or payload.get('price_initial_cents') is not None or payload.get('price_final_cents') is not None:
            score += 10
        if payload.get('short_description'):
            score += 10
        if payload.get('genres'):
            score += 10
        if payload.get('categories'):
            score += 5
        if payload.get('tags'):
            score += 10
        if payload.get('review_count') is not None or payload.get('review_summary') or payload.get('recent_review_summary'):
            score += 5
        if payload.get('supports_windows') or payload.get('supports_mac') or payload.get('supports_linux'):
            score += 5
        return min(score, 100)

    def _should_fetch_page_signals(self, explicit: bool, current_tags: list[Any], include_page_signals: bool) -> bool:
        if not include_page_signals:
            return False
        if explicit:
            return self.config.page_signal_enrichment_enabled
        return self.config.page_signal_enrichment_enabled and not current_tags

    def _should_skip_non_game(self, detail: dict[str, Any], explicit: bool) -> bool:
        return not explicit and not self._is_game_type(detail.get('type'))

    def _ensure_run(self, sync_type: str, resume_run_id: str | None, config: dict[str, Any]) -> dict[str, Any]:
        if resume_run_id:
            existing = self.repo.get_sync_run(resume_run_id)
            if existing:
                self.repo.update_sync_run(resume_run_id, {'status': 'running'})
                return existing
        return self.repo.create_sync_run(sync_type, self.config.country, self.config.language, config, status='running')

    def _process_items(self, run_id: str, items: list[dict[str, Any]], explicit_appids: set[int], include_page_signals: bool) -> dict[str, Any]:
        completed = 0
        failed = 0
        skipped = 0
        rate_limited = 0

        for item in items:
            appid = int(item['appid'])
            if item['status'] == 'running':
                started_at = item.get('started_at')
                if started_at:
                    try:
                        started = datetime.fromisoformat(started_at)
                        if datetime.now(timezone.utc) - started < timedelta(hours=2):
                            continue
                    except ValueError:
                        pass
            self.repo.update_sync_item(item['id'], {'status': 'running', 'started_at': self._now_iso(), 'attempt_count': item.get('attempt_count', 0) + 1})
            self.repo.mark_app_fetching(appid)
            explicit = appid in explicit_appids
            try:
                result = fetch_app_details_result(appid, country=self.config.country, language=self.config.language, run_id=run_id)
                if not result.success:
                    message = result.error or 'unknown_error'
                    next_retry_at = self._compute_next_retry_at(item.get('attempt_count', 0) + 1)
                    self.repo.mark_app_failed(appid, message, next_retry_at)
                    self.repo.update_sync_item(item['id'], {'status': 'failed', 'completed_at': self._now_iso(), 'response_status': result.http_status, 'error_message': message})
                    failed += 1
                    if result.status == 'steam_rate_limited':
                        rate_limited += 1
                        break
                    continue

                detail = normalize_app_details(appid, result.to_dict())
                existing = self.repo.get_app_by_appid(appid) or {}
                if self._should_fetch_page_signals(explicit, existing.get('tags') or [], include_page_signals):
                    page_signals = fetch_page_signals(appid, country=self.config.country, language=self.config.language, run_id=run_id)
                else:
                    page_signals = existing.get('raw_page_signals_json') or {}
                payload = self._normalize_app_payload(detail, page_signals, existing=existing)
                app_record = self.repo.upsert_steam_app(payload)
                if self._should_skip_non_game(detail, explicit):
                    self.repo.mark_app_skipped_non_game(appid, {'raw_appdetails_json': detail, 'raw_page_signals_json': page_signals})
                    self.repo.update_sync_item(item['id'], {'status': 'skipped', 'completed_at': self._now_iso(), 'details': {'reason': 'non_game'}})
                    skipped += 1
                else:
                    latest_snapshot = self.repo.get_latest_snapshot(appid)
                    if should_create_snapshot(payload, latest_snapshot):
                        self.repo.insert_snapshot(snapshot_payload(payload))
                    self.repo.mark_app_enriched(appid, {
                        'content_hash': payload['content_hash'],
                        'data_completeness_score': payload['data_completeness_score'],
                        'next_refresh_at': payload['next_refresh_at'],
                        'raw_appdetails_json': payload.get('raw_appdetails_json'),
                        'raw_page_signals_json': payload.get('raw_page_signals_json'),
                    })
                    self.repo.update_sync_item(item['id'], {'status': 'completed', 'completed_at': self._now_iso(), 'response_status': 200, 'details': {'name': payload.get('name')}})
                    completed += 1
            except SteamRateLimitError as exc:
                message = str(exc)
                self.repo.mark_app_failed(appid, message, self._compute_next_retry_at(item.get('attempt_count', 0) + 1))
                self.repo.update_sync_item(item['id'], {'status': 'rate_limited', 'completed_at': self._now_iso(), 'error_message': message})
                rate_limited += 1
                break
            except Exception as exc:
                message = str(exc)
                self.repo.mark_app_failed(appid, message, self._compute_next_retry_at(item.get('attempt_count', 0) + 1))
                self.repo.update_sync_item(item['id'], {'status': 'failed', 'completed_at': self._now_iso(), 'error_message': message})
                failed += 1
            finally:
                time.sleep(self.config.request_delay_seconds)

        return {
            'detail_records_attempted': len(items),
            'detail_records_enriched': completed,
            'detail_records_failed': failed,
            'detail_records_skipped': skipped,
            'detail_records_rate_limited': rate_limited,
        }

    def sync_catalog(self) -> dict[str, Any]:
        if self.config.dry_run:
            return {'dry_run': True}
        run = self.repo.create_sync_run('catalog', self.config.country, self.config.language, {'dry_run': self.config.dry_run}, status='running')
        run_id = run['id']
        try:
            cursor = self.repo.get_max_catalog_last_modified()
            received = 0
            inserted = 0
            updated = 0
            batch: list[dict[str, Any]] = []
            for record in self.catalog_client.iter_apps(if_modified_since=cursor, max_results=self.config.catalog_batch_size):
                received += 1
                batch.append(record)
                if len(batch) >= self.config.supabase_batch_size:
                    existing = self.repo.get_existing_appids([item['appid'] for item in batch])
                    updated += len(existing)
                    inserted += len(batch) - len(existing)
                    self.repo.upsert_catalog_apps(batch, existing_appids=existing)
                    batch = []
            if batch:
                existing = self.repo.get_existing_appids([item['appid'] for item in batch])
                updated += len(existing)
                inserted += len(batch) - len(existing)
                self.repo.upsert_catalog_apps(batch, existing_appids=existing)
            self.repo.complete_sync_run(run_id, {
                'catalog_records_received': received,
                'catalog_records_inserted': inserted,
                'catalog_records_updated': updated,
            })
            return {'run_id': run_id, 'success': True, 'received': received, 'inserted': inserted, 'updated': updated}
        except Exception as exc:
            self.repo.fail_sync_run(run_id, str(exc))
            raise

    def sync_details(
        self,
        limit: int | None = None,
        include_page_signals: bool = False,
        force: bool = False,
        resume_run_id: str | None = None,
    ) -> dict[str, Any]:
        if self.config.dry_run:
            appids = select_appids_for_enrichment(self.repo, self.config, limit or self.config.detail_batch_limit)
            return {'dry_run': True, 'selected': appids[: limit] if limit else appids}

        run = self._ensure_run('details', resume_run_id, {'detail_limit': limit, 'include_page_signals': include_page_signals, 'force': force})
        explicit_appids = set(self.repo.get_explicitly_referenced_appids())
        items = self.repo.get_incomplete_sync_items(run['id'])
        if not items:
            appids = select_appids_for_enrichment(self.repo, self.config, limit or self.config.detail_batch_limit, force=force)
            if limit is not None:
                appids = appids[:limit]
            items = self.repo.create_sync_items(run['id'], appids)

        summary = self._process_items(run['id'], items, explicit_appids, include_page_signals)
        self.repo.complete_sync_run(run['id'], summary)
        return {'run_id': run['id'], **summary}

    def sync_app(self, appid: int, include_page_signals: bool = False, force: bool = False) -> dict[str, Any]:
        if self.config.dry_run:
            return {'dry_run': True, 'appid': appid}
        run = self.repo.create_sync_run('manual_app', self.config.country, self.config.language, {'appid': appid, 'include_page_signals': include_page_signals, 'force': force}, status='running')
        items = self.repo.create_sync_items(run['id'], [appid])
        summary = self._process_items(run['id'], items, {appid}, include_page_signals)
        self.repo.complete_sync_run(run['id'], summary)
        return {'run_id': run['id'], **summary}
 
    def get_status(self, run_id: str | None = None) -> dict[str, Any]:
        if run_id:
            run = self.repo.get_sync_run(run_id)
        else:
            run = self.repo.get_latest_sync_run('weekly') or self.repo.get_latest_sync_run()
 
        active_runs = self.repo.get_active_sync_runs()
        result: dict[str, Any] = {
            'run': run,
            'active_runs': active_runs,
        }
        if run:
            items = self.repo.get_sync_items_for_run(run['id'])
            status_counts: dict[str, int] = {}
            for item in items:
                status_counts[item.get('status') or 'unknown'] = status_counts.get(item.get('status') or 'unknown', 0) + 1
            result['item_counts'] = status_counts
            result['item_count'] = len(items)
        return result
 
    def retry_failures(self, limit: int | None = None) -> dict[str, Any]:
        if self.config.dry_run:
            appids = [int(item['appid']) for item in self.repo.get_retryable_failed_apps(self._now_iso(), limit or self.config.detail_batch_limit)]
            return {'dry_run': True, 'selected': appids}
        run = self.repo.create_sync_run('retry_failures', self.config.country, self.config.language, {'limit': limit}, status='running')
        failed_apps = self.repo.get_retryable_failed_apps(self._now_iso(), limit or self.config.detail_batch_limit)
        if not failed_apps:
            self.repo.complete_sync_run(run['id'], {'detail_records_attempted': 0})
            return {'run_id': run['id'], 'retried': 0}
        appids = [int(item['appid']) for item in failed_apps]
        items = self.repo.create_sync_items(run['id'], appids)
        summary = self._process_items(run['id'], items, set(appids), True)
        self.repo.complete_sync_run(run['id'], summary)
        return {'run_id': run['id'], **summary}

    def sync_weekly(self, limit: int | None = None, include_page_signals: bool = False) -> dict[str, Any]:
        if self.config.dry_run:
            return {'dry_run': True}
 
        weekly_run = self.repo.create_sync_run(
            'weekly',
            self.config.country,
            self.config.language,
            {
                'detail_limit': limit,
                'include_page_signals': include_page_signals,
            },
            status='running',
        )
        weekly_summary: dict[str, Any] = {}
        status = 'completed'
        try:
            catalog_summary = self.sync_catalog()
            detail_summary = self.sync_details(limit=limit or self.config.detail_batch_limit, include_page_signals=include_page_signals)
            retry_summary = self.retry_failures(limit=limit or self.config.detail_batch_limit)
            weekly_summary = {
                'catalog': catalog_summary,
                'details': detail_summary,
                'retry_failures': retry_summary,
            }
            if (
                (isinstance(catalog_summary, dict) and catalog_summary.get('success') is False)
                or (isinstance(detail_summary, dict) and detail_summary.get('success') is False)
                or (isinstance(retry_summary, dict) and retry_summary.get('success') is False)
            ):
                status = 'completed_with_errors'
            self.repo.complete_sync_run(weekly_run['id'], weekly_summary, status=status)
            return weekly_summary
        except Exception as exc:
            self.repo.fail_sync_run(weekly_run['id'], str(exc))
            raise
