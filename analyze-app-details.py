import argparse
import csv
import json
import re
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


def clean_text(value: Any) -> str:
    if not value:
        return ""

    text = str(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def get_release_status(record: dict[str, Any]) -> str:
    release = record.get("release_date") or {}

    if release.get("coming_soon") is True:
        return "coming_soon"

    date_text = release.get("date")

    if date_text:
        return "released"

    return "unknown"


def get_price_final_cents(record: dict[str, Any]) -> int | None:
    price = record.get("price_overview") or {}

    final = price.get("final")

    if isinstance(final, int):
        return final

    return None


def get_price_display(record: dict[str, Any]) -> str:
    price = record.get("price_overview") or {}

    if record.get("is_free") is True:
        return "Free"

    return price.get("final_formatted") or ""


def get_recommendation_count(record: dict[str, Any]) -> int:
    recommendations = record.get("recommendations") or {}
    total = recommendations.get("total")

    if isinstance(total, int):
        return total

    return 0


def get_descriptions(record: dict[str, Any]) -> tuple[str, str]:
    return (
        clean_text(record.get("short_description")),
        clean_text(record.get("about_the_game")),
    )


def get_names(items: Any) -> list[str]:
    if not items:
        return []

    names = []

    for item in items:
        if isinstance(item, dict):
            desc = item.get("description")
            if desc:
                names.append(desc)
        elif isinstance(item, str):
            names.append(item)

    return names


def classify_record(record: dict[str, Any]) -> str:
    if not record.get("success"):
        return "fetch_failed"

    if record.get("type") != "game":
        return "not_game"

    release_status = get_release_status(record)
    recs = get_recommendation_count(record)
    has_price = record.get("price_overview") is not None
    is_free = record.get("is_free") is True
    short_description, about_the_game = get_descriptions(record)

    if release_status == "coming_soon":
        return "upcoming"

    if not has_price and not is_free:
        return "missing_price"

    if recs >= 100:
        return "commercially_relevant"

    if recs >= 25:
        return "some_signal"

    if short_description or about_the_game:
        return "low_signal"

    return "possible_junk"


def write_candidates_csv(records: list[dict[str, Any]], output_path: Path) -> None:
    rows = []

    for record in records:
        classification = classify_record(record)
        recs = get_recommendation_count(record)
        release = record.get("release_date") or {}
        short_description, _ = get_descriptions(record)

        rows.append(
            {
                "appid": record.get("appid"),
                "name": record.get("name"),
                "classification": classification,
                "release_status": get_release_status(record),
                "release_date": release.get("date", ""),
                "is_free": record.get("is_free"),
                "price": get_price_display(record),
                "price_final_cents": get_price_final_cents(record),
                "recommendations_total": recs,
                "metacritic_score": (record.get("metacritic") or {}).get("score", ""),
                "genres": ", ".join(get_names(record.get("genres"))),
                "categories": ", ".join(get_names(record.get("categories"))),
                "developers": ", ".join(record.get("developers") or []),
                "publishers": ", ".join(record.get("publishers") or []),
                "short_description": short_description,
                "steam_url": f"https://store.steampowered.com/app/{record.get('appid')}",
            }
        )

    rows.sort(
        key=lambda r: (
            r["classification"] != "commercially_relevant",
            -(r["recommendations_total"] or 0),
            r["name"] or "",
        )
    )

    fieldnames = [
        "appid",
        "name",
        "classification",
        "release_status",
        "release_date",
        "is_free",
        "price",
        "price_final_cents",
        "recommendations_total",
        "metacritic_score",
        "genres",
        "categories",
        "developers",
        "publishers",
        "short_description",
        "steam_url",
    ]

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def print_summary(records: list[dict[str, Any]]) -> None:
    print("records:", len(records))
    print("success:", sum(1 for r in records if r.get("success")))
    print("types:", Counter(r.get("type") for r in records))
    print("classifications:", Counter(classify_record(r) for r in records))
    print("has_price:", sum(1 for r in records if r.get("price_overview")))
    print("is_free:", sum(1 for r in records if r.get("is_free") is True))
    print("has_recommendations:", sum(1 for r in records if r.get("recommendations")))
    print("has_metacritic:", sum(1 for r in records if r.get("metacritic")))

    rec_counts = [get_recommendation_count(r) for r in records]
    rec_counts_nonzero = [x for x in rec_counts if x > 0]

    if rec_counts_nonzero:
        print("recommendations min_nonzero:", min(rec_counts_nonzero))
        print("recommendations max:", max(rec_counts_nonzero))
        print("recommendations >= 25:", sum(1 for x in rec_counts if x >= 25))
        print("recommendations >= 100:", sum(1 for x in rec_counts if x >= 100))
        print("recommendations >= 500:", sum(1 for x in rec_counts if x >= 500))
        print("recommendations >= 1000:", sum(1 for x in rec_counts if x >= 1000))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="steam_app_candidates.csv")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    records = load_jsonl(input_path)
    print_summary(records)
    write_candidates_csv(records, output_path)

    print(f"Saved CSV: {output_path}")


if __name__ == "__main__":
    main()