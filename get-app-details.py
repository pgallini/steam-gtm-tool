import argparse
from html import parser
import json
import time
from pathlib import Path
from typing import Any
import random
import requests


APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"


def load_apps(input_file: Path) -> list[dict[str, Any]]:
    with input_file.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_existing_appids(output_file: Path) -> set[int]:
    """
    Reads an existing JSONL output file and returns appids already fetched.
    This makes the script safe to stop/restart.
    """
    existing = set()

    if not output_file.exists():
        return existing

    with output_file.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue

            try:
                record = json.loads(line)
                existing.add(int(record["appid"]))
            except Exception:
                # Ignore malformed lines rather than failing the whole resume.
                continue

    return existing


def chunk_list(items: list[Any], chunk_size: int) -> list[list[Any]]:
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]

def fetch_app_details_batch(
    session: requests.Session,
    appids: list[int],
    *,
    country_code: str = "us",
    language: str = "english",
    max_retries: int = 5,
) -> dict[str, Any]:
    params = {
        "appids": ",".join(str(appid) for appid in appids),
        "cc": country_code,
        "l": language,
    }

    for attempt in range(1, max_retries + 1):
        response = session.get(APP_DETAILS_URL, params=params, timeout=45)

        if response.status_code == 429:
            wait_seconds = min(60, 5 * attempt)
            print(
                f"Rate limited on appids {appids}. "
                f"Attempt {attempt}/{max_retries}. Waiting {wait_seconds}s..."
            )
            time.sleep(wait_seconds)
            continue

        if not response.ok:
            raise RuntimeError(
                f"AppDetails request failed.\n"
                f"Status: {response.status_code}\n"
                f"URL: {response.url}\n"
                f"Response: {response.text[:1000]}"
            )

        return response.json()

    raise RuntimeError(
        f"AppDetails request failed after {max_retries} retries due to rate limiting. "
        f"Appids: {appids}"
    )

def normalize_app_detail_record(appid: int, response_for_app: dict[str, Any]) -> dict[str, Any]:
    success = response_for_app.get("success", False)
    data = response_for_app.get("data") if success else None

    if not data:
        return {
            "appid": appid,
            "success": False,
            "type": None,
            "name": None,
        }

    return {
        "appid": appid,
        "success": success,
        "type": data.get("type"),
        "name": data.get("name"),
        "steam_appid": data.get("steam_appid"),
        "required_age": data.get("required_age"),
        "is_free": data.get("is_free"),
        "controller_support": data.get("controller_support"),
        "release_date": data.get("release_date"),
        "developers": data.get("developers"),
        "publishers": data.get("publishers"),
        "price_overview": data.get("price_overview"),
        "platforms": data.get("platforms"),
        "categories": data.get("categories"),
        "genres": data.get("genres"),
        "recommendations": data.get("recommendations"),
        "metacritic": data.get("metacritic"),
        "website": data.get("website"),
        "supported_languages": data.get("supported_languages"),
        "header_image": data.get("header_image"),
        "capsule_image": data.get("capsule_image"),
        "capsule_imagev5": data.get("capsule_imagev5"),
        "short_description": data.get("short_description"),
        "about_the_game": data.get("about_the_game"),
        "screenshots": data.get("screenshots"),
        "movies": data.get("movies"),
        "pc_requirements": data.get("pc_requirements"),
        "mac_requirements": data.get("mac_requirements"),
        "linux_requirements": data.get("linux_requirements"),
    }

    return record


def append_jsonl(output_file: Path, records: list[dict[str, Any]]) -> None:
    with output_file.open("a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="steam_apps_full.json")
    parser.add_argument("--output", default="steam_app_details.jsonl")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--sleep", type=float, default=2.0)
    parser.add_argument("--country", default="us")
    parser.add_argument("--language", default="english")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--random-sample", action="store_true")
    args = parser.parse_args()

    input_file = Path(args.input)
    output_file = Path(args.output)

    apps = load_apps(input_file)
    existing_appids = load_existing_appids(output_file)

    appids = [int(app["appid"]) for app in apps if int(app["appid"]) not in existing_appids]

    if args.random_sample:
        random.shuffle(appids)

    if args.offset:
        appids = appids[args.offset:]

    if args.limit:
        appids = appids[: args.limit]

    print(f"Loaded {len(apps)} apps from {input_file}")
    print(f"Already fetched: {len(existing_appids)}")
    print(f"Remaining this run: {len(appids)}")
    print(f"Output file: {output_file}")

    if not appids:
        print("Nothing to fetch.")
        return

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "steam-gtm-research-prototype/0.1"
        }
    )

    total_saved = 0

    for batch_number, batch in enumerate(chunk_list(appids, args.batch_size), start=1):
        try:
            response_data = fetch_app_details_batch(
                session,
                batch,
                country_code=args.country,
                language=args.language,
            )

            records = []

            for appid in batch:
                app_response = response_data.get(str(appid), {"success": False})
                records.append(normalize_app_detail_record(appid, app_response))

            append_jsonl(output_file, records)
            total_saved += len(records)

            successes = sum(1 for r in records if r["success"])

            print(
                f"Batch {batch_number}: saved {len(records)} records "
                f"({successes} successful); total_saved={total_saved}"
            )

        except Exception as e:
            print(f"Batch {batch_number} failed for appids {batch[:5]}...")
            print(e)

        time.sleep(args.sleep)

    print("Done.")


if __name__ == "__main__":
    main()