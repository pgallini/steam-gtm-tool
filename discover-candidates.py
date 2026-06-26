import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import re
import requests
from bs4 import BeautifulSoup

from dotenv import load_dotenv
from openai import OpenAI

# IMPORTANT:
# Your existing file is probably named search-steam.py.
# Python cannot import files with hyphens, so run:
#
#   cp search-steam.py search_steam.py
#
# This import expects search_steam.py to expose:
#   search_steam(query: str, max_results: int, country="us", language="english")
from search_steam import search_steam


load_dotenv()

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


TAG_STRATEGY_SCHEMA = {
    "name": "steam_tag_discovery_strategy",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "anchor_tags": {
                "type": "array",
                "items": {"type": "string"},
            },
            "supporting_tags": {
                "type": "array",
                "items": {"type": "string"},
            },
            "broad_tags": {
                "type": "array",
                "items": {"type": "string"},
            },
            "search_queries": {
                "type": "array",
                "items": {"type": "string"},
            },
            "reasoning_summary": {
                "type": "string",
            },
        },
        "required": [
            "anchor_tags",
            "supporting_tags",
            "broad_tags",
            "search_queries",
            "reasoning_summary",
        ],
    },
    "strict": True,
}

def search_steam_by_tag_ids(
    *,
    tag_ids: list[int],
    max_results: int = 50,
) -> list[dict]:
    tag_param = ",".join(str(tag_id) for tag_id in tag_ids)

    response = requests.get(
        "https://store.steampowered.com/search/",
        params={
            "tags": tag_param,
            "ndl": "1",
        },
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    results = []

    for row in soup.select("a.search_result_row"):
        appid_raw = row.get("data-ds-appid")

        if not appid_raw:
            match = re.search(r"/app/(\d+)", row.get("href", ""))
            appid_raw = match.group(1) if match else None

        if not appid_raw:
            continue

        title_el = row.select_one(".title")
        price_el = row.select_one(".discount_final_price")
        release_el = row.select_one(".search_released")

        results.append(
            {
                "appid": int(appid_raw),
                "title": title_el.get_text(strip=True) if title_el else None,
                "price": price_el.get_text(strip=True) if price_el else None,
                "release_date": release_el.get_text(strip=True) if release_el else None,
                "source_url": response.url,
            }
        )

        if len(results) >= max_results:
            break

    return results

def get_tag_by_name(seed_tags: list[dict], tag_name: str) -> dict | None:
    for tag in seed_tags:
        if tag.get("name") == tag_name:
            return tag
    return None

def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_appid(value: Any) -> int | None:
    """
    Steam app IDs should be integers.

    Search results and JSON files may contain them as strings, ints, or missing values.
    This helper keeps the rest of the script clean.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_seed_tags(seed_signals: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Return the seed game's Steam tags in their existing order.

    get-page-signals.py should already have extracted tags as dictionaries like:
      {"tagid": 916648, "name": "Creature Collector", "count": 794, "browseable": true}

    If older output only has tag names, this still works.
    """
    tags = seed_signals.get("tags") or []

    normalized = []

    for index, tag in enumerate(tags, start=1):
        if isinstance(tag, str):
            normalized.append(
                {
                    "name": tag,
                    "rank": index,
                    "tagid": None,
                    "count": None,
                    "browseable": None,
                }
            )
        elif isinstance(tag, dict) and tag.get("name"):
            normalized.append(
                {
                    "name": tag.get("name"),
                    "rank": index,
                    "tagid": tag.get("tagid"),
                    "count": tag.get("count"),
                    "browseable": tag.get("browseable"),
                }
            )

    return normalized


def get_tag_names(tags: list[dict[str, Any]]) -> list[str]:
    return [tag["name"] for tag in tags if tag.get("name")]


def add_candidate(
    candidates: dict[int, dict[str, Any]],
    *,
    appid: int,
    name: str | None,
    source_type: str,
    source_detail: dict[str, Any],
) -> None:
    """
    Add or update a candidate game.

    Important design choice:
    A candidate can be discovered multiple ways. We preserve every source instead
    of overwriting it. This helps explain why the game was included later.
    """
    if appid not in candidates:
        candidates[appid] = {
            "appid": appid,
            "name": name,
            "sources": [],
        }

    if name and not candidates[appid].get("name"):
        candidates[appid]["name"] = name

    candidates[appid]["sources"].append(
        {
            "source_type": source_type,
            "source_detail": source_detail,
        }
    )


def add_more_like_this(
    candidates: dict[int, dict[str, Any]],
    seed_signals: dict[str, Any],
) -> None:
    """
    Add Steam's built-in "More Like This" recommendations.

    This was the original prototype source. It is still useful, but it should
    no longer be the only source because it can miss strategically important comps.
    """
    seed_appid = seed_signals.get("appid")
    appids = seed_signals.get("more_like_this_appids") or []

    for rank, raw_appid in enumerate(appids, start=1):
        appid = normalize_appid(raw_appid)

        if not appid:
            continue

        add_candidate(
            candidates,
            appid=appid,
            name=None,
            source_type="more_like_this",
            source_detail={
                "seed_appid": seed_appid,
                "rank": rank,
            },
        )


def build_fallback_strategy(seed_tags: list[dict[str, Any]], max_queries: int) -> dict[str, Any]:
    """
    Fallback when OPENAI_API_KEY is missing.

    This is intentionally simple:
    - top 4 tags become anchors
    - next 6 become supporting tags
    - broad tags are unknown
    - search queries are made from top tags and simple combinations

    This is not as good as the LLM strategy, but it keeps the script runnable.
    """
    names = get_tag_names(seed_tags)

    anchor_tags = names[:4]
    supporting_tags = names[4:10]
    broad_tags = []

    queries = []

    for tag in anchor_tags:
        queries.append(tag)

    if len(anchor_tags) >= 2:
        queries.append(f"{anchor_tags[0]} {anchor_tags[1]}")

    if len(anchor_tags) >= 3:
        queries.append(f"{anchor_tags[0]} {anchor_tags[2]}")

    if len(anchor_tags) >= 4:
        queries.append(f"{anchor_tags[0]} {anchor_tags[1]} {anchor_tags[3]}")

    return {
        "anchor_tags": anchor_tags,
        "supporting_tags": supporting_tags,
        "broad_tags": broad_tags,
        "search_queries": queries[:max_queries],
        "reasoning_summary": "Fallback strategy used because OPENAI_API_KEY was not available.",
    }


def get_llm_tag_strategy(
    *,
    model: str,
    seed_name: str,
    seed_appid: int | None,
    seed_tags: list[dict[str, Any]],
    max_anchor_tags: int,
    max_supporting_tags: int,
    max_queries: int,
) -> dict[str, Any]:
    """
    Ask the LLM to decide which Steam tags are strategically important.

    This replaces the flawed hard-coded tag list.

    Why:
    - "Creature Collector" may be crucial for one game.
    - "Farming Sim" may be crucial for another.
    - "Deckbuilder" may be crucial for another.
    - Broad tags like RPG, Action, Adventure, or 2D may be useful context but
      should not dominate discovery.

    The LLM uses the seed game's actual Steam tag list and returns:
    - anchor_tags: distinctive tags that should drive discovery
    - supporting_tags: useful context tags
    - broad_tags: too generic to drive discovery alone
    - search_queries: practical Steam search queries
    """
    if not client.api_key:
        return build_fallback_strategy(seed_tags, max_queries=max_queries)

    payload = {
        "task": "Create a Steam competitor discovery strategy from the seed game's Steam tags.",
        "seed_game": {
            "name": seed_name,
            "appid": seed_appid,
            "steam_tags": seed_tags,
        },
        "instructions": [
            "Classify tags based on strategic usefulness for competitor discovery.",
            "Anchor tags should be distinctive gameplay, audience, genre, or fantasy signals.",
            "Supporting tags add useful context but should usually be combined with anchors.",
            "Broad tags are too generic to drive discovery by themselves.",
            "Do not invent tags. Use anchor/supporting/broad tags only from the supplied Steam tags.",
            "Search queries may combine supplied tags into concise Steam-search-friendly phrases.",
            "Prefer queries that will find strategically similar games, not just thematically similar games.",
            "Avoid relying only on mood/aesthetic tags if mechanical tags are present.",
            "Keep search queries short, usually 1-4 words.",
        ],
        "limits": {
            "max_anchor_tags": max_anchor_tags,
            "max_supporting_tags": max_supporting_tags,
            "max_search_queries": max_queries,
        },
    }

    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    "You are a Steam market research strategist. "
                    "You help discover comparable games from Steam tag profiles."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": TAG_STRATEGY_SCHEMA["name"],
                "schema": TAG_STRATEGY_SCHEMA["schema"],
                "strict": True,
            }
        },
    )

    strategy = json.loads(response.output_text)

    # Be defensive: cap outputs in case a model ignores limits.
    strategy["anchor_tags"] = strategy.get("anchor_tags", [])[:max_anchor_tags]
    strategy["supporting_tags"] = strategy.get("supporting_tags", [])[:max_supporting_tags]
    strategy["search_queries"] = strategy.get("search_queries", [])[:max_queries]

    return strategy


def add_default_anchor_queries(strategy: dict[str, Any], max_queries: int) -> list[str]:
    """
    Ensure we search individual anchor tags even if the LLM only gave combo queries.

    For Blood Cult 2, this makes sure a tag like "Creature Collector" gets searched directly.
    For other games, this does the same for their most distinctive tags.
    """
    queries = []

    for tag in strategy.get("anchor_tags", []):
        if tag not in queries:
            queries.append(tag)

    for query in strategy.get("search_queries", []):
        if query not in queries:
            queries.append(query)

    return queries[:max_queries]


def add_search_results(
    candidates: dict[int, dict[str, Any]],
    *,
    query: str,
    source_type: str,
    max_results: int,
    sleep_seconds: float,
) -> None:
    """
    Search Steam and add the results as candidates.

    search-steam.py returns rows with:
      appid, title, url, release_date, price, review_summary, platforms
    """
    print(f"Searching Steam: {query}")

    try:
        results = search_steam(query=query, max_results=max_results)
    except Exception as e:
        print(f"Search failed for query '{query}': {e}")
        return

    for rank, result in enumerate(results, start=1):
        appid = normalize_appid(result.get("appid"))

        if not appid:
            continue

        add_candidate(
            candidates,
            appid=appid,
            name=result.get("title"),
            source_type=source_type,
            source_detail={
                "query": query,
                "rank": rank,
                "price": result.get("price"),
                "release_date": result.get("release_date"),
                "review_summary": result.get("review_summary"),
            },
        )

    time.sleep(sleep_seconds)

def add_tag_id_search_results(
    candidates: dict[int, dict[str, Any]],
    *,
    seed_tags: list[dict[str, Any]],
    tag_names: list[str],
    source_type: str,
    max_results: int,
    sleep_seconds: float,
) -> None:
    """
    Search Steam using real Steam tag IDs, not text search.

    This is the key fix. Text search for "Creature Collector" searches words.
    Tag ID search for 916648 searches games tagged Creature Collector.
    """
    tag_ids = []

    for tag_name in tag_names:
        tag = get_tag_by_name(seed_tags, tag_name)

        if not tag or not tag.get("tagid"):
            print(f"No tag ID found for tag: {tag_name}")
            return

        tag_ids.append(int(tag["tagid"]))

    print(f"Searching Steam tag IDs: {tag_names} -> {tag_ids}")

    try:
        results = search_steam_by_tag_ids(
            tag_ids=tag_ids,
            max_results=max_results,
        )
    except Exception as e:
        print(f"Tag ID search failed for {tag_names}: {e}")
        return

    for rank, result in enumerate(results, start=1):
        appid = normalize_appid(result.get("appid"))

        if not appid:
            continue

        add_candidate(
            candidates,
            appid=appid,
            name=result.get("title"),
            source_type=source_type,
            source_detail={
                "tags": tag_names,
                "tag_ids": tag_ids,
                "rank": rank,
                "price": result.get("price"),
                "release_date": result.get("release_date"),
                "source_url": result.get("source_url"),
            },
        )

    time.sleep(sleep_seconds)

def write_candidates(path: Path, candidates: dict[int, dict[str, Any]]) -> None:
    """
    Write the merged candidate list.

    Sorting puts multi-source candidates first because appearing from multiple
    discovery methods is a useful relevance signal.
    """
    rows = list(candidates.values())

    rows.sort(
        key=lambda row: (
            -len(row.get("sources") or []),
            row.get("appid") or 0,
        )
    )

    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {len(rows)} candidates to {path}")


def write_strategy(path: Path, strategy: dict[str, Any]) -> None:
    """
    Save the LLM/fallback tag strategy for inspection.

    This is important because if discovery misses something, we can inspect whether
    the tag strategy underweighted a key concept.
    """
    path.write_text(json.dumps(strategy, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved discovery strategy to {path}")


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument("--seed-signals", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--strategy-output", default=None)

    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--sleep", type=float, default=1.0)

    parser.add_argument("--max-anchor-tags", type=int, default=6)
    parser.add_argument("--max-supporting-tags", type=int, default=8)
    parser.add_argument("--max-search-queries", type=int, default=12)
    parser.add_argument("--max-results-per-search", type=int, default=25)

    parser.add_argument(
        "--skip-more-like-this",
        action="store_true",
        help="Skip Steam More Like This candidate source.",
    )

    parser.add_argument(
        "--skip-search",
        action="store_true",
        help="Skip Steam search candidate source.",
    )

    args = parser.parse_args()

    seed_signals_path = Path(args.seed_signals)
    seed_signals = load_json(seed_signals_path)

    seed_appid = normalize_appid(seed_signals.get("appid"))
    seed_name = seed_signals.get("basic_info", {}).get("page_title") or str(seed_appid)

    seed_tags = get_seed_tags(seed_signals)

    print(f"Seed: {seed_name} ({seed_appid})")
    print("Seed tags:")
    for tag in seed_tags:
        count = tag.get("count")
        count_text = f" count={count}" if count is not None else ""
        print(f"  {tag['rank']}. {tag['name']}{count_text}")

    strategy = get_llm_tag_strategy(
        model=args.model,
        seed_name=seed_name,
        seed_appid=seed_appid,
        seed_tags=seed_tags,
        max_anchor_tags=args.max_anchor_tags,
        max_supporting_tags=args.max_supporting_tags,
        max_queries=args.max_search_queries,
    )

    print("\nDiscovery strategy:")
    print(f"  Anchor tags: {strategy.get('anchor_tags')}")
    print(f"  Supporting tags: {strategy.get('supporting_tags')}")
    print(f"  Broad tags: {strategy.get('broad_tags')}")
    print(f"  Search queries: {strategy.get('search_queries')}")
    print(f"  Reasoning: {strategy.get('reasoning_summary')}")

    strategy_output = (
        Path(args.strategy_output)
        if args.strategy_output
        else Path(args.output).with_name(Path(args.output).stem + "_strategy.json")
    )

    write_strategy(strategy_output, strategy)

    candidates: dict[int, dict[str, Any]] = {}

    if not args.skip_more_like_this:
        add_more_like_this(candidates, seed_signals)

    if not args.skip_search:
        anchor_tags = strategy.get("anchor_tags", [])

        # 1. Search each anchor tag using real Steam tag IDs.
        for tag_name in anchor_tags:
            add_tag_id_search_results(
                candidates,
                seed_tags=seed_tags,
                tag_names=[tag_name],
                source_type="steam_tag_id_search",
                max_results=args.max_results_per_search,
                sleep_seconds=args.sleep,
            )

        # 2. Search useful two-tag combinations using real Steam tag IDs.
        for i in range(len(anchor_tags)):
            for j in range(i + 1, len(anchor_tags)):
                add_tag_id_search_results(
                    candidates,
                    seed_tags=seed_tags,
                    tag_names=[anchor_tags[i], anchor_tags[j]],
                    source_type="steam_tag_combo_search",
                    max_results=args.max_results_per_search,
                    sleep_seconds=args.sleep,
                )

        # 3. Keep text search as a weaker supplemental source.
        # This helps find titles/descriptions that match LLM-generated search phrases,
        # but it should not be treated as true tag discovery.
        queries = add_default_anchor_queries(strategy, max_queries=args.max_search_queries)

        for query in queries:
            add_search_results(
                candidates,
                query=query,
                source_type="steam_text_search",
                max_results=args.max_results_per_search,
                sleep_seconds=args.sleep,
            )

    write_candidates(Path(args.output), candidates)


if __name__ == "__main__":
    main()