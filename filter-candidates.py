import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []

    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue

            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"Skipping bad JSON on line {line_number}: {e}")

    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def get_recommendations_total(record: dict[str, Any]) -> int:
    """
    Steam appdetails exposes public recommendation count as:
      record["recommendations"]["total"]

    In our reports we have been treating this as the closest available proxy
    for review volume / market signal.
    """
    recommendations = record.get("recommendations") or {}
    total = recommendations.get("total")

    try:
        return int(total)
    except (TypeError, ValueError):
        return 0


def get_release_status(record: dict[str, Any]) -> str:
    release_date = record.get("release_date") or {}

    if release_date.get("coming_soon") is True:
        return "upcoming"

    if record.get("success") is not True:
        return "fetch_failed"

    return "released"


def should_keep(
    record: dict[str, Any],
    *,
    min_reviews: int,
    include_upcoming: bool,
    include_free: bool,
    include_failed: bool,
) -> tuple[bool, str]:
    """
    Returns:
      keep: whether record passes filter
      reason: diagnostic reason for keep/drop
    """
    if record.get("success") is not True:
        return include_failed, "fetch_failed"

    if record.get("type") != "game":
        return False, "not_game"

    if record.get("is_free") is True and not include_free:
        return False, "free_excluded"

    release_status = get_release_status(record)
    recommendations_total = get_recommendations_total(record)

    if recommendations_total < min_reviews:
        return False, f"below_min_reviews_{min_reviews}"

    if release_status == "upcoming":
        return True, "kept_upcoming"

    return True, "kept"


def add_filter_metadata(
    record: dict[str, Any],
    *,
    min_reviews: int,
    keep: bool,
    reason: str,
) -> dict[str, Any]:
    """
    Preserve the source record but add filter metadata so we can audit later.
    """
    enriched = dict(record)

    enriched["filter_metadata"] = {
        "kept": keep,
        "reason": reason,
        "min_reviews": min_reviews,
        "recommendations_total": get_recommendations_total(record),
        "release_status": get_release_status(record),
    }

    return enriched


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Filter enriched Steam candidate details before scoring/LLM classification."
    )

    parser.add_argument("--input", required=True, help="Input candidate details JSONL")
    parser.add_argument("--output", required=True, help="Filtered output JSONL")

    parser.add_argument(
        "--dropped-output",
        default=None,
        help="Optional JSONL file for dropped candidates with filter metadata",
    )

    parser.add_argument(
        "--min-reviews",
        type=int,
        default=1000,
        help="Minimum recommendations/reviews required to keep a released game",
    )

    parser.add_argument(
        "--include-upcoming",
        action="store_true",
        help="Keep upcoming games even if they have fewer than min reviews",
    )

    parser.add_argument(
        "--include-free",
        action="store_true",
        help="Keep free games if they pass the other filters",
    )

    parser.add_argument(
        "--include-failed",
        action="store_true",
        help="Keep failed fetch records for debugging",
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    dropped_output_path = (
        Path(args.dropped_output)
        if args.dropped_output
        else output_path.with_name(output_path.stem + "_dropped.jsonl")
    )

    records = load_jsonl(input_path)

    kept = []
    dropped = []
    reason_counts = Counter()

    for record in records:
        keep, reason = should_keep(
            record,
            min_reviews=args.min_reviews,
            include_upcoming=args.include_upcoming,
            include_free=args.include_free,
            include_failed=args.include_failed,
        )

        record_with_metadata = add_filter_metadata(
            record,
            min_reviews=args.min_reviews,
            keep=keep,
            reason=reason,
        )

        reason_counts[reason] += 1

        if keep:
            kept.append(record_with_metadata)
        else:
            dropped.append(record_with_metadata)

    write_jsonl(output_path, kept)
    write_jsonl(dropped_output_path, dropped)

    print(f"Input records: {len(records)}")
    print(f"Kept records: {len(kept)}")
    print(f"Dropped records: {len(dropped)}")
    print(f"Saved filtered records to: {output_path}")
    print(f"Saved dropped records to: {dropped_output_path}")

    print("\nFilter reasons:")
    for reason, count in reason_counts.most_common():
        print(f"  {reason}: {count}")

    if kept:
        print("\nTop kept records:")
        kept_sorted = sorted(
            kept,
            key=lambda r: get_recommendations_total(r),
            reverse=True,
        )

        for record in kept_sorted[:10]:
            print(
                f"  {record.get('appid')} | "
                f"{record.get('name')} | "
                f"reviews={get_recommendations_total(record)} | "
                f"price={((record.get('price_overview') or {}).get('final_formatted') or 'N/A')}"
            )


if __name__ == "__main__":
    main()