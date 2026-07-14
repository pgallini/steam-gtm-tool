from __future__ import annotations

import os
from dataclasses import dataclass


def _parse_bool(value: str | bool | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    return normalized in {'1', 'true', 'yes', 'y', 'on'}


def _parse_int(value: str | int | None, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if value is None or value == '':
        result = default
    elif isinstance(value, int):
        result = value
    else:
        try:
            result = int(value)
        except ValueError as exc:
            raise ValueError(f'Invalid integer value: {value}') from exc
    if minimum is not None and result < minimum:
        raise ValueError(f'Value must be >= {minimum}: {result}')
    if maximum is not None and result > maximum:
        raise ValueError(f'Value must be <= {maximum}: {result}')
    return result


def _parse_float(value: str | float | None, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if value is None or value == '':
        result = default
    elif isinstance(value, float):
        result = value
    else:
        try:
            result = float(value)
        except ValueError as exc:
            raise ValueError(f'Invalid float value: {value}') from exc
    if minimum is not None and result < minimum:
        raise ValueError(f'Value must be >= {minimum}: {result}')
    if maximum is not None and result > maximum:
        raise ValueError(f'Value must be <= {maximum}: {result}')
    return result


@dataclass(frozen=True)
class SteamSyncConfig:
    country: str = 'us'
    language: str = 'english'

    catalog_batch_size: int = 1000
    supabase_batch_size: int = 500

    detail_batch_limit: int = 500
    request_delay_seconds: float = 1.5

    max_retries: int = 4
    retry_base_seconds: float = 2.0
    retry_max_seconds: float = 120.0

    stale_after_days: int = 7
    failed_retry_after_hours: int = 24

    snapshot_enabled: bool = True
    page_signal_enrichment_enabled: bool = True

    dry_run: bool = False

    @classmethod
    def load_from_environment(cls) -> 'SteamSyncConfig':
        return cls(
            country=os.getenv('STEAM_SYNC_COUNTRY', 'us').strip() or 'us',
            language=os.getenv('STEAM_SYNC_LANGUAGE', 'english').strip() or 'english',
            catalog_batch_size=_parse_int(os.getenv('STEAM_SYNC_CATALOG_BATCH_SIZE'), 1000, minimum=1),
            supabase_batch_size=_parse_int(os.getenv('STEAM_SYNC_SUPABASE_BATCH_SIZE'), 500, minimum=1, maximum=1000),
            detail_batch_limit=_parse_int(os.getenv('STEAM_SYNC_DETAIL_LIMIT'), 500, minimum=1),
            request_delay_seconds=_parse_float(os.getenv('STEAM_SYNC_REQUEST_DELAY_SECONDS'), 1.5, minimum=0.0),
            max_retries=_parse_int(os.getenv('STEAM_SYNC_MAX_RETRIES'), 4, minimum=1),
            retry_base_seconds=_parse_float(os.getenv('STEAM_SYNC_RETRY_BASE_SECONDS'), 2.0, minimum=0.0),
            retry_max_seconds=_parse_float(os.getenv('STEAM_SYNC_RETRY_MAX_SECONDS'), 120.0, minimum=0.0),
            stale_after_days=_parse_int(os.getenv('STEAM_SYNC_STALE_AFTER_DAYS'), 7, minimum=0),
            failed_retry_after_hours=_parse_int(os.getenv('STEAM_SYNC_FAILED_RETRY_HOURS'), 24, minimum=0),
            snapshot_enabled=_parse_bool(os.getenv('STEAM_SYNC_SNAPSHOT_ENABLED'), True),
            page_signal_enrichment_enabled=_parse_bool(os.getenv('STEAM_SYNC_PAGE_SIGNALS_ENABLED'), True),
            dry_run=_parse_bool(os.getenv('STEAM_SYNC_DRY_RUN'), False),
        )

    def with_overrides(
        self,
        country: str | None = None,
        language: str | None = None,
        catalog_batch_size: int | None = None,
        supabase_batch_size: int | None = None,
        detail_batch_limit: int | None = None,
        request_delay_seconds: float | None = None,
        max_retries: int | None = None,
        retry_base_seconds: float | None = None,
        retry_max_seconds: float | None = None,
        stale_after_days: int | None = None,
        failed_retry_after_hours: int | None = None,
        snapshot_enabled: bool | None = None,
        page_signal_enrichment_enabled: bool | None = None,
        dry_run: bool | None = None,
    ) -> 'SteamSyncConfig':
        return SteamSyncConfig(
            country=country or self.country,
            language=language or self.language,
            catalog_batch_size=catalog_batch_size if catalog_batch_size is not None else self.catalog_batch_size,
            supabase_batch_size=supabase_batch_size if supabase_batch_size is not None else self.supabase_batch_size,
            detail_batch_limit=detail_batch_limit if detail_batch_limit is not None else self.detail_batch_limit,
            request_delay_seconds=request_delay_seconds if request_delay_seconds is not None else self.request_delay_seconds,
            max_retries=max_retries if max_retries is not None else self.max_retries,
            retry_base_seconds=retry_base_seconds if retry_base_seconds is not None else self.retry_base_seconds,
            retry_max_seconds=retry_max_seconds if retry_max_seconds is not None else self.retry_max_seconds,
            stale_after_days=stale_after_days if stale_after_days is not None else self.stale_after_days,
            failed_retry_after_hours=failed_retry_after_hours if failed_retry_after_hours is not None else self.failed_retry_after_hours,
            snapshot_enabled=snapshot_enabled if snapshot_enabled is not None else self.snapshot_enabled,
            page_signal_enrichment_enabled=page_signal_enrichment_enabled if page_signal_enrichment_enabled is not None else self.page_signal_enrichment_enabled,
            dry_run=dry_run if dry_run is not None else self.dry_run,
        )
