from __future__ import annotations

import argparse
import json

from supabase.research_pipeline import runResearchPipeline


def main() -> None:
    parser = argparse.ArgumentParser(description='Run the Supabase-backed Steam GTM research pipeline.')
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    result = runResearchPipeline(args.run_id)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
