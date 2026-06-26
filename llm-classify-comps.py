import argparse
import csv
import json
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


COMP_BUCKETS = [
    "Direct Comp",
    "Adjacent Comp",
    "Audience Comp",
    "Mechanic Comp",
    "Commercial Benchmark",
    "Aspirational / Market Context",
    "Low Fit / Noise",
]


OUTPUT_SCHEMA = {
    "name": "steam_comp_classification_result",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "seed_game": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "appid": {"type": "integer"},
                    "name": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["appid", "name", "summary"],
            },
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "appid": {"type": "integer"},
                        "name": {"type": "string"},
                        "bucket": {
                            "type": "string",
                            "enum": COMP_BUCKETS,
                        },
                        "direct_fit_score": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100,
                        },
                        "audience_fit_score": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100,
                        },
                        "mechanic_fit_score": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100,
                        },
                        "commercial_benchmark_score": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100,
                        },
                        "confidence": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100,
                        },
                        "reason": {
                            "type": "string",
                        },
                        "use_for": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "do_not_use_for": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "appid",
                        "name",
                        "bucket",
                        "direct_fit_score",
                        "audience_fit_score",
                        "mechanic_fit_score",
                        "commercial_benchmark_score",
                        "confidence",
                        "reason",
                        "use_for",
                        "do_not_use_for",
                    ],
                },
            },
        },
        "required": ["seed_game", "candidates"],
    },
    "strict": True,
}


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_seed_signals(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def csv_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default

    try:
        return int(float(str(value).replace(",", "")))
    except ValueError:
        return default


def truncate(value: Any, max_chars: int) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())

    if len(text) > max_chars:
        return text[:max_chars] + "..."

    return text


def build_seed_profile(seed_signals: dict[str, Any]) -> dict[str, Any]:
    tags = seed_signals.get("tags") or []
    tag_names = [tag.get("name") for tag in tags if tag.get("name")]

    return {
        "appid": seed_signals.get("appid"),
        "name": seed_signals.get("basic_info", {}).get("page_title") or "Seed Game",
        "tags": tag_names[:20],
        "review_summary": seed_signals.get("basic_info", {}).get("review_summary"),
    }


def build_candidate_profile(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "appid": csv_int(row.get("appid")),
        "name": row.get("name") or "",
        "candidate_tags": [
            t.strip()
            for t in (row.get("candidate_tags") or "").split(",")
            if t.strip()
        ][:20],
        "overlapping_tags": [
            t.strip()
            for t in (row.get("overlapping_tags") or "").split(",")
            if t.strip()
        ],
        "code_fit_score": csv_int(row.get("fit_score")),
        "overlap_count": csv_int(row.get("overlap_count")),
        "price": row.get("price") or "",
        "release_date": row.get("release_date") or "",
        "recommendations_total": csv_int(row.get("recommendations_total")),
        "genres": [
            g.strip()
            for g in (row.get("genres") or "").split(",")
            if g.strip()
        ],
        "short_description": truncate(row.get("short_description"), 700),
        "steam_url": row.get("steam_url") or "",
    }


def chunk_list(items: list[Any], chunk_size: int) -> list[list[Any]]:
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]


def classify_batch(
    *,
    model: str,
    seed_profile: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    system_prompt = """
You are a senior go-to-market strategist for PC/Steam games.

You classify whether Steam "More Like This" candidates are useful comparable titles for a seed game.

Use the supplied structured data only. Do not browse the web.

Important:
- Do not treat raw tag overlap as final truth.
- Interpret core fantasy, player promise, gameplay loop, tone/vibe, and commercial usefulness.
- A game can be commercially important but still a weak direct comp.
- A game can be a strong direct comp but low commercial signal if it is free, unreleased, or has few recommendations.
- Keep reasons concise and practical.

Bucket definitions:
- Direct Comp: close audience, core fantasy, tone, and gameplay loop. Useful for positioning, pricing, Steam page, and reviews.
- Adjacent Comp: related audience and some similar systems, but not the same core fantasy.
- Audience Comp: likely shares player audience, but gameplay/core promise differs.
- Mechanic Comp: useful for studying specific mechanics/systems, but not overall positioning.
- Commercial Benchmark: useful for price/scope/review-volume comparison, even if not creatively similar.
- Aspirational / Market Context: large or breakout title that informs the market, but should not be treated as a normal direct benchmark.
- Low Fit / Noise: weak relevance despite Steam recommendation or tag overlap.
""".strip()

    user_payload = {
        "task": "Classify Steam candidate comparable games for GTM research.",
        "seed_game": seed_profile,
        "candidates": candidates,
        "scoring_guidance": {
            "direct_fit_score": "0-100. How close this is as a direct comparable game for positioning.",
            "audience_fit_score": "0-100. How likely the same audience would care.",
            "mechanic_fit_score": "0-100. How useful it is for studying mechanics/systems.",
            "commercial_benchmark_score": "0-100. How useful it is for pricing/scope/review benchmark.",
            "confidence": "0-100. Confidence from provided data.",
        },
    }

    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False),
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": OUTPUT_SCHEMA["name"],
                "schema": OUTPUT_SCHEMA["schema"],
                "strict": True,
            }
        },
    )

    return json.loads(response.output_text)


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def write_csv(path: Path, original_rows: list[dict[str, Any]], classified: list[dict[str, Any]]) -> None:
    original_by_appid = {
        csv_int(row.get("appid")): row
        for row in original_rows
    }

    fieldnames = [
        "appid",
        "name",
        "bucket",
        "direct_fit_score",
        "audience_fit_score",
        "mechanic_fit_score",
        "commercial_benchmark_score",
        "confidence",
        "reason",
        "use_for",
        "do_not_use_for",
        "code_fit_score",
        "overlap_count",
        "overlapping_tags",
        "candidate_tags",
        "price",
        "release_date",
        "recommendations_total",
        "genres",
        "short_description",
        "steam_url",
    ]

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for item in classified:
            appid = csv_int(item.get("appid"))
            original = original_by_appid.get(appid, {})

            writer.writerow(
                {
                    "appid": appid,
                    "name": item.get("name"),
                    "bucket": item.get("bucket"),
                    "direct_fit_score": item.get("direct_fit_score"),
                    "audience_fit_score": item.get("audience_fit_score"),
                    "mechanic_fit_score": item.get("mechanic_fit_score"),
                    "commercial_benchmark_score": item.get("commercial_benchmark_score"),
                    "confidence": item.get("confidence"),
                    "reason": item.get("reason"),
                    "use_for": "; ".join(item.get("use_for") or []),
                    "do_not_use_for": "; ".join(item.get("do_not_use_for") or []),
                    "code_fit_score": original.get("fit_score"),
                    "overlap_count": original.get("overlap_count"),
                    "overlapping_tags": original.get("overlapping_tags"),
                    "candidate_tags": original.get("candidate_tags"),
                    "price": original.get("price"),
                    "release_date": original.get("release_date"),
                    "recommendations_total": original.get("recommendations_total"),
                    "genres": original.get("genres"),
                    "short_description": original.get("short_description"),
                    "steam_url": original.get("steam_url"),
                }
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-signals", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--output-json", default="llm_comp_classifications.json")
    parser.add_argument("--output-csv", default="llm_comp_classifications.csv")
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=1.0)
    args = parser.parse_args()

    seed_signals = load_seed_signals(Path(args.seed_signals))
    seed_profile = build_seed_profile(seed_signals)

    rows = read_csv(Path(args.candidates))

    if args.limit:
        rows = rows[: args.limit]

    candidate_profiles = [build_candidate_profile(row) for row in rows]

    all_classified = []

    print(f"Seed: {seed_profile['name']} / {seed_profile['appid']}")
    print(f"Candidates: {len(candidate_profiles)}")
    print(f"Model: {args.model}")

    for batch_number, batch in enumerate(chunk_list(candidate_profiles, args.batch_size), start=1):
        print(f"Classifying batch {batch_number} with {len(batch)} candidates...")

        result = classify_batch(
            model=args.model,
            seed_profile=seed_profile,
            candidates=batch,
        )

        all_classified.extend(result.get("candidates") or [])

        time.sleep(args.sleep)

    final_result = {
        "seed_game": seed_profile,
        "candidates": all_classified,
    }

    write_json(Path(args.output_json), final_result)
    write_csv(Path(args.output_csv), rows, all_classified)

    print(f"Saved JSON: {args.output_json}")
    print(f"Saved CSV: {args.output_csv}")

    print("\nTop results:")
    for item in all_classified[:10]:
        print(
            item.get("bucket"),
            "|",
            item.get("direct_fit_score"),
            "|",
            item.get("name"),
            "|",
            item.get("reason"),
        )


if __name__ == "__main__":
    main()