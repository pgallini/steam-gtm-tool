import argparse
import csv
from pathlib import Path
from typing import Any
import json

def load_anchor_tags_from_strategy(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(split_values(",".join(data.get("anchor_tags") or [])))

def as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).replace(",", "")))
    except ValueError:
        return default


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def split_values(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def has_anchor_overlap(row: dict[str, Any], anchor_tags: set[str]) -> bool:
    overlapping = set(split_values(row.get("overlapping_tags") or ""))
    candidate_tags = set(split_values(row.get("candidate_tags") or ""))

    return bool((overlapping | candidate_tags) & anchor_tags)


def add_reason(row: dict[str, Any], reason: str) -> None:
    existing = row.get("shortlist_reasons") or ""

    reasons = [r.strip() for r in existing.split(";") if r.strip()]

    if reason not in reasons:
        reasons.append(reason)

    row["shortlist_reasons"] = "; ".join(reasons)


def dedupe_by_appid(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    deduped = []

    for row in rows:
        appid = row.get("appid")

        if appid in seen:
            continue

        seen.add(appid)
        deduped.append(row)

    return deduped


def sort_by_fit(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda r: (
            -as_int(r.get("fit_score")),
            -as_int(r.get("overlap_count")),
            -as_int(r.get("recommendations_total")),
        ),
    )


def sort_by_reviews(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda r: (
            -as_int(r.get("recommendations_total")),
            -as_int(r.get("fit_score")),
            -as_int(r.get("overlap_count")),
        ),
    )


def sort_by_anchor_fit(rows: list[dict[str, Any]], anchor_tags: set[str]) -> list[dict[str, Any]]:
    def anchor_overlap_count(row: dict[str, Any]) -> int:
        overlapping = set(split_values(row.get("overlapping_tags") or ""))
        candidate_tags = set(split_values(row.get("candidate_tags") or ""))
        return len((overlapping | candidate_tags) & anchor_tags)

    return sorted(
        rows,
        key=lambda r: (
            -anchor_overlap_count(r),
            -as_int(r.get("fit_score")),
            -as_int(r.get("recommendations_total")),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a pre-LLM shortlist from scored Steam candidates."
    )

    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)

    parser.add_argument("--max-candidates", type=int, default=150)

    parser.add_argument("--top-fit", type=int, default=50)
    parser.add_argument("--top-reviewed", type=int, default=30)
    parser.add_argument("--top-anchor", type=int, default=50)

    parser.add_argument(
        "--anchor-tags",
        default="",
        help='Comma-separated key tags, e.g. "Creature Collector,Dungeon Crawler,Turn-Based Strategy"',
    )
    parser.add_argument(
        "--strategy",
        default=None,
        help="Discovery strategy JSON file. Uses anchor_tags from this file.",
)

    args = parser.parse_args()

    rows = read_csv(Path(args.input))

    if not rows:
        raise RuntimeError("No rows found in input CSV.")

    fieldnames = list(rows[0].keys())

    if "shortlist_reasons" not in fieldnames:
        fieldnames.append("shortlist_reasons")

    anchor_tags = set()

    if args.strategy:
        anchor_tags = load_anchor_tags_from_strategy(Path(args.strategy))
    elif args.anchor_tags:
        anchor_tags = set(split_values(args.anchor_tags))

    selected = []

    # Lane 1: best deterministic fit.
    for row in sort_by_fit(rows)[: args.top_fit]:
        row = dict(row)
        add_reason(row, "top_fit_score")
        selected.append(row)

    # Lane 2: strongest commercial/review signal.
    for row in sort_by_reviews(rows)[: args.top_reviewed]:
        row = dict(row)
        add_reason(row, "top_review_count")
        selected.append(row)

    # Lane 3: candidates with important anchor tags.
    if anchor_tags:
        anchor_rows = [r for r in rows if has_anchor_overlap(r, anchor_tags)]

        for row in sort_by_anchor_fit(anchor_rows, anchor_tags)[: args.top_anchor]:
            row = dict(row)
            add_reason(row, "anchor_tag_match")
            selected.append(row)

    selected = dedupe_by_appid(selected)

    selected = sorted(
        selected,
        key=lambda r: (
            -as_int(r.get("fit_score")),
            -as_int(r.get("overlap_count")),
            -as_int(r.get("recommendations_total")),
        ),
    )

    selected = selected[: args.max_candidates]

    write_csv(Path(args.output), selected, fieldnames)

    print(f"Input rows: {len(rows)}")
    print(f"Shortlisted rows: {len(selected)}")
    print(f"Saved shortlist: {args.output}")

    print("\nTop shortlisted candidates:")
    for row in selected[:20]:
        print(
            f"{row.get('appid')} | "
            f"{row.get('name')} | "
            f"fit={row.get('fit_score')} | "
            f"overlap={row.get('overlap_count')} | "
            f"reviews={row.get('recommendations_total')} | "
            f"reasons={row.get('shortlist_reasons')}"
        )


if __name__ == "__main__":
    main()