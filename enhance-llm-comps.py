import argparse
import csv
from pathlib import Path


DIRECT_BUCKETS = {"Direct Comp"}
ADJACENT_BUCKETS = {"Adjacent Comp"}
CONTEXT_BUCKETS = {
    "Audience Comp",
    "Mechanic Comp",
    "Commercial Benchmark",
    "Aspirational / Market Context",
}
LOW_BUCKETS = {"Low Fit / Noise"}


def as_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).replace(",", "")))
    except ValueError:
        return default


def get_priority_tier(row: dict) -> str:
    bucket = row.get("bucket", "")
    direct = as_int(row.get("direct_fit_score"))
    audience = as_int(row.get("audience_fit_score"))
    commercial = as_int(row.get("commercial_benchmark_score"))
    confidence = as_int(row.get("confidence"))
    recommendations = as_int(row.get("recommendations_total"))

    if bucket in DIRECT_BUCKETS and direct >= 80 and confidence >= 80:
        return "Tier 1"

    if bucket in DIRECT_BUCKETS:
        return "Tier 2"

    if bucket in ADJACENT_BUCKETS and (audience >= 70 or commercial >= 70):
        return "Tier 2"

    if bucket in CONTEXT_BUCKETS and (commercial >= 80 or recommendations >= 100000):
        return "Context"

    if bucket in LOW_BUCKETS:
        return "Ignore / Context Only"

    return "Tier 3"


def get_disagreement_flag(row: dict) -> str:
    code_score = as_int(row.get("code_fit_score"))
    direct = as_int(row.get("direct_fit_score"))
    commercial = as_int(row.get("commercial_benchmark_score"))

    if code_score >= 140 and direct <= 30:
        return "High tag overlap, low strategic fit"

    if code_score <= 80 and direct >= 75:
        return "Low tag overlap, high strategic fit"

    if commercial >= 90 and direct <= 30:
        return "High commercial signal, low direct fit"

    return ""


def get_monetization_flag(row: dict) -> str:
    price = (row.get("price") or "").strip().lower()
    recommendations = as_int(row.get("recommendations_total"))

    if price == "free":
        return "Free-to-play / weak pricing benchmark"

    if recommendations == 0:
        return "No recommendation signal"

    return ""


def get_strategic_note(row: dict) -> str:
    bucket = row.get("bucket", "")
    tier = row.get("priority_tier", "")
    disagreement = row.get("disagreement_flag", "")

    if bucket == "Direct Comp":
        return "Use as a primary comp for positioning, feature expectations, Steam page framing, and review mining."

    if bucket == "Adjacent Comp":
        return "Use as a secondary comp for audience overlap, feature inspiration, scope, and commercial context."

    if bucket == "Audience Comp":
        return "Use to understand broader audience behavior, but avoid treating it as a direct creative or pricing comp."

    if bucket == "Mechanic Comp":
        return "Use to study specific systems or mechanics, not overall positioning."

    if bucket == "Commercial Benchmark":
        return "Use for commercial scale/context only; avoid using it for direct positioning."

    if bucket == "Aspirational / Market Context":
        return "Use as market context or aspiration, not as a normal benchmark."

    if bucket == "Low Fit / Noise":
        if disagreement:
            return "Useful as an example of why raw tag overlap can mislead; otherwise deprioritize."

        return "Deprioritize for this comp set."

    return f"Review manually. Suggested priority: {tier}"


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = list(rows[0].keys())

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="llm_comp_classifications_enhanced.csv")
    args = parser.parse_args()

    rows = read_csv(Path(args.input))

    enhanced = []

    for row in rows:
        row = dict(row)
        row["priority_tier"] = get_priority_tier(row)
        row["disagreement_flag"] = get_disagreement_flag(row)
        row["monetization_flag"] = get_monetization_flag(row)
        row["strategic_note"] = get_strategic_note(row)
        enhanced.append(row)

    # Sort for client review: Tier 1, Tier 2, Context, Tier 3, Ignore
    tier_order = {
        "Tier 1": 1,
        "Tier 2": 2,
        "Context": 3,
        "Tier 3": 4,
        "Ignore / Context Only": 5,
    }

    enhanced.sort(
        key=lambda r: (
            tier_order.get(r.get("priority_tier"), 99),
            -as_int(r.get("direct_fit_score")),
            -as_int(r.get("commercial_benchmark_score")),
        )
    )

    # Put new fields near the front.
    front_fields = [
        "appid",
        "name",
        "priority_tier",
        "bucket",
        "direct_fit_score",
        "audience_fit_score",
        "mechanic_fit_score",
        "commercial_benchmark_score",
        "confidence",
        "strategic_note",
        "reason",
        "use_for",
        "do_not_use_for",
        "disagreement_flag",
        "monetization_flag",
    ]

    existing_fields = list(enhanced[0].keys())
    ordered_fields = front_fields + [f for f in existing_fields if f not in front_fields]

    reordered = []

    for row in enhanced:
        reordered.append({field: row.get(field, "") for field in ordered_fields})

    write_csv(Path(args.output), reordered)

    print(f"Saved enhanced CSV: {args.output}")


if __name__ == "__main__":
    main()