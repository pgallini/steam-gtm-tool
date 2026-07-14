from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from typing import Any

from .config import SteamSyncConfig
from .repository import SteamSyncRepository
from .service import SteamSyncService


def build_config(args: argparse.Namespace) -> SteamSyncConfig:
    config = SteamSyncConfig()
    if args.country:
        config = replace(config, country=args.country)
    if args.language:
        config = replace(config, language=args.language)
    if args.request_delay is not None:
        config = replace(config, request_delay_seconds=args.request_delay)
    if args.dry_run:
        config = replace(config, dry_run=True)
    if args.include_page_signals:
        config = replace(config, page_signal_enrichment_enabled=True)
    return config


def summarize(result: Any) -> str:
    try:
        return json.dumps(result, indent=2, default=str)
    except TypeError:
        return str(result)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Steam sync command line interface')
    parser.add_argument('mode', choices=['catalog', 'details', 'weekly', 'app', 'retry-failures', 'status'], help='Sync mode to execute')
    parser.add_argument('--country', help='Country code to use for Steam requests')
    parser.add_argument('--language', help='Language code to use for Steam requests')
    parser.add_argument('--limit', type=int, help='Maximum number of detail records to process')
    parser.add_argument('--request-delay', type=float, help='Delay in seconds between detail requests')
    parser.add_argument('--dry-run', action='store_true', help='Run without modifying Supabase')
    parser.add_argument('--force', action='store_true', help='Force detail refresh when applicable')
    parser.add_argument('--include-page-signals', action='store_true', help='Include page signal enrichment for eligible records')
    parser.add_argument('--resume-run-id', help='Resume an existing sync run by ID')
    parser.add_argument('--appid', type=int, help='AppID to refresh when using the app mode')
    parser.add_argument('--run-id', help='Sync run ID to show status for when using status mode')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format='%(message)s')

    config = build_config(args)
    repo = SteamSyncRepository()
    service = SteamSyncService(config=config, repository=repo)

    try:
        if args.mode == 'catalog':
            result = service.sync_catalog()
        elif args.mode == 'details':
            result = service.sync_details(limit=args.limit, include_page_signals=args.include_page_signals, force=args.force, resume_run_id=args.resume_run_id)
        elif args.mode == 'weekly':
            result = service.sync_weekly(limit=args.limit, include_page_signals=args.include_page_signals)
        elif args.mode == 'app':
            if args.appid is None:
                raise ValueError('The --appid option is required for app mode')
            result = service.sync_app(appid=args.appid, include_page_signals=args.include_page_signals, force=args.force)
        elif args.mode == 'retry-failures':
            result = service.retry_failures(limit=args.limit)
        elif args.mode == 'status':
            result = service.get_status(run_id=args.run_id)
        else:
            raise ValueError(f'Unsupported mode: {args.mode}')
    except Exception as exc:
        logging.error('Steam sync failed: %s', exc)
        return 1

    logging.info('Sync result: %s', summarize(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
