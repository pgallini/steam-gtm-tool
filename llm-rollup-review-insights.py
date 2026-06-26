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
    "name": "steam_review_rollup_gtm_insights",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "seed_game": {"type": "string"},
            "executive_summary": {"type": "string"},
            "cross_game_takeaways": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "theme": {"type": "string"},
                        "insight": {"type": "string"},
                        "supporting_games": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["theme", "insight", "supporting_games"],
                },
            },
            "common_praise_drivers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "driver": {"type": "string"},
                        "why_it_matters": {"type": "string"},
                        "supporting_games": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["driver", "why_it_matters", "supporting_games"],
                },
            },
            "common_complaint_drivers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "risk": {"type": "string"},
                        "why_it_matters": {"type": "string"},
                        "supporting_games": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["risk", "why_it_matters", "supporting_games"],
                },
            },
            "positioning_opportunities": {
                "type": "array",
                "items": {"type": "string"},
            },
            "product_implications": {
                "type": "array",
                "items": {"type": "string"},
            },
            "messaging_recommendations": {
                "type": "array",
                "items": {"type": "string"},
            },
            "research_next_steps": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "seed_game",
            "executive_summary",
            "cross_game_takeaways",
            "common_praise_drivers",
            "common_complaint_drivers",
            "positioning_opportunities",
            "product_implications",
            "messaging_recommendations",
            "research_next_steps",
        ],
    },
    "strict": True,
}


def load_summaries(summary_dir: Path) -> list[dict[str, Any]]:
    summaries = []

    for path in sorted(summary_dir.glob("summary_*.json")):
        summaries.append(json.loads(path.read_text(encoding="utf-8")))

    return summaries


def compact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """
    Reduce each game summary to the fields needed for the roll-up.
    This keeps the LLM input compact while preserving the useful GTM signal.
    """
    return {
        "game": summary.get("game"),
        "appid": summary.get("appid"),
        "summary": summary.get("summary"),
        "top_praise_themes": summary.get("top_praise_themes", [])[:6],
        "top_complaint_themes": summary.get("top_complaint_themes", [])[:6],
        "feature_expectations": summary.get("feature_expectations", [])[:8],
        "price_value_perception": summary.get("price_value_perception"),
        "player_language": summary.get("player_language", [])[:8],
        "positioning_opportunities": summary.get("positioning_opportunities", [])[:8],
        "risks_for_similar_game": summary.get("risks_for_similar_game", [])[:8],
        "recommended_actions": summary.get("recommended_actions", [])[:8],
    }


def rollup_reviews(
    *,
    model: str,
    seed_game: str,
    summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    system_prompt = """
You are a senior go-to-market strategist for PC/Steam games.

You are creating a cross-game synthesis from per-game Steam review summaries.
Your job is to identify patterns across the closest competitor set.

Focus on:
- repeat praise drivers
- repeat complaint/risk drivers
- category expectations
- positioning opportunities
- product implications
- messaging recommendations

Be practical. Avoid generic marketing advice.
Use only the provided summaries. Do not browse the web.
""".strip()

    payload = {
        "seed_game": seed_game,
        "competitor_review_summaries": [compact_summary(s) for s in summaries],
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

    lines.append(f"## Cross-Game Takeaways")
    lines.append("")
    lines.append(result["executive_summary"])
    lines.append("")

    lines.append("### Key Patterns Across Tier 1 Comps")
    lines.append("")
    for item in result["cross_game_takeaways"]:
        games = ", ".join(item["supporting_games"])
        lines.append(f"- **{item['theme']}** — {item['insight']} _Seen in: {games}._")
    lines.append("")

    lines.append("### Common Praise Drivers")
    lines.append("")
    for item in result["common_praise_drivers"]:
        games = ", ".join(item["supporting_games"])
        lines.append(f"- **{item['driver']}** — {item['why_it_matters']} _Seen in: {games}._")
    lines.append("")

    lines.append("### Common Complaint / Risk Drivers")
    lines.append("")
    for item in result["common_complaint_drivers"]:
        games = ", ".join(item["supporting_games"])
        lines.append(f"- **{item['risk']}** — {item['why_it_matters']} _Seen in: {games}._")
    lines.append("")

    lines.append("### Positioning Opportunities")
    lines.append("")
    for item in result["positioning_opportunities"]:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("### Product Implications")
    lines.append("")
    for item in result["product_implications"]:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("### Messaging Recommendations")
    lines.append("")
    for item in result["messaging_recommendations"]:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("### Recommended Research Next Steps")
    lines.append("")
    for item in result["research_next_steps"]:
        lines.append(f"- {item}")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary-dir", default="tier1_reviews")
    parser.add_argument("--seed-name", default="Seed Game")
    parser.add_argument("--output-json", default="tier1_reviews/rollup_review_insights.json")
    parser.add_argument("--output-md", default="tier1_reviews/rollup_review_insights.md")
    parser.add_argument("--model", default="gpt-5.4-mini")
    args = parser.parse_args()

    summaries = load_summaries(Path(args.summary_dir))

    if not summaries:
        raise RuntimeError(f"No summary_*.json files found in {args.summary_dir}")

    print(f"Loaded {len(summaries)} summaries.")

    result = rollup_reviews(
        model=args.model,
        seed_game=args.seed_name,
        summaries=summaries,
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