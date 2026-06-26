import argparse
import json
from pathlib import Path


def load_summaries(summary_dir: Path) -> list[dict]:
    summaries = []

    for path in sorted(summary_dir.glob("summary_*.json")):
        summaries.append(json.loads(path.read_text(encoding="utf-8")))

    return summaries


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary-dir", default="tier1_reviews")
    parser.add_argument("--output", default="tier1_review_insights_report.md")
    parser.add_argument("--seed-name", default="Seed Game")
    parser.add_argument("--rollup-md", default=None)
    args = parser.parse_args()

    summaries = load_summaries(Path(args.summary_dir))

    lines = []

    lines.append(f"# Tier 1 Steam Review Insights: {args.seed_name}")
    lines.append("")
    lines.append(
        "This report summarizes positive and negative Steam reviews for Tier 1 direct comps. "
        "The goal is to identify what players praise, what they complain about, what expectations exist in the category, "
        "and what positioning opportunities may exist for similar games."
    )
    lines.append("")

    lines.append("## Games Analyzed")
    lines.append("")
    for s in summaries:
        lines.append(f"- **{s['game']}** (`{s['appid']}`)")
    lines.append("")

    rollup_path = Path(args.rollup_md) if args.rollup_md else None

    if rollup_path and rollup_path.exists():
        lines.append(rollup_path.read_text(encoding="utf-8").strip())
        lines.append("")
    else:
        lines.append("## Cross-Game Takeaways")
        lines.append("")
        lines.append("_No roll-up file provided._")
        lines.append("")

    for s in summaries:
        lines.append(f"---")
        lines.append("")
        lines.append(f"## {s['game']}")
        lines.append("")
        lines.append(s["summary"])
        lines.append("")

        lines.append("### Praise Themes")
        lines.append("")
        for theme in s["top_praise_themes"][:5]:
            lines.append(f"- **{theme['theme']}** — {theme['why_it_matters']}")
        lines.append("")

        lines.append("### Complaint Themes")
        lines.append("")
        for theme in s["top_complaint_themes"][:5]:
            lines.append(f"- **{theme['theme']}** — {theme['why_it_matters']}")
        lines.append("")

        lines.append("### Positioning Opportunities")
        lines.append("")
        for item in s["positioning_opportunities"][:6]:
            lines.append(f"- {item}")
        lines.append("")

        lines.append("### Risks for Similar Games")
        lines.append("")
        for item in s["risks_for_similar_game"][:6]:
            lines.append(f"- {item}")
        lines.append("")

        lines.append("### Recommended Actions")
        lines.append("")
        for item in s["recommended_actions"][:6]:
            lines.append(f"- {item}")
        lines.append("")

    Path(args.output).write_text("\n".join(lines), encoding="utf-8")

    print(f"Saved report: {args.output}")


if __name__ == "__main__":
    main()