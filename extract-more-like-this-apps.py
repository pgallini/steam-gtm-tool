import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    signals = json.loads(input_path.read_text(encoding="utf-8"))

    appids = signals.get("more_like_this_appids") or []

    apps = [
        {
            "appid": appid,
            "name": None,
            "source": "more_like_this",
            "source_appid": signals.get("appid"),
        }
        for appid in appids
    ]

    output_path.write_text(json.dumps(apps, indent=2), encoding="utf-8")

    print(f"Read {len(appids)} More Like This appids from {input_path}")
    print(f"Saved app list to {output_path}")


if __name__ == "__main__":
    main()