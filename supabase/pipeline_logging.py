from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


STEP_LOG_FILES = {
    '01_extract_seed_page_signals': '01_extract_seed_page_signals.log',
    '02_discover_candidates': '02_discover_candidates.log',
    '03_get_app_details': '03_get_app_details.log',
    '04_filter_candidates': '04_filter_candidates.log',
    '05_score_more_like_this': '05_score_more_like_this.log',
    '06_shortlist_candidates': '06_shortlist_candidates.log',
    '07_llm_classify_comps': '07_llm_classify_comps.log',
    '08_generate_comp_report': '08_generate_comp_report.log',
    '09_fetch_tier1_reviews': '09_fetch_tier1_reviews.log',
    '10_summarize_tier1_reviews': '10_summarize_tier1_reviews.log',
    '11_llm_rollup_review_insights': '11_llm_rollup_review_insights.log',
    '12_generate_review_insights_report': '12_generate_review_insights_report.log',
}

_LOGGER_CACHE: dict[str, logging.Logger] = {}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def log_root_dir() -> Path:
    override = os.getenv('STEAM_GTM_LOG_DIR')
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / 'logs' / 'manual_steps'


def logger_for(step_key: str) -> logging.Logger:
    if step_key in _LOGGER_CACHE:
        return _LOGGER_CACHE[step_key]

    log_dir = log_root_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / STEP_LOG_FILES.get(step_key, f'{step_key}.log')

    logger = logging.getLogger(f'steam_gtm.manual_steps.{step_key}')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    handler = logging.FileHandler(log_path, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)

    _LOGGER_CACHE[step_key] = logger
    return logger


def log_step(step_key: str, *, run_id: str | None = None, message: str, **details: Any) -> None:
    payload: dict[str, Any] = {
        'timestamp': utc_now_iso(),
        'step': step_key,
        'run_id': run_id,
        'message': message,
    }
    if details:
        payload['details'] = details
    logger_for(step_key).info(json.dumps(payload, ensure_ascii=False, default=str))


def log_step_event(step_key: str, phase: str, *, run_id: str | None = None, message: str | None = None, **details: Any) -> None:
    log_step(step_key, run_id=run_id, message=message or f'{phase.title()} {step_key}', phase=phase, **details)