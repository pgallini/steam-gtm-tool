import argparse
import json
import re
import time
import html
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup


STORE_APP_URL = "https://store.steampowered.com/app/{appid}/"


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def extract_appid_from_url(url: str) -> int | None:
    match = re.search(r"/app/(\d+)", url)
    return int(match.group(1)) if match else None


def fetch_store_page(appid: int, *, country: str = "us", language: str = "english") -> str:
    url = STORE_APP_URL.format(appid=appid)

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "steam-gtm-research-prototype/0.1",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )

    # Helps with age-gated/mature pages.
    session.cookies.set("birthtime", "568022401", domain=".steampowered.com")
    session.cookies.set("lastagecheckage", "1-January-1988", domain=".steampowered.com")
    session.cookies.set("mature_content", "1", domain=".steampowered.com")

    response = session.get(
        url,
        params={"cc": country, "l": language},
        timeout=45,
    )

    if not response.ok:
        raise RuntimeError(
            f"Store page request failed.\n"
            f"Status: {response.status_code}\n"
            f"URL: {response.url}\n"
            f"Response: {response.text[:1000]}"
        )

    return response.text


def extract_tags(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """
    Fallback tag extraction from visible page elements.
    Rich tag extraction from scripts is preferred when available.
    """
    tags = []
    seen = set()

    for element in soup.select(".app_tag"):
        tag_name = clean_text(element.get_text(" "))

        if not tag_name:
            continue

        if tag_name in {"+", "Popular user-defined tags for this product:"}:
            continue

        tag_id = element.get("data-tagid")
        key = (tag_id, tag_name.lower())

        if key in seen:
            continue

        seen.add(key)

        tags.append(
            {
                "tagid": int(tag_id) if tag_id and tag_id.isdigit() else None,
                "name": tag_name,
                "count": None,
                "browseable": None,
            }
        )

    return tags


def extract_rich_tags_from_scripts(html_text: str) -> list[dict[str, Any]]:
    """
    Extract richer Steam tag data embedded in script text.

    Example:
    [{"tagid":916648,"name":"Creature Collector","count":180,"browseable":true}]
    """
    pattern = re.compile(
        r'\[\{"tagid":\d+,"name":.*?,"browseable":(?:true|false)\}\]',
        re.DOTALL,
    )

    tag_sets = []

    for match in pattern.findall(html_text):
        try:
            parsed = json.loads(match)
        except Exception:
            continue

        if isinstance(parsed, list) and parsed and "tagid" in parsed[0] and "name" in parsed[0]:
            tag_sets.append(parsed)

    if not tag_sets:
        return []

    # The app's visible tag set is usually the longest tag array.
    tag_sets.sort(key=len, reverse=True)
    return tag_sets[0]


def extract_basic_page_info(soup: BeautifulSoup) -> dict[str, Any]:
    title_el = soup.select_one(".apphub_AppName")
    title = clean_text(title_el.get_text(" ")) if title_el else None

    review_summary = None
    review_el = soup.select_one(".user_reviews_summary_row .game_review_summary")
    if review_el:
        review_summary = clean_text(review_el.get_text(" "))

    recent_review_summary = None
    recent_el = soup.select_one("#review_summary_recent .game_review_summary")
    if recent_el:
        recent_review_summary = clean_text(recent_el.get_text(" "))

    return {
        "page_title": title,
        "review_summary": review_summary,
        "recent_review_summary": recent_review_summary,
    }


def extract_more_like_this_appids(soup: BeautifulSoup) -> list[int]:
    """
    Extract Steam 'More Like This' carousel app IDs.
    """
    appids = []

    for element in soup.select('[data-featuretarget="storeitems-carousel"]'):
        raw_props = element.get("data-props")

        if not raw_props:
            continue

        try:
            decoded = html.unescape(raw_props)
            props = json.loads(decoded)
        except Exception:
            continue

        if props.get("title", "").lower() != "more like this":
            continue

        for appid in props.get("appIDs", []):
            if isinstance(appid, int):
                appids.append(appid)

    return appids


def extract_linked_apps(soup: BeautifulSoup, source_appid: int) -> list[dict[str, Any]]:
    """
    Extract linked Steam apps from the page.

    These are not guaranteed to be comps. They may include demos, DLC,
    soundtracks, news links, franchise links, etc.
    """
    apps_by_id: dict[int, dict[str, Any]] = {}

    for link in soup.find_all("a", href=True):
        href = link["href"]
        linked_appid = extract_appid_from_url(href)

        if not linked_appid or linked_appid == source_appid:
            continue

        name = ""

        name_el = link.select_one(".tab_item_name")
        if name_el:
            name = clean_text(name_el.get_text(" "))

        if not name:
            img = link.find("img")
            if img:
                name = clean_text(img.get("alt"))

        if not name:
            name = clean_text(link.get_text(" "))

        apps_by_id.setdefault(
            linked_appid,
            {
                "appid": linked_appid,
                "name": name or None,
                "url": f"https://store.steampowered.com/app/{linked_appid}",
                "source_href": href,
            },
        )

    return list(apps_by_id.values())


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--appid", type=int, required=True)

    # Required output paths give caller full control.
    parser.add_argument("--output", required=True, help="Output JSON signals file path")

    # Optional HTML output path.
    parser.add_argument("--save-html", action="store_true")
    parser.add_argument("--html-output", default=None, help="Output HTML file path")

    parser.add_argument("--country", default="us")
    parser.add_argument("--language", default="english")

    args = parser.parse_args()

    html_text = fetch_store_page(args.appid, country=args.country, language=args.language)

    if args.save_html:
        html_path = Path(args.html_output or f"steam_page_{args.appid}.html")
        ensure_parent_dir(html_path)
        html_path.write_text(html_text, encoding="utf-8")
        print(f"Saved HTML to {html_path}")

    soup = BeautifulSoup(html_text, "html.parser")

    rich_tags = extract_rich_tags_from_scripts(html_text)

    result = {
        "appid": args.appid,
        "basic_info": extract_basic_page_info(soup),
        "tags": rich_tags or extract_tags(soup),
        "more_like_this_appids": extract_more_like_this_appids(soup),
        "linked_apps": extract_linked_apps(soup, args.appid),
        "fetched_at_unix": int(time.time()),
    }

    output_path = Path(args.output)
    ensure_parent_dir(output_path)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Saved signals to {output_path}")
    print()
    print("Page title:", result["basic_info"].get("page_title"))
    print("Tags:", [tag["name"] for tag in result["tags"]])
    print("More Like This appids:", result["more_like_this_appids"])
    print("Linked apps found:", len(result["linked_apps"]))

    print("\nFirst linked apps:")
    for app in result["linked_apps"][:15]:
        print(app)


if __name__ == "__main__":
    main()