from __future__ import annotations

import hashlib
import json
from typing import Any


TRACKED_SNAPSHOT_KEYS = [
    'name',
    'review_summary',
    'recent_review_summary',
    'review_count',
    'price_initial_cents',
    'price_final_cents',
    'discount_percent',
    'currency',
    'release_status',
    'release_date',
    'is_available',
    'tags',
    'tag_ids',
]


def _canonicalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _canonicalize_value(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        normalized = [_canonicalize_value(item) for item in value]
        try:
            return sorted(normalized)
        except TypeError:
            return normalized
    if value is None:
        return None
    return value


def make_content_hash(payload: dict[str, Any]) -> str:
    canonical = {key: _canonicalize_value(payload.get(key)) for key in TRACKED_SNAPSHOT_KEYS}
    encoded = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def should_create_snapshot(current_app: dict[str, Any], latest_snapshot: dict[str, Any] | None) -> bool:
    current_hash = current_app.get('content_hash') or make_content_hash(current_app)
    latest_hash = latest_snapshot.get('content_hash') if latest_snapshot else None
    return latest_hash != current_hash


def snapshot_payload(current_app: dict[str, Any]) -> dict[str, Any]:
    payload = {
        'appid': current_app['appid'],
        'fetch_source': 'steam_sync',
        'name': current_app.get('name'),
        'review_summary': current_app.get('review_summary'),
        'recent_review_summary': current_app.get('recent_review_summary'),
        'review_count': current_app.get('review_count'),
        'price_initial_cents': current_app.get('price_initial_cents'),
        'discount_percent': current_app.get('discount_percent'),
        'currency': current_app.get('currency'),
        'release_status': current_app.get('release_status'),
        'release_date': current_app.get('release_date'),
        'is_available': current_app.get('is_available'),
        'tags': current_app.get('tags') or [],
        'tag_ids': current_app.get('tag_ids') or [],
        'content_hash': current_app.get('content_hash') or make_content_hash(current_app),
        'raw_json': {
            'price': {
                'price_initial_cents': current_app.get('price_initial_cents'),
                'price_final_cents': current_app.get('price_final_cents'),
                'discount_percent': current_app.get('discount_percent'),
                'currency': current_app.get('currency'),
            },
            'review_summary': current_app.get('review_summary'),
            'recent_review_summary': current_app.get('recent_review_summary'),
            'review_count': current_app.get('review_count'),
            'release_status': current_app.get('release_status'),
            'release_date': current_app.get('release_date'),
            'tags': current_app.get('tags') or [],
            'tag_ids': current_app.get('tag_ids') or [],
        },
    }
    return payload
