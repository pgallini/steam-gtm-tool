import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    return records


def minutes_to_hours(minutes: int | None) -> float | None:
    if minutes is None:
        return None
    return round(minutes / 60, 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--samples", type=int, default=10)
    args = parser.parse_args()

    records = load_jsonl(Path(args.input))

    print("records:", len(records))
    print("voted_up:", Counter(r.get("voted_up") for r in records))
    print("languages:", Counter(r.get("language") for r in records).most_common(10))
    print("steam_purchase:", Counter(r.get("steam_purchase") for r in records))
    print("received_for_free:", Counter(r.get("received_for_free") for r in records))
    print("early_access:", Counter(r.get("written_during_early_access") for r in records))

    review_lengths = [len(r.get("review") or "") for r in records]
    playtimes = [
        r.get("playtime_at_review")
        for r in records
        if isinstance(r.get("playtime_at_review"), int)
    ]

    if review_lengths:
        print("review length min:", min(review_lengths))
        print("review length avg:", round(sum(review_lengths) / len(review_lengths), 1))
        print("review length max:", max(review_lengths))

    if playtimes:
        print("playtime_at_review min hours:", minutes_to_hours(min(playtimes)))
        print("playtime_at_review avg hours:", minutes_to_hours(round(sum(playtimes) / len(playtimes))))
        print("playtime_at_review max hours:", minutes_to_hours(max(playtimes)))

    print("\nSample reviews:")
    for r in records[: args.samples]:
        text = (r.get("review") or "").replace("\n", " ").strip()
        if len(text) > 500:
            text = text[:500] + "..."

        print("-" * 80)
        print("recommendationid:", r.get("recommendationid"))
        print("voted_up:", r.get("voted_up"))
        print("votes_up:", r.get("votes_up"))
        print("playtime_at_review_hours:", minutes_to_hours(r.get("playtime_at_review")))
        print(text)


if __name__ == "__main__":
    main()