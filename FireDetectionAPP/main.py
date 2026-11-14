import csv
import io

import httpx
from flask import Flask, jsonify, render_template

app = Flask(__name__)

FIRMS_BASE_URL = (
    "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
)

AVAILABILITY_BASE_URL = (
    "https://firms.modaps.eosdis.nasa.gov/"
    "api/data_availability/csv"
)

# Paste your FIRMS key here.
MAP_KEY = "705189bcb7c90b9bfaf328ea9d69c937"

SOURCE = "VIIRS_SNPP_NRT"
AREA = "world"
DAY_RANGE = 1
DATE = "2026-11-02"


def parse_csv(response_text):
    text = response_text.strip()

    if not text:
        return []

    if text.lower().startswith("invalid"):
        raise RuntimeError(text)

    return list(csv.DictReader(io.StringIO(text)))


def fetch_firms_data():
    # DATE must be at the end of the Area API URL.
    url = (
        f"{FIRMS_BASE_URL}/{MAP_KEY}/"
        f"{SOURCE}/{AREA}/{DAY_RANGE}/{DATE}"
    )

    response = httpx.get(url, timeout=60.0)

    if response.status_code != 200:
        raise RuntimeError(
            f"FIRMS returned HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    rows = parse_csv(response.text)

    # Safe logging that does not expose MAP_KEY.
    print("Source:", SOURCE)
    print("Area:", AREA)
    print("Date:", DATE)
    print("Day range:", DAY_RANGE)
    print("HTTP status:", response.status_code)
    print("Parsed rows:", len(rows))

    if rows:
        print("Columns:", list(rows[0].keys()))
        print("First row:", rows[0])

    return rows


def fetch_available_dates():
    url = (
        f"{AVAILABILITY_BASE_URL}/"
        f"{MAP_KEY}/{SOURCE}"
    )

    response = httpx.get(url, timeout=30.0)

    if response.status_code != 200:
        raise RuntimeError(
            f"Availability API returned HTTP "
            f"{response.status_code}: "
            f"{response.text[:300]}"
        )

    return parse_csv(response.text)


@app.route("/")
def homepage():
    try:
        fire_data = fetch_firms_data()
        error = None
    except Exception as exc:
        fire_data = []
        error = str(exc)

    return render_template(
        "page1.html",
        title="FireHarmonics",
        fire_data=fire_data[:100],
        total_detections=len(fire_data),
        source=SOURCE,
        selected_date=DATE,
        error=error,
    )


@app.route("/test")
def test_api():
    try:
        fire_data = fetch_firms_data()

        return jsonify(
            {
                "source": SOURCE,
                "area": AREA,
                "date": DATE,
                "count": len(fire_data),
                "first_five_rows": fire_data[:5],
            }
        )

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/availability")
def availability():
    try:
        rows = fetch_available_dates()

        return jsonify(
            {
                "source": SOURCE,
                "count": len(rows),
                "rows": rows,
            }
        )

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(debug=True)