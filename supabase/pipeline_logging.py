from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
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


def logger_for(step_key: str) -> logging.Logger:
    if step_key in _LOGGER_CACHE:
        return _LOGGER_CACHE[step_key]

    logger = logging.getLogger(f'steam_gtm.manual_steps.{step_key}')
    logger.setLevel(logging.INFO)
    logger.propagate = True
    logger.handlers.clear()

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
