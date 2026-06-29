from __future__ import annotations

import os
import sys
from typing import Any

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from supabase.client import SupabaseClient
from supabase.steam_utils import canonical_steam_url

client = SupabaseClient()


def seed() -> None:
    organization_id = 'd76f2b86-7d52-40f7-9e2f-9f59f9d88d6c'
    game_id = 'ea5a87ae-9339-4f4f-bace-7f7ad66abf1b'
    run_id = 'd9c6c6f6-3a74-46f5-9019-40c0d8af5b8e'

    org_payload = {
        'id': organization_id,
        'name': 'Steam GTM',
        'slug': 'steam-gtm',
    }
    client.insert('organizations', org_payload, upsert=True, on_conflict='slug')

    game_payload: dict[str, Any] = {
        'id': game_id,
        'organization_id': organization_id,
        'title': 'Creepy Horrors: Blood Cult 2',
        'slug': 'creepy-horrors-blood-cult-2',
        'steam_appid': 4405120,
        'steam_url': canonical_steam_url(4405120),
        'status': 'steam_page_live',
        'raw_intake_json': {},
    }
    client.insert('games', game_payload, upsert=True, on_conflict='id')

    run_payload = {
        'id': run_id,
        'game_id': game_id,
        'organization_id': organization_id,
        'name': 'Blood Cult 2 Candidate Control Run',
        'status': 'draft',
        'current_stage': 'intake',
        'run_config': {
            'anchor_tags': ['RPG', 'Strategy', 'Horror'],
            'supporting_tags': ['Base Building', 'Adventure'],
        },
    }
    client.insert('research_runs', run_payload, upsert=True, on_conflict='id')

    steam_controls = [
        {'appid': 2288470, 'name': 'Aethermancer'},
        {'appid': 1151340, 'name': 'Cult of the Lamb'},
        {'appid': 289070, 'name': 'Graveyard Keeper'},
    ]
    for steam_app in steam_controls:
        client.insert('steam_apps', steam_app, upsert=True, on_conflict='appid')

    controls = [
        {
            'id': '3887868c-42ea-4ca2-841f-7e723a41f19f',
            'run_id': run_id,
            'organization_id': organization_id,
            'control_type': 'require_include',
            'title': 'Aethermancer',
            'steam_appid': 2288470,
            'steam_url': canonical_steam_url(2288470),
            'reason': 'Required competitor for direct comparison',
            'user_notes': 'Must be included and evaluated thoroughly.',
        },
        {
            'id': '8f4d2bce-6c63-4acc-a6b1-5e6a8f7f3525',
            'run_id': run_id,
            'organization_id': organization_id,
            'control_type': 'must_consider',
            'title': 'Cult of the Lamb',
            'steam_appid': 1151340,
            'steam_url': canonical_steam_url(1151340),
            'reason': 'Strategic must-consider audience competitor',
            'user_notes': 'Important to understand darkly charming cult mechanics.',
        },
        {
            'id': 'c25f1b31-1ee2-4f78-b7db-e1f22c4b7c56',
            'run_id': run_id,
            'organization_id': organization_id,
            'control_type': 'benchmark_only',
            'title': 'Graveyard Keeper',
            'steam_appid': 289070,
            'steam_url': canonical_steam_url(289070),
            'reason': 'Commercial benchmark for graveyard and progression design',
            'user_notes': 'Include as a pricing/benchmark reference.',
        },
    ]

    for control in controls:
        client.insert('run_candidate_controls', control, upsert=True, on_conflict='id', returning='representation')

    print('Seed data inserted successfully.')


if __name__ == '__main__':
    seed()
