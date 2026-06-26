import os
import time
import json
import requests
from dotenv import load_dotenv

load_dotenv()

STEAM_API_KEY = os.environ["STEAM_API_KEY"]

# BASE_URL = "https://partner.steam-api.com/IStoreService/GetAppList/v1/"
BASE_URL = "https://api.steampowered.com/IStoreService/GetAppList/v1/"

def get_steam_app_list(
    *,
    max_results: int = 1000,
    include_games: bool = True,
    include_dlc: bool = False,
    include_software: bool = False,
    include_videos: bool = False,
    include_hardware: bool = False,
    if_modified_since: int | None = None,
    last_appid: int | None = None,
) -> dict:
    input_json = {
        "include_games": include_games,
        "include_dlc": include_dlc,
        "include_software": include_software,
        "include_videos": include_videos,
        "include_hardware": include_hardware,
        "max_results": max_results,
    }

    if if_modified_since is not None:
        input_json["if_modified_since"] = if_modified_since

    if last_appid is not None:
        input_json["last_appid"] = last_appid

    response = requests.get(
        BASE_URL,
        params={
            "key": STEAM_API_KEY,
            "input_json": json.dumps(input_json),
        },
        timeout=30,
    )

    if not response.ok:
        safe_url = response.url.replace(STEAM_API_KEY, "[REDACTED]")
        raise RuntimeError(
            f"Steam API request failed.\n"
            f"Status: {response.status_code}\n"
            f"URL: {safe_url}\n"
            f"Response: {response.text[:1000]}"
        )

    return response.json()

def fetch_all_steam_games(max_pages: int | None = None) -> list[dict]:
    all_apps = []
    last_appid = None
    page = 0

    while True:
        page += 1

        data = get_steam_app_list(
            max_results=50000,
            include_games=True,
            include_dlc=False,
            include_software=False,
            include_videos=False,
            include_hardware=False,
            last_appid=last_appid,
        )

        apps = data.get("response", {}).get("apps", [])

        if not apps:
            break

        all_apps.extend(apps)

        last_appid = apps[-1]["appid"]

        print(f"Page {page}: fetched {len(apps)} apps; last_appid={last_appid}")

        if max_pages and page >= max_pages:
            break

        # Be polite; Steam does not publish a simple global public rate limit.
        time.sleep(1)

    return all_apps

if __name__ == "__main__":
    # For a smoke test, limit to 1 page first.
    # apps = fetch_all_steam_games(max_pages=1)
    apps = fetch_all_steam_games()

    print(f"\nFetched {len(apps)} apps")
    print("Sample:")
    for app in apps[:5]:
        print(app)

    with open("steam_apps_full.json", "w", encoding="utf-8") as f:
        json.dump(apps, f, indent=2)

    print("Saved to steam_apps_full.json")
