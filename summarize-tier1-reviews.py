import argparse
import csv
import subprocess
from pathlib import Path


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def safe_name(name: str) -> str:
    return (
        name.lower()
        .replace(" ", "_")
        .replace(":", "")
        .replace("/", "_")
        .replace("\\", "_")
        .replace("™", "")
        .replace("'", "")
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Enhanced comp CSV")
    parser.add_argument("--review-dir", default="tier1_reviews")
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--max-positive", type=int, default=50)
    parser.add_argument("--max-negative", type=int, default=50)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    rows = read_csv(Path(args.input))

    comps = [
        row for row in rows
        if row.get("priority_tier") == "Tier 1"
        and row.get("bucket") == "Direct Comp"
    ]

    if args.limit:
        comps = comps[: args.limit]

    review_dir = Path(args.review_dir)

    for comp in comps:
        appid = comp["appid"]
        name = comp["name"]
        slug = safe_name(name)

        positive = review_dir / f"reviews_{appid}_{slug}_positive.jsonl"
        negative = review_dir / f"reviews_{appid}_{slug}_negative.jsonl"

        if not positive.exists() or not negative.exists():
            print(f"Skipping {name}; missing review files.")
            print(f"Expected: {positive}")
            print(f"Expected: {negative}")
            continue

        output_json = review_dir / f"summary_{appid}_{slug}.json"
        output_md = review_dir / f"summary_{appid}_{slug}.md"

        cmd = [
            "python",
            "llm-summarize-reviews.py",
            "--appid",
            str(appid),
            "--game-name",
            name,
            "--positive",
            str(positive),
            "--negative",
            str(negative),
            "--output-json",
            str(output_json),
            "--output-md",
            str(output_md),
            "--model",
            args.model,
            "--max-positive",
            str(args.max_positive),
            "--max-negative",
            str(args.max_negative),
        ]

        print(f"\nSummarizing reviews for {name}")
        subprocess.run(cmd, check=True)

    print("\nDone summarizing Tier 1 reviews.")


if __name__ == "__main__":
    main()