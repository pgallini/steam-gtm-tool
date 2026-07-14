from __future__ import annotations

import logging
import os
from pathlib import Path


def log_root_dir() -> Path:
    override = os.getenv('STEAM_SYNC_LOG_DIR')
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent.parent / 'logs' / 'steam_sync'


def logger_for(name: str) -> logging.Logger:
    logger = logging.getLogger(f'steam_sync.{name}')
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False

    log_dir = log_root_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_dir / f'{name}.log', encoding='utf-8')
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger
