from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent
MAJOR_PUBLISHERS_PATH = ROOT_DIR / 'major_publishers.json'
INDUSTRY_CLASSICS_PATH = ROOT_DIR / 'industry_classics.json'


CONFIDENCE_ORDER = {'high': 3, 'medium': 2, 'low': 1, None: 0}


def _load_json_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open('r', encoding='utf-8') as handle:
        data = json.load(handle)
    return data if isinstance(data, list) else []


def normalize_company_name(name: str | None) -> str:
    text = re.sub(r'[^\w\s]', ' ', str(name or '').lower().strip())
    text = re.sub(r'\b(inc|llc|ltd|corp|corporation|co|company|gmbh|plc|sarl|sa|bv|ag|pty|limited)\b', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def normalize_title(title: str | None) -> str:
    text = re.sub(r'[^\w\s]', ' ', str(title or '').lower().strip())
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def split_company_names(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        items = re.split(r'\s*(?:,|;|&|/|\band\b|\+)\s*', values, flags=re.IGNORECASE)
    elif isinstance(values, (list, tuple, set)):
        items = [str(item) for item in values]
    else:
        items = [str(values)]
    cleaned: list[str] = []
    for item in items:
        text = str(item or '').strip()
        if text:
            cleaned.append(text)
    return cleaned


@lru_cache(maxsize=4)
def _load_json_file_with_mtime(path_str: str, mtime: float) -> list[dict[str, Any]]:
    return _load_json_file(Path(path_str))


def load_major_publishers() -> list[dict[str, Any]]:
    mtime = MAJOR_PUBLISHERS_PATH.stat().st_mtime if MAJOR_PUBLISHERS_PATH.exists() else -1.0
    return _load_json_file_with_mtime(str(MAJOR_PUBLISHERS_PATH), mtime)


def load_industry_classics() -> list[dict[str, Any]]:
    mtime = INDUSTRY_CLASSICS_PATH.stat().st_mtime if INDUSTRY_CLASSICS_PATH.exists() else -1.0
    return _load_json_file_with_mtime(str(INDUSTRY_CLASSICS_PATH), mtime)


def detect_major_publisher(developer_names: Any, publisher_names: Any) -> dict[str, Any]:
    candidates = split_company_names(developer_names) + split_company_names(publisher_names)
    normalized_candidates = {normalize_company_name(name): name for name in candidates if normalize_company_name(name)}
    for entry in load_major_publishers():
        canonical_name = str(entry.get('canonical_name') or '').strip()
        parent_company = str(entry.get('parent_company') or canonical_name or '').strip() or None
        confidence = entry.get('confidence') or ('medium' if canonical_name in {'Annapurna Interactive', 'Devolver Digital', 'Team17', '505 Games', 'Focus Entertainment', 'Paradox Interactive', 'THQ Nordic / Embracer'} else 'high')
        aliases = [canonical_name, *split_company_names(entry.get('aliases'))]
        for alias in aliases:
            normalized_alias = normalize_company_name(alias)
            if not normalized_alias:
                continue
            if normalized_alias in normalized_candidates:
                matched_name = normalized_candidates[normalized_alias]
                return {
                    'is_major_publisher': True,
                    'major_publisher_match': canonical_name or alias,
                    'parent_company': parent_company,
                    'aaa_confidence': confidence if confidence in {'high', 'medium', 'low'} else 'high',
                    'aaa_reason': f'Publisher matched known major publisher list: {canonical_name or alias}.',
                    'matched_input': matched_name,
                }
    return {
        'is_major_publisher': False,
        'major_publisher_match': None,
        'parent_company': None,
        'aaa_confidence': None,
        'aaa_reason': None,
        'matched_input': None,
    }


def detect_industry_classic(app_id: int | None, title: str | None, review_count: int | None) -> dict[str, Any]:
    normalized_title = normalize_title(title)
    for entry in load_industry_classics():
        entry_app_id = entry.get('app_id')
        entry_title = str(entry.get('name') or '').strip()
        reason = str(entry.get('reason') or 'Matched industry classics list.')
        if app_id is not None and entry_app_id is not None and int(entry_app_id) == int(app_id):
            confidence = entry.get('confidence') or 'high'
            return {
                'is_industry_classic': True,
                'classic_confidence': confidence,
                'classic_reason': reason,
                'classic_match': entry_title or None,
                'classic_match_type': 'app_id',
                'classic_heuristic_only': False,
            }
        if normalized_title and normalize_title(entry_title) == normalized_title:
            confidence = entry.get('confidence') or 'high'
            return {
                'is_industry_classic': True,
                'classic_confidence': confidence,
                'classic_reason': reason,
                'classic_match': entry_title or None,
                'classic_match_type': 'title',
                'classic_heuristic_only': False,
            }

    heuristic_confidence = None
    if review_count is not None:
        if review_count >= 100000:
            heuristic_confidence = 'high'
        elif review_count >= 50000:
            heuristic_confidence = 'medium'
        elif review_count >= 25000:
            heuristic_confidence = 'low'

    if heuristic_confidence:
        return {
            'is_industry_classic': True,
            'classic_confidence': heuristic_confidence,
            'classic_reason': 'Very high Steam review count suggests unusually broad market awareness.',
            'classic_match': None,
            'classic_match_type': 'heuristic',
            'classic_heuristic_only': True,
        }

    return {
        'is_industry_classic': False,
        'classic_confidence': None,
        'classic_reason': None,
        'classic_match': None,
        'classic_match_type': None,
        'classic_heuristic_only': False,
    }


def detect_low_signal(review_count: int | None, tags: Any, short_description: str | None, review_summary: str | None) -> dict[str, Any]:
    tag_list = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
    has_useful_description = bool(str(short_description or '').strip() or str(review_summary or '').strip())
    if (review_count or 0) < 100 and not tag_list and not has_useful_description:
        return {
            'is_low_signal': True,
            'low_signal_reason': 'Insufficient Steam signal for reliable market comparison.',
        }
    return {
        'is_low_signal': False,
        'low_signal_reason': None,
    }


def resolve_comp_exclusion_category(*, major_result: dict[str, Any], classic_result: dict[str, Any], low_signal_result: dict[str, Any]) -> str:
    if classic_result.get('is_industry_classic') and not classic_result.get('classic_heuristic_only'):
        if classic_result.get('classic_confidence') == 'low':
            return 'uncertain'
        return 'industry_classic'
    if major_result.get('is_major_publisher'):
        if major_result.get('aaa_confidence') == 'low':
            return 'uncertain'
        return 'aaa_or_major_publisher'
    if low_signal_result.get('is_low_signal'):
        return 'low_signal'
    if classic_result.get('is_industry_classic') or major_result.get('is_major_publisher'):
        return 'uncertain'
    return 'indie_relevant'


def classify_comp_metadata(candidate: dict[str, Any], app: dict[str, Any] | None) -> dict[str, Any]:
    review_count = app.get('review_count') if isinstance(app, dict) else candidate.get('review_count')
    review_count_int = int(review_count) if str(review_count or '').isdigit() else None
    title = candidate.get('title') or (app or {}).get('name')
    developer_names = candidate.get('developer') or (app or {}).get('developer')
    publisher_names = candidate.get('publisher') or (app or {}).get('publisher')
    tags = (app or {}).get('tags') or []
    major_result = detect_major_publisher(developer_names, publisher_names)
    classic_result = detect_industry_classic(candidate.get('steam_appid') or (app or {}).get('appid'), title, review_count_int)
    low_signal_result = detect_low_signal(review_count_int, tags, (app or {}).get('short_description'), (app or {}).get('review_summary'))
    comp_exclusion_category = resolve_comp_exclusion_category(
        major_result=major_result,
        classic_result=classic_result,
        low_signal_result=low_signal_result,
    )
    reason = major_result.get('aaa_reason') or classic_result.get('classic_reason') or low_signal_result.get('low_signal_reason')
    if comp_exclusion_category == 'indie_relevant':
        reason = 'No major publisher, industry classic, or low-signal exclusion detected.'
    elif comp_exclusion_category == 'uncertain':
        reason = reason or 'Classification needs human review.'

    return {
        'comp_exclusion_category': comp_exclusion_category,
        'is_major_publisher': major_result.get('is_major_publisher', False),
        'major_publisher_match': major_result.get('major_publisher_match'),
        'parent_company': major_result.get('parent_company'),
        'aaa_confidence': major_result.get('aaa_confidence'),
        'aaa_reason': major_result.get('aaa_reason'),
        'is_industry_classic': classic_result.get('is_industry_classic', False),
        'classic_confidence': classic_result.get('classic_confidence'),
        'classic_reason': classic_result.get('classic_reason'),
    }
