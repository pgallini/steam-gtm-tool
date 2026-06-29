from __future__ import annotations

import argparse
import sys
from typing import Any

from supabase.research_run_service import addRunEvent, getResearchRun, prepareRunCandidates, updateResearchRunStatus


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Prepare run candidates from run_candidate_controls.')
    parser.add_argument('--run-id', required=True, help='Research run UUID')
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_id = args.run_id

    try:
        result = prepareRunCandidates(run_id)
        print(f"Prepared {result.get('count', 0)} run candidates for run {run_id}.")
    except Exception as exc:  # noqa: BLE001
        updateResearchRunStatus(run_id, 'failed', current_stage='discovery', failure_message=str(exc))
        addRunEvent(run_id, 'discovery', 'script_failed', str(exc))
        print(f'Error preparing run candidates: {exc}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
