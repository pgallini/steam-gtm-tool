import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup


SEARCH_URL = "https://store.steampowered.com/search/"


def clean_text(value: str | None) -> str:
    if not value:
        return ""

    return re.sub(r"\s+", " ", value).strip()


def parse_price(result) -> str:
    price_el = result.select_one(".discount_final_price")

    if price_el:
        return clean_text(price_el.get_text(" "))

    return ""


def parse_release_date(result) -> str:
    date_el = result.select_one(".search_released")

    if date_el:
        return clean_text(date_el.get_text(" "))

    return ""


def parse_reviews(result) -> str:
    review_el = result.select_one(".search_review_summary")

    if review_el:
        return clean_text(review_el.get("data-tooltip-html") or review_el.get_text(" "))

    return ""


def parse_platforms(result) -> list[str]:
    platforms = []

    for platform in ["win", "mac", "linux"]:
        if result.select_one(f".platform_img.{platform}"):
            platforms.append(platform)

    return platforms


def parse_search_result(result) -> dict[str, Any] | None:
    appid = result.get("data-ds-appid") or result.get("data-ds-bundleid")

    href = result.get("href")

    if not appid or not href:
        return None

    title_el = result.select_one(".title")
    title = clean_text(title_el.get_text(" ")) if title_el else ""

    return {
        "appid": int(appid) if str(appid).isdigit() else appid,
        "title": title,
        "url": href.split("?")[0],
        "release_date": parse_release_date(result),
        "price": parse_price(result),
        "review_summary": parse_reviews(result),
        "platforms": parse_platforms(result),
    }


def search_steam(
    *,
    query: str,
    max_results: int = 50,
    country: str = "us",
    language: str = "english",
) -> list[dict[str, Any]]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "steam-gtm-research-prototype/0.1",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )

    results = []
    page = 1

    while len(results) < max_results:
        params = {
            "term": query,
            "cc": country,
            "l": language,
            "page": page,
        }

        response = session.get(SEARCH_URL, params=params, timeout=45)

        if not response.ok:
            raise RuntimeError(
                f"Search request failed.\n"
                f"Status: {response.status_code}\n"
                f"URL: {response.url}\n"
                f"Response: {response.text[:1000]}"
            )

        soup = BeautifulSoup(response.text, "html.parser")
        rows = soup.select("a.search_result_row")

        if not rows:
            break

        for row in rows:
            parsed = parse_search_result(row)
            if parsed:
                results.append(parsed)

            if len(results) >= max_results:
                break

        page += 1

    return results


def write_csv(rows: list[dict[str, Any]], output_path: Path) -> None:
    fieldnames = [
        "appid",
        "title",
        "url",
        "release_date",
        "price",
        "review_summary",
        "platforms",
    ]

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            out = dict(row)
            out["platforms"] = ", ".join(row.get("platforms") or [])
            writer.writerow(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--max-results", type=int, default=50)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    results = search_steam(query=args.query, max_results=args.max_results)

    output_json = Path(args.output or f"steam_search_{args.query.replace(' ', '_')}.json")
    output_csv = output_json.with_suffix(".csv")

    output_json.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    write_csv(results, output_csv)

    print(f"Query: {args.query}")
    print(f"Results: {len(results)}")
    print(f"Saved JSON: {output_json}")
    print(f"Saved CSV: {output_csv}")

    print("\nTop results:")
    for result in results[:15]:
        print(
            result["appid"],
            result["title"],
            "|",
            result["price"],
            "|",
            result["review_summary"],
            "|",
            result["url"],
        )


if __name__ == "__main__":
    main()