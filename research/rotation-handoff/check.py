import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
CATALOG = "/api/rotations/assessment/catalog"
ASSESSMENT = "/api/rotations/assessment"


def read_json(url: str, body: bytes | None = None) -> dict:
    headers = {"Content-Type": "application/json"} if body is not None else {}
    with urlopen(Request(url, data=body, headers=headers), timeout=10) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check the bundled Marsden example over HTTP."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8081")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    catalog = read_json(base_url + CATALOG)
    payload = (ROOT / "examples/request.json").read_bytes()
    request = json.loads(payload)
    if request["field_id"] not in {field["id"] for field in catalog["fields"]}:
        raise SystemExit("Catalog check failed: unexpected field.")
    if [plan["id"] for plan in catalog["rotations"]] != request["rotation_ids"]:
        raise SystemExit("Catalog check failed: unexpected rotations.")
    actual = read_json(base_url + ASSESSMENT, payload)
    expected = json.loads((ROOT / "examples/response.json").read_text(encoding="utf-8"))
    if actual != expected:
        raise SystemExit(
            "Assessment check failed: response differs from examples/response.json."
        )

    print("OK: catalog and three scenarios match the bundled example.")
    print(f"Recent two-season windows: {actual['projection']['window_count']}")
    for scenario in actual["results"]:
        for plan in scenario["rotations"]:
            condition = scenario["scenario"]
            print(
                condition["id"],
                condition["analog_year"],
                plan["rotation"]["id"],
                f"water shortfall {plan['water_shortfall']['value_pct']}%",
            )


if __name__ == "__main__":
    main()
