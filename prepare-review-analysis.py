import argparse
import json
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    return records


def review_score(record: dict[str, Any]) -> float:
    """
    Prefer reviews that are helpful/upvoted and have meaningful text.
    """
    text = record.get("review") or ""
    votes_up = record.get("votes_up") or 0
    weighted = record.get("weighted_vote_score") or "0"

    try:
        weighted_float = float(weighted)
    except ValueError:
        weighted_float = 0.0

    length_bonus = min(len(text) / 500, 2)

    return votes_up + weighted_float + length_bonus


def clean_review_text(text: str, max_chars: int = 1200) -> str:
    text = text.replace("\r", " ").replace("\n", " ").strip()
    text = " ".join(text.split())

    if len(text) > max_chars:
        text = text[:max_chars] + "..."

    return text


def format_review(record: dict[str, Any]) -> str:
    voted_up = record.get("voted_up")
    sentiment = "Positive" if voted_up else "Negative"

    playtime_minutes = record.get("playtime_at_review")
    playtime_hours = None

    if isinstance(playtime_minutes, int):
        playtime_hours = round(playtime_minutes / 60, 1)

    text = clean_review_text(record.get("review") or "")

    return (
        f"- Sentiment: {sentiment}\n"
        f"  Helpful votes: {record.get('votes_up')}\n"
        f"  Playtime at review: {playtime_hours} hours\n"
        f"  Review: {text}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--game-name", required=True)
    parser.add_argument("--appid", required=True)
    parser.add_argument("--max-positive", type=int, default=50)
    parser.add_argument("--max-negative", type=int, default=50)
    args = parser.parse_args()

    records = load_jsonl(Path(args.input))

    positive = [r for r in records if r.get("voted_up") is True and r.get("review")]
    negative = [r for r in records if r.get("voted_up") is False and r.get("review")]

    positive.sort(key=review_score, reverse=True)
    negative.sort(key=review_score, reverse=True)

    positive = positive[: args.max_positive]
    negative = negative[: args.max_negative]

    lines = []

    lines.append(f"# Steam Review Analysis Input")
    lines.append("")
    lines.append(f"Game: {args.game_name}")
    lines.append(f"App ID: {args.appid}")
    lines.append(f"Total reviews loaded: {len(records)}")
    lines.append(f"Positive reviews selected: {len(positive)}")
    lines.append(f"Negative reviews selected: {len(negative)}")
    lines.append("")
    lines.append("## Analysis Instructions")
    lines.append("")
    lines.append("Analyze these Steam reviews for GTM and competitor research.")
    lines.append("")
    lines.append("Return:")
    lines.append("1. Top praise themes")
    lines.append("2. Top complaint themes")
    lines.append("3. Feature/friction issues")
    lines.append("4. Price/value perception")
    lines.append("5. Player language worth reusing in positioning")
    lines.append("6. Risks or opportunities for a comparable game")
    lines.append("")
    lines.append("## Positive Reviews")
    lines.append("")

    for record in positive:
        lines.append(format_review(record))
        lines.append("")

    lines.append("## Negative Reviews")
    lines.append("")

    for record in negative:
        lines.append(format_review(record))
        lines.append("")

    output_path = Path(args.output)
    output_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"Saved analysis input to {output_path}")


if __name__ == "__main__":
    main()