import argparse
import csv
import subprocess
import time
from pathlib import Path


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def as_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).replace(",", "")))
    except ValueError:
        return default


def get_tier1_direct_comps(rows: list[dict]) -> list[dict]:
    return [
        row for row in rows
        if row.get("priority_tier") == "Tier 1"
        and row.get("bucket") == "Direct Comp"
    ]


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


def run_review_fetch(
    *,
    appid: int,
    name: str,
    review_type: str,
    max_pages: int,
    sleep_seconds: float,
    output_dir: Path,
) -> None:
    slug = safe_name(name)

    output_jsonl = output_dir / f"reviews_{appid}_{slug}_{review_type}.jsonl"
    output_csv = output_dir / f"reviews_{appid}_{slug}_{review_type}.csv"

    cmd = [
        "python",
        "get-reviews.py",
        "--appid",
        str(appid),
        "--review-type",
        review_type,
        "--max-pages",
        str(max_pages),
        "--sleep",
        str(sleep_seconds),
        "--output",
        str(output_jsonl),
        "--csv",
        str(output_csv),
    ]

    print(f"\nFetching {review_type} reviews for {name} ({appid})")
    print(" ".join(cmd))

    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Enhanced comp CSV")
    parser.add_argument("--output-dir", default="tier1_reviews")
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    rows = read_csv(Path(args.input))
    comps = get_tier1_direct_comps(rows)

    if args.limit:
        comps = comps[: args.limit]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Tier 1 direct comps found: {len(comps)}")

    for comp in comps:
        appid = as_int(comp.get("appid"))
        name = comp.get("name") or str(appid)

        run_review_fetch(
            appid=appid,
            name=name,
            review_type="positive",
            max_pages=args.max_pages,
            sleep_seconds=args.sleep,
            output_dir=output_dir,
        )

        time.sleep(args.sleep)

        run_review_fetch(
            appid=appid,
            name=name,
            review_type="negative",
            max_pages=args.max_pages,
            sleep_seconds=args.sleep,
            output_dir=output_dir,
        )

        time.sleep(args.sleep)

    print("\nDone fetching Tier 1 reviews.")


if __name__ == "__main__":
    main()