from __future__ import annotations

import logging


def logger_for(name: str) -> logging.Logger:
    logger = logging.getLogger(f'steam_sync.{name}')
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = True
    return logger
