from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .config import SteamSyncConfig
from .repository import SteamSyncRepository


def _normalize_appid(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def select_appids_for_enrichment(repo: SteamSyncRepository, config: SteamSyncConfig, limit: int | None = None, force: bool = False) -> list[int]:
    explicit_appids = repo.get_explicitly_referenced_appids()
    active_appids = repo.get_active_reference_appids()
    explicit_set = set(explicit_appids)
    active_set = set(active_appids)
 
    now = datetime.now(timezone.utc)
    stale_threshold = (now - timedelta(days=config.stale_after_days)).isoformat()
 
    if force:
        stale_games = repo.get_force_games_for_enrichment(limit or config.detail_batch_limit)
    else:
        stale_games = repo.get_stale_games_for_enrichment(stale_threshold, limit or config.detail_batch_limit)
    unclassified = repo.get_unclassified_catalog_apps(limit or config.detail_batch_limit)

    ordered_appids: list[int] = []

    for appid in explicit_appids:
        if appid not in ordered_appids:
            ordered_appids.append(appid)
    for appid in active_appids:
        if appid not in ordered_appids:
            ordered_appids.append(appid)
    for row in stale_games:
        appid = _normalize_appid(row.get('appid'))
        if appid and appid not in ordered_appids:
            ordered_appids.append(appid)
    for row in unclassified:
        appid = _normalize_appid(row.get('appid'))
        if appid and appid not in ordered_appids:
            ordered_appids.append(appid)

    if limit is not None:
        return ordered_appids[:limit]
    return ordered_appids
