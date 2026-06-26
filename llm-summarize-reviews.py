import argparse
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


OUTPUT_SCHEMA = {
    "name": "steam_review_gtm_summary",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "game": {"type": "string"},
            "appid": {"type": "integer"},
            "summary": {"type": "string"},
            "top_praise_themes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "theme": {"type": "string"},
                        "why_it_matters": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["theme", "why_it_matters", "evidence"],
                },
            },
            "top_complaint_themes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "theme": {"type": "string"},
                        "why_it_matters": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["theme", "why_it_matters", "evidence"],
                },
            },
            "feature_expectations": {
                "type": "array",
                "items": {"type": "string"},
            },
            "price_value_perception": {"type": "string"},
            "player_language": {
                "type": "array",
                "items": {"type": "string"},
            },
            "positioning_opportunities": {
                "type": "array",
                "items": {"type": "string"},
            },
            "risks_for_similar_game": {
                "type": "array",
                "items": {"type": "string"},
            },
            "recommended_actions": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "game",
            "appid",
            "summary",
            "top_praise_themes",
            "top_complaint_themes",
            "feature_expectations",
            "price_value_perception",
            "player_language",
            "positioning_opportunities",
            "risks_for_similar_game",
            "recommended_actions",
        ],
    },
    "strict": True,
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []

    if not path.exists():
        return rows

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))

    return rows


def review_score(review: dict[str, Any]) -> float:
    text = review.get("review") or ""
    votes = review.get("votes_up") or 0

    weighted = review.get("weighted_vote_score") or "0"

    try:
        weighted_float = float(weighted)
    except ValueError:
        weighted_float = 0.0

    length_score = min(len(text) / 500, 2)

    return votes + weighted_float + length_score


def clean_text(text: str, max_chars: int = 1000) -> str:
    text = text.replace("\r", " ").replace("\n", " ")
    text = " ".join(text.split())

    if len(text) > max_chars:
        return text[:max_chars] + "..."

    return text


def select_reviews(rows: list[dict[str, Any]], max_reviews: int) -> list[dict[str, Any]]:
    rows = [r for r in rows if r.get("review")]
    rows.sort(key=review_score, reverse=True)
    return rows[:max_reviews]


def format_review(review: dict[str, Any]) -> dict[str, Any]:
    playtime_minutes = review.get("playtime_at_review")
    playtime_hours = None

    if isinstance(playtime_minutes, int):
        playtime_hours = round(playtime_minutes / 60, 1)

    return {
        "voted_up": review.get("voted_up"),
        "votes_up": review.get("votes_up"),
        "playtime_hours": playtime_hours,
        "steam_purchase": review.get("steam_purchase"),
        "received_for_free": review.get("received_for_free"),
        "review": clean_text(review.get("review") or ""),
    }


def summarize_reviews(
    *,
    model: str,
    appid: int,
    game_name: str,
    positive_reviews: list[dict[str, Any]],
    negative_reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    system_prompt = """
You are a senior go-to-market strategist for PC/Steam games.

Analyze Steam reviews for competitor research. Your job is to extract practical GTM insights:
- what players love
- what players complain about
- what expectations this creates for similar games
- language marketers might reuse
- risks and opportunities for a competing or adjacent game

Use only the review data provided. Do not browse the web.
Keep the output concise, evidence-based, and useful to a game marketer.
Do not include long direct quotes; use short snippets only.
""".strip()

    payload = {
        "game": game_name,
        "appid": appid,
        "positive_reviews": [format_review(r) for r in positive_reviews],
        "negative_reviews": [format_review(r) for r in negative_reviews],
    }

    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": OUTPUT_SCHEMA["name"],
                "schema": OUTPUT_SCHEMA["schema"],
                "strict": True,
            }
        },
    )

    return json.loads(response.output_text)


def write_markdown(path: Path, result: dict[str, Any]) -> None:
    lines = []

    lines.append(f"# Steam Review GTM Summary: {result['game']}")
    lines.append("")
    lines.append(f"App ID: `{result['appid']}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(result["summary"])
    lines.append("")

    lines.append("## Top Praise Themes")
    lines.append("")
    for item in result["top_praise_themes"]:
        lines.append(f"### {item['theme']}")
        lines.append("")
        lines.append(item["why_it_matters"])
        lines.append("")
        if item["evidence"]:
            lines.append("Evidence:")
            for e in item["evidence"]:
                lines.append(f"- {e}")
        lines.append("")

    lines.append("## Top Complaint Themes")
    lines.append("")
    for item in result["top_complaint_themes"]:
        lines.append(f"### {item['theme']}")
        lines.append("")
        lines.append(item["why_it_matters"])
        lines.append("")
        if item["evidence"]:
            lines.append("Evidence:")
            for e in item["evidence"]:
                lines.append(f"- {e}")
        lines.append("")

    lines.append("## Feature Expectations")
    lines.append("")
    for item in result["feature_expectations"]:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## Price / Value Perception")
    lines.append("")
    lines.append(result["price_value_perception"])
    lines.append("")

    lines.append("## Player Language Worth Noting")
    lines.append("")
    for item in result["player_language"]:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## Positioning Opportunities")
    lines.append("")
    for item in result["positioning_opportunities"]:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## Risks for a Similar Game")
    lines.append("")
    for item in result["risks_for_similar_game"]:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## Recommended Actions")
    lines.append("")
    for item in result["recommended_actions"]:
        lines.append(f"- {item}")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--appid", type=int, required=True)
    parser.add_argument("--game-name", required=True)
    parser.add_argument("--positive", required=True)
    parser.add_argument("--negative", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--max-positive", type=int, default=50)
    parser.add_argument("--max-negative", type=int, default=50)
    args = parser.parse_args()

    positive = select_reviews(load_jsonl(Path(args.positive)), args.max_positive)
    negative = select_reviews(load_jsonl(Path(args.negative)), args.max_negative)

    print(f"Positive reviews selected: {len(positive)}")
    print(f"Negative reviews selected: {len(negative)}")

    result = summarize_reviews(
        model=args.model,
        appid=args.appid,
        game_name=args.game_name,
        positive_reviews=positive,
        negative_reviews=negative,
    )

    Path(args.output_json).write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    write_markdown(Path(args.output_md), result)

    print(f"Saved JSON: {args.output_json}")
    print(f"Saved Markdown: {args.output_md}")


if __name__ == "__main__":
    main()