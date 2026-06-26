import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


REVIEW_URL_TEMPLATE = "https://store.steampowered.com/appreviews/{appid}"


def fetch_review_page(
    session: requests.Session,
    *,
    appid: int,
    cursor: str = "*",
    num_per_page: int = 100,
    language: str = "english",
    review_type: str = "all",
    purchase_type: str = "all",
    filter_type: str = "recent",
) -> dict[str, Any]:
    url = REVIEW_URL_TEMPLATE.format(appid=appid)

    params = {
        "json": 1,
        "filter": filter_type,
        "language": language,
        "review_type": review_type,
        "purchase_type": purchase_type,
        "num_per_page": num_per_page,
        "cursor": cursor,
    }

    response = session.get(url, params=params, timeout=45)

    if response.status_code == 429:
        raise RuntimeError("RATE_LIMITED")

    if not response.ok:
        raise RuntimeError(
            f"Review request failed.\n"
            f"Status: {response.status_code}\n"
            f"URL: {response.url}\n"
            f"Response: {response.text[:1000]}"
        )

    return response.json()


def normalize_review(appid: int, review: dict[str, Any]) -> dict[str, Any]:
    author = review.get("author") or {}

    return {
        "appid": appid,
        "recommendationid": review.get("recommendationid"),
        "language": review.get("language"),
        "review": review.get("review"),
        "voted_up": review.get("voted_up"),
        "votes_up": review.get("votes_up"),
        "votes_funny": review.get("votes_funny"),
        "weighted_vote_score": review.get("weighted_vote_score"),
        "comment_count": review.get("comment_count"),
        "steam_purchase": review.get("steam_purchase"),
        "received_for_free": review.get("received_for_free"),
        "written_during_early_access": review.get("written_during_early_access"),
        "timestamp_created": review.get("timestamp_created"),
        "timestamp_updated": review.get("timestamp_updated"),
        "playtime_forever": author.get("playtime_forever"),
        "playtime_last_two_weeks": author.get("playtime_last_two_weeks"),
        "playtime_at_review": author.get("playtime_at_review"),
        "last_played": author.get("last_played"),
    }


def append_jsonl(output_file: Path, records: list[dict[str, Any]]) -> None:
    with output_file.open("a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_csv_from_jsonl(jsonl_file: Path, csv_file: Path) -> None:
    fieldnames = [
        "appid",
        "recommendationid",
        "language",
        "voted_up",
        "votes_up",
        "votes_funny",
        "weighted_vote_score",
        "comment_count",
        "steam_purchase",
        "received_for_free",
        "written_during_early_access",
        "timestamp_created",
        "timestamp_updated",
        "playtime_forever",
        "playtime_last_two_weeks",
        "playtime_at_review",
        "last_played",
        "review",
    ]

    rows = []

    if jsonl_file.exists():
        with jsonl_file.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))

    with csv_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        if rows:
            writer.writerows(rows)

    if rows:
        print(f"Saved {len(rows)} rows to CSV.")
    else:
        print(f"No review rows found. Wrote empty CSV with headers: {csv_file}")

def fetch_reviews_for_app(
    *,
    appid: int,
    output_file: Path,
    max_pages: int,
    sleep_seconds: float,
    language: str,
    review_type: str,
    purchase_type: str,
    filter_type: str,
) -> None:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "steam-gtm-research-prototype/0.1"
        }
    )

    cursor = "*"
    total_saved = 0

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.touch(exist_ok=True)

    for page in range(1, max_pages + 1):
        try:
            data = fetch_review_page(
                session,
                appid=appid,
                cursor=cursor,
                language=language,
                review_type=review_type,
                purchase_type=purchase_type,
                filter_type=filter_type,
            )

        except RuntimeError as e:
            if str(e) == "RATE_LIMITED":
                wait = 60
                print(f"Rate limited. Waiting {wait}s...")
                time.sleep(wait)
                continue

            raise

        success = data.get("success")
        reviews = data.get("reviews") or []
        query_summary = data.get("query_summary") or {}

        print(
            f"Page {page}: success={success}, "
            f"reviews={len(reviews)}, "
            f"total_reviews={query_summary.get('total_reviews')}, "
            f"total_positive={query_summary.get('total_positive')}, "
            f"total_negative={query_summary.get('total_negative')}"
        )

        if not reviews:
            print("No more reviews returned.")
            break

        normalized = [normalize_review(appid, review) for review in reviews]
        append_jsonl(output_file, normalized)

        total_saved += len(normalized)

        next_cursor = data.get("cursor")

        if not next_cursor or next_cursor == cursor:
            print("No new cursor returned; stopping.")
            break

        cursor = next_cursor

        time.sleep(sleep_seconds)

    print(f"Done. Saved {total_saved} reviews to {output_file}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--appid", type=int, required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--csv", default=None)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--language", default="english")
    parser.add_argument("--review-type", default="all", choices=["all", "positive", "negative"])
    parser.add_argument("--purchase-type", default="all", choices=["all", "steam", "non_steam_purchase"])
    parser.add_argument("--filter", default="recent", choices=["recent", "updated", "all"])
    args = parser.parse_args()

    output_file = Path(args.output or f"steam_reviews_{args.appid}.jsonl")
    csv_file = Path(args.csv or f"steam_reviews_{args.appid}.csv")

    fetch_reviews_for_app(
        appid=args.appid,
        output_file=output_file,
        max_pages=args.max_pages,
        sleep_seconds=args.sleep,
        language=args.language,
        review_type=args.review_type,
        purchase_type=args.purchase_type,
        filter_type=args.filter,
    )

    write_csv_from_jsonl(output_file, csv_file)
    print(f"Saved CSV to {csv_file}")


if __name__ == "__main__":
    main()