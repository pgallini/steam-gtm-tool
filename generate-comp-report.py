import argparse
import csv
from collections import defaultdict
from pathlib import Path


def as_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).replace(",", "")))
    except ValueError:
        return default


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def clean(value) -> str:
    return " ".join(str(value or "").split())


# Backward-compatible default sort.
def sort_rows(rows: list[dict]) -> list[dict]:
    return sort_direct(rows)

def sort_direct(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda r: (
            -as_int(r.get("direct_fit_score")),
            -as_int(r.get("audience_fit_score")),
            -as_int(r.get("mechanic_fit_score")),
            -as_int(r.get("commercial_benchmark_score")),
        ),
    )


def sort_audience_context(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda r: (
            -as_int(r.get("audience_fit_score")),
            -as_int(r.get("commercial_benchmark_score")),
            -as_int(r.get("direct_fit_score")),
            -as_int(r.get("mechanic_fit_score")),
        ),
    )


def sort_mechanic_commercial(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda r: (
            -as_int(r.get("mechanic_fit_score")),
            -as_int(r.get("commercial_benchmark_score")),
            -as_int(r.get("audience_fit_score")),
            -as_int(r.get("direct_fit_score")),
        ),
    )

def dedupe_by_appid(rows: list[dict]) -> list[dict]:
    seen = set()
    deduped = []

    for row in rows:
        appid = row.get("appid")
        if appid in seen:
            continue

        seen.add(appid)
        deduped.append(row)

    return deduped

def md_table(rows: list[dict], max_rows: int = 10) -> list[str]:
    lines = []
    lines.append("| Game | Bucket | Direct | Audience | Mechanic | Commercial | Price | Recs | Why it matters |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---|")

    for row in rows[:max_rows]:
        name = clean(row.get("name"))
        url = clean(row.get("steam_url"))
        game = f"[{name}]({url})" if url else name

        lines.append(
            "| "
            + " | ".join(
                [
                    game,
                    clean(row.get("bucket")),
                    str(as_int(row.get("direct_fit_score"))),
                    str(as_int(row.get("audience_fit_score"))),
                    str(as_int(row.get("mechanic_fit_score"))),
                    str(as_int(row.get("commercial_benchmark_score"))),
                    clean(row.get("price")),
                    str(as_int(row.get("recommendations_total"))),
                    clean(row.get("reason")),
                ]
            )
            + " |"
        )

    return lines


def bullet_list(rows: list[dict], max_rows: int = 8) -> list[str]:
    lines = []

    for row in rows[:max_rows]:
        name = clean(row.get("name"))
        bucket = clean(row.get("bucket"))
        direct = as_int(row.get("direct_fit_score"))
        commercial = as_int(row.get("commercial_benchmark_score"))
        reason = clean(row.get("reason"))

        lines.append(f"- **{name}** — {bucket}; direct fit {direct}, commercial {commercial}. {reason}")

    return lines


def summarize_bucket(
    rows: list[dict],
    bucket: str,
    sorter=sort_direct,
) -> list[dict]:
    bucket_rows = [r for r in rows if r.get("bucket") == bucket]
    return sorter(bucket_rows)


def get_rows_by_tier(rows: list[dict], tier: str) -> list[dict]:
    return sort_rows([r for r in rows if r.get("priority_tier") == tier])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="comp_report.md")
    parser.add_argument("--seed-name", default="Seed Game")
    args = parser.parse_args()

    rows = read_csv(Path(args.input))

    by_bucket = defaultdict(list)

    for row in rows:
        by_bucket[row.get("bucket", "Unknown")].append(row)

    tier1 = get_rows_by_tier(rows, "Tier 1")
    tier2 = get_rows_by_tier(rows, "Tier 2")
    context = get_rows_by_tier(rows, "Context")
    ignored = get_rows_by_tier(rows, "Ignore / Context Only")

    direct = summarize_bucket(rows, "Direct Comp", sort_direct)
    adjacent = summarize_bucket(rows, "Adjacent Comp", sort_direct)
    audience = summarize_bucket(rows, "Audience Comp", sort_audience_context)
    mechanic = summarize_bucket(rows, "Mechanic Comp", sort_mechanic_commercial)
    commercial = summarize_bucket(rows, "Commercial Benchmark", sort_audience_context)
    low_fit = summarize_bucket(rows, "Low Fit / Noise", sort_direct)

    high_tag_low_fit = [
        r for r in rows
        if clean(r.get("disagreement_flag")) == "High tag overlap, low strategic fit"
    ]

    lines = []

    lines.append(f"# Steam Competitor Discovery Report: {args.seed_name}")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(
        "This report starts with Steam's **More Like This** recommendations, enriches those games with Steam metadata, "
        "then uses an LLM pass to classify each candidate by strategic usefulness. The goal is not just to find games "
        "with overlapping tags, but to separate true comps from broader audience, mechanic, commercial, or noisy matches."
    )
    lines.append("")

    lines.append("## Top Priority Comps")
    lines.append("")
    lines.extend(md_table(tier1 + tier2, max_rows=10))
    lines.append("")

    lines.append("## Tier 1: Direct Comps")
    lines.append("")
    if tier1:
        lines.extend(bullet_list(tier1, max_rows=10))
    else:
        lines.append("_No Tier 1 comps identified._")
    lines.append("")

    lines.append("## Tier 2: Secondary Direct + Adjacent Comps")
    lines.append("")
    if tier2:
        lines.extend(bullet_list(tier2, max_rows=12))
    else:
        lines.append("_No Tier 2 comps identified._")
    lines.append("")

    lines.append("## Bucket Breakdown")
    lines.append("")
    lines.append("| Bucket | Count |")
    lines.append("|---|---:|")
    for bucket, bucket_rows in sorted(by_bucket.items()):
        lines.append(f"| {bucket} | {len(bucket_rows)} |")
    lines.append("")

    lines.append("## Direct Comps")
    lines.append("")
    if direct:
        lines.extend(md_table(direct, max_rows=12))
    else:
        lines.append("_No direct comps identified._")
    lines.append("")

    lines.append("## Adjacent Comps")
    lines.append("")
    if adjacent:
        lines.extend(md_table(adjacent, max_rows=12))
    else:
        lines.append("_No adjacent comps identified._")
    lines.append("")

    lines.append("## Audience / Market Context")
    lines.append("")
    combined_context = sort_audience_context(dedupe_by_appid(audience + context))
    if combined_context:
        lines.extend(md_table(combined_context, max_rows=20))
    else:
        lines.append("_No audience/context comps identified._")
    lines.append("")

    lines.append("## Mechanic / Commercial References")
    lines.append("")
    mechanic_commercial = sort_rows(mechanic + commercial)
    if mechanic_commercial:
        lines.extend(md_table(mechanic_commercial, max_rows=12))
    else:
        lines.append("_No mechanic/commercial references identified._")
    lines.append("")

    lines.append("## Low-Fit / Noisy Results")
    lines.append("")
    if low_fit:
        lines.extend(md_table(low_fit, max_rows=12))
    else:
        lines.append("_No low-fit results identified._")
    lines.append("")

    lines.append("## Important Disagreement Flags")
    lines.append("")
    if high_tag_low_fit:
        lines.append(
            "These candidates had high deterministic tag overlap, but the LLM judged them to be weak strategic comps. "
            "This is important because it demonstrates why semantic interpretation is needed."
        )
        lines.append("")
        lines.extend(md_table(high_tag_low_fit, max_rows=10))
    else:
        lines.append("_No major high-tag/low-fit disagreements identified._")
    lines.append("")

    lines.append("## Recommended Next Research")
    lines.append("")
    lines.append("1. Pull positive and negative Steam reviews for Tier 1 direct comps.")
    lines.append("2. Summarize praise themes, complaint themes, feature expectations, and pricing/value comments.")
    lines.append("3. Compare Steam page positioning across direct comps.")
    lines.append("4. Use adjacent and mechanic comps to study specific systems, not direct positioning.")
    lines.append("5. Keep low-fit/noisy results as evidence that raw tag overlap alone is insufficient.")
    lines.append("")

    output_path = Path(args.output)
    output_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"Saved report: {output_path}")


if __name__ == "__main__":
    main()