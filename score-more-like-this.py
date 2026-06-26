import argparse
import csv
import json
import subprocess
import time
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    return records


def tag_names(signals: dict[str, Any]) -> list[str]:
    tags = signals.get("tags") or []
    return [str(tag.get("name")).strip() for tag in tags if tag.get("name")]


def ensure_page_signals(
    appid: int,
    page_signals_dir: Path,
    sleep_seconds: float = 1.0,
) -> Path:
    signals_path = page_signals_dir / f"steam_page_signals_{appid}.json"

    if signals_path.exists():
        return signals_path

    print(f"Fetching page signals for {appid}...")

    subprocess.run(
        [
            "python",
            "get-page-signals.py",
            "--appid",
            str(appid),
            "--output",
            str(signals_path),
        ],
        check=True,
    )

    time.sleep(sleep_seconds)

    return signals_path

def get_recommendations_total(record: dict[str, Any]) -> int:
    recommendations = record.get("recommendations") or {}
    total = recommendations.get("total")

    if isinstance(total, int):
        return total

    return 0


def get_price(record: dict[str, Any]) -> str:
    if record.get("is_free") is True:
        return "Free"

    price = record.get("price_overview") or {}
    return price.get("final_formatted") or ""


def get_release_date(record: dict[str, Any]) -> str:
    release = record.get("release_date") or {}
    return release.get("date") or ""


def weighted_tag_score(seed_tags: list[str], candidate_tags: list[str]) -> tuple[int, list[str]]:
    """
    Simple weighted score:
    - earlier seed tags matter more
    - exact name overlap only
    """
    seed_normalized = [t.lower() for t in seed_tags]
    candidate_normalized = {t.lower() for t in candidate_tags}

    score = 0
    overlaps = []

    for index, tag in enumerate(seed_normalized):
        if tag in candidate_normalized:
            # Top tags are more important.
            weight = max(1, 20 - index)
            score += weight
            overlaps.append(seed_tags[index])

    return score, overlaps


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-signals", required=True)
    parser.add_argument("--details", required=True)
    parser.add_argument("--output", default="scored_more_like_this.csv")
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--page-signals-dir", default="data/page_signals")
    args = parser.parse_args()

    page_signals_dir = Path(args.page_signals_dir)
    page_signals_dir.mkdir(parents=True, exist_ok=True)

    seed_signals = load_json(Path(args.seed_signals))
    detail_records = load_jsonl(Path(args.details))

    seed_appid = seed_signals["appid"]
    seed_tag_list = tag_names(seed_signals)

    rows = []

    for record in detail_records:
        appid = record.get("appid")

        if not appid:
            continue

        try:
            candidate_signals_path = ensure_page_signals(appid, page_signals_dir, args.sleep)
            candidate_signals = load_json(candidate_signals_path)
            candidate_tag_list = tag_names(candidate_signals)
        except Exception as e:
            print(f"Failed to fetch/read page signals for {appid}: {e}")
            candidate_tag_list = []

        score, overlaps = weighted_tag_score(seed_tag_list, candidate_tag_list)

        rows.append(
            {
                "seed_appid": seed_appid,
                "appid": appid,
                "name": record.get("name"),
                "fit_score": score,
                "overlap_count": len(overlaps),
                "overlapping_tags": ", ".join(overlaps),
                "candidate_tags": ", ".join(candidate_tag_list[:20]),
                "price": get_price(record),
                "release_date": get_release_date(record),
                "recommendations_total": get_recommendations_total(record),
                "genres": ", ".join([g.get("description", "") for g in record.get("genres") or []]),
                "short_description": record.get("short_description") or "",
                "steam_url": f"https://store.steampowered.com/app/{appid}",
            }
        )

    rows.sort(
        key=lambda r: (
            -int(r["fit_score"]),
            -int(r["recommendations_total"]),
        )
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "seed_appid",
        "appid",
        "name",
        "fit_score",
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

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Saved scored comps to {output_path}")

    print("\nTop scored comps:")
    for row in rows[:15]:
        print(
            row["fit_score"],
            "|",
            row["name"],
            "|",
            row["price"],
            "|",
            row["recommendations_total"],
            "|",
            row["overlapping_tags"],
        )


if __name__ == "__main__":
    main()