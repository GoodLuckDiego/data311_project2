"""
DATA 311 — Interactive Commuter Survey Routing Map
==================================================

Combines:
  1. Valhalla OSM network routing: pedestrian, bicycle, auto.
  2. Humboldt Transit Authority GTFS: nearest stop and routes serving it.
  3. OpenTripPlanner (OTP): scheduled transit itineraries for a weekday 08:00 departure.
  4. Folium: Esri Street Map / Imagery, MarkerCluster, destination marker,
     0.5-mile buffer, hover tooltips, and dashed origin-to-destination connectors.

IMPORTANT:
- Valhalla's public demo is rate-limited. Results are cached locally.
- OTP must be running locally/remotely and built with the same current HTA GTFS
  feed plus an appropriate OpenStreetMap PBF extract. This script calls OTP's
  GraphQL endpoint; it does not build or host the OTP server.
- The library coordinates below are retained from the original Folium script:
  40.8196, -124.0617. They are NOT replaced by the approximate campus point
  used in the exploratory Colab notebook.
- "distance_miles" remains a straight-line (Haversine) measurement. Route
  distances are stored separately and are not mislabeled as straight-line.

Install:
    pip install pandas requests folium tqdm

Run:
    python commuter_routing_map.py

Optional environment variable:
    OTP_GRAPHQL_URL=http://localhost:8080/otp/routers/default/index/graphql

Set OTP_GRAPHQL_URL in your Windows environment or edit the value below.
"""

from __future__ import annotations

import html
import json
import math
import os
import time
import zipfile
from pathlib import Path
from typing import Any

import folium
import pandas as pd
import requests
from folium.plugins import MarkerCluster
from tqdm import tqdm


# =============================================================================
# 1. FILE CONFIGURATION — EDIT THESE PATHS IF YOUR FOLDER STRUCTURE CHANGED
# =============================================================================

INPUT_FILE = Path(
    r"C:\Users\MARE\Documents\data311_project2\data\Latitude and Longitude Transit.csv"
)

OUTPUT_DIR = Path(
    r"C:\Users\MARE\OneDrive\Documents\data311_project2\data\processed"
)

OUTPUT_MAP = OUTPUT_DIR / "commuter_survey_interactive_routing_map.html"
OUTPUT_ENRICHED_CSV = OUTPUT_DIR / "commuter_survey_routing_enriched.csv"
OUTPUT_TRANSIT_LEGS_CSV = OUTPUT_DIR / "commuter_survey_transit_legs.csv"

CACHE_DIR = OUTPUT_DIR / "routing_cache"
VALHALLA_CACHE_FILE = CACHE_DIR / "valhalla_routes.json"
OTP_CACHE_FILE = CACHE_DIR / "otp_transit_routes.json"


# =============================================================================
# 2. INPUT COLUMN CONFIGURATION
# =============================================================================

LAT_COL = "latitude"
LON_COL = "longitude"

# These fields are retained in marker tooltips and exported when present.
IDENTIFIER_COLS = [
    "location_id",
    "geocode_location",
    "geocode_location_type",
    "ObjectId",
]


# =============================================================================
# 3. DESTINATION / MAP CONFIGURATION
# =============================================================================

# Original destination coordinates from the user's Folium script.
LIBRARY_LAT = 40.8754
LIBRARY_LON = -124.0785
DESTINATION_NAME = "Cal Poly Humboldt — Library"

# Original half-mile buffer: 0.5 miles = 804.67 meters.
BUFFER_METERS = 804.67

MAP_ZOOM_START = 11
MAP_CENTER = [40.8665, -124.0828]

# Preserve the selected weekday / 8:00 AM analysis.
# Change this date when analyzing another service day.
TRANSIT_DATE = "2026-11-12"
TRANSIT_TIME = "08:00:00"
TRANSIT_TIME_LABEL = "Weekday, 8:00 AM"

# Humboldt GTFS feed from the uploaded Colab notebook.
HTA_GTFS_URL = "https://hta.optibus.co/public_api/v1/feeds/gtfs"
HTA_GTFS_FALLBACK_URL = (
    "https://data.trilliumtransit.com/gtfs/humboldtcounty-ca-us/"
    "humboldtcounty-ca-us.zip"
)

# Valhalla public demo endpoint used in the Colab notebook.
VALHALLA_URL = "https://valhalla1.openstreetmap.de"

# OTP GraphQL endpoint. OTP is optional at script startup, but required for
# actual scheduled transit journey times. Set OTP_GRAPHQL_URL in Windows.
OTP_GRAPHQL_URL = os.getenv(
    "OTP_GRAPHQL_URL",
    "http://localhost:8080/otp/routers/default/index/graphql",
)

# Public Valhalla fair-use pacing: approximately one request per second.
VALHALLA_REQUEST_DELAY_SECONDS = 1.1
OTP_REQUEST_DELAY_SECONDS = 0.25
REQUEST_TIMEOUT_SECONDS = 60

# The route engine is called once per unique coordinate, not once per row.
COORDINATE_ROUNDING = 6

# Maximum number of itineraries requested from OTP for each origin.
OTP_NUM_ITINERARIES = 3


# =============================================================================
# 4. GENERAL UTILITIES
# =============================================================================

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "DATA311-CalPolyHumboldt-CommuterAccessibility/1.0",
        "X-Client-Id": "data311-calpolyhumboldt-classroom",
    }
)


def section(title: str) -> None:
    print("\n" + "=" * 88)
    print(title)
    print("=" * 88)


def safe_float(value: Any) -> float | None:
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return None
        return result
    except (TypeError, ValueError):
        return None


def haversine_miles(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Calculate straight-line great-circle distance, not road distance."""
    earth_radius_miles = 3958.8

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1)
        * math.cos(phi2)
        * math.sin(delta_lambda / 2) ** 2
    )

    return 2 * earth_radius_miles * math.asin(math.sqrt(a))


def format_number(value: Any, digits: int = 2, suffix: str = "") -> str:
    number = safe_float(value)
    if number is None:
        return "Unavailable"
    return f"{number:,.{digits}f}{suffix}"


def format_minutes(seconds: Any) -> str:
    number = safe_float(seconds)
    if number is None:
        return "Unavailable"
    return f"{number / 60:,.1f} min"


def read_json_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        print(f"WARNING: Cache could not be read; starting a fresh cache: {path}")
        return {}


def write_json_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(cache, file, indent=2)
    temporary_path.replace(path)


def coordinate_key(lat: float, lon: float) -> str:
    return f"{lat:.{COORDINATE_ROUNDING}f},{lon:.{COORDINATE_ROUNDING}f}"


def html_value(value: Any) -> str:
    if pd.isna(value):
        return "Not provided"
    return html.escape(str(value))


# =============================================================================
# 5. INPUT DATA VALIDATION
# =============================================================================

def load_survey_data() -> pd.DataFrame:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Input CSV was not found:\n{INPUT_FILE}\n\n"
            "Update INPUT_FILE in Section 1 to the actual CSV location."
        )

    data = pd.read_csv(INPUT_FILE)

    required = [LAT_COL, LON_COL]
    missing = [column for column in required if column not in data.columns]
    if missing:
        raise KeyError(
            f"Missing coordinate columns: {missing}\n"
            f"Columns found: {data.columns.tolist()}"
        )

    data[LAT_COL] = pd.to_numeric(data[LAT_COL], errors="coerce")
    data[LON_COL] = pd.to_numeric(data[LON_COL], errors="coerce")

    valid = (
        data[LAT_COL].between(-90, 90)
        & data[LON_COL].between(-180, 180)
    )

    invalid_count = int((~valid).sum())
    if invalid_count:
        print(
            f"WARNING: {invalid_count:,} records have missing or invalid "
            "coordinates. They will remain in the enriched CSV but will not "
            "receive map markers or route calculations."
        )

    data["coordinate_valid"] = valid
    return data


# =============================================================================
# 6. DOWNLOAD AND LOAD HTA GTFS
# =============================================================================

def download_gtfs() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    gtfs_dir = CACHE_DIR / "hta_gtfs"
    zip_path = CACHE_DIR / "hta_gtfs.zip"

    if gtfs_dir.exists() and (gtfs_dir / "stops.txt").exists():
        print(f"Using existing GTFS extract: {gtfs_dir}")
        return gtfs_dir

    last_error = None

    for url in [HTA_GTFS_URL, HTA_GTFS_FALLBACK_URL]:
        try:
            print(f"Downloading GTFS: {url}")
            response = SESSION.get(url, timeout=120)
            response.raise_for_status()

            zip_path.write_bytes(response.content)

            gtfs_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path) as archive:
                archive.extractall(gtfs_dir)

            if not (gtfs_dir / "stops.txt").exists():
                raise FileNotFoundError(
                    "The downloaded archive did not contain stops.txt."
                )

            print(f"GTFS extracted to: {gtfs_dir}")
            return gtfs_dir

        except Exception as error:
            last_error = error
            print(f"GTFS download failed: {error}")

    raise RuntimeError(
        "Could not download the HTA GTFS feed from either source."
    ) from last_error


def read_gtfs_table(gtfs_dir: Path, filename: str) -> pd.DataFrame:
    path = gtfs_dir / filename
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype=str, low_memory=False)


def load_gtfs() -> tuple[pd.DataFrame, pd.DataFrame]:
    gtfs_dir = download_gtfs()

    stops = read_gtfs_table(gtfs_dir, "stops.txt")
    routes = read_gtfs_table(gtfs_dir, "routes.txt")
    trips = read_gtfs_table(gtfs_dir, "trips.txt")

    required_stop_columns = {"stop_id", "stop_name", "stop_lat", "stop_lon"}
    if not required_stop_columns.issubset(stops.columns):
        raise ValueError(
            "GTFS stops.txt is missing required columns: "
            f"{required_stop_columns - set(stops.columns)}"
        )

    stops["stop_lat"] = pd.to_numeric(stops["stop_lat"], errors="coerce")
    stops["stop_lon"] = pd.to_numeric(stops["stop_lon"], errors="coerce")
    stops = stops.dropna(subset=["stop_lat", "stop_lon"]).copy()

    # Build route labels served by each stop from trips.txt and routes.txt.
    if (
        not trips.empty
        and not routes.empty
        and {"trip_id", "route_id"}.issubset(trips.columns)
        and {"route_id"}.issubset(routes.columns)
    ):
        route_columns = [
            column
            for column in ["route_id", "route_short_name", "route_long_name"]
            if column in routes.columns
        ]
        trip_routes = trips[["trip_id", "route_id"]].merge(
            routes[route_columns].drop_duplicates("route_id"),
            on="route_id",
            how="left",
        )

        if "route_short_name" in trip_routes.columns:
            short_name = trip_routes["route_short_name"].fillna("").str.strip()
        else:
            short_name = pd.Series("", index=trip_routes.index)

        if "route_long_name" in trip_routes.columns:
            long_name = trip_routes["route_long_name"].fillna("").str.strip()
        else:
            long_name = pd.Series("", index=trip_routes.index)

        trip_routes["route_label"] = (
            short_name.where(short_name.ne(""), trip_routes["route_id"].astype(str))
            + " "
            + long_name
        ).str.strip()

        if "stop_times.txt" in {path.name for path in gtfs_dir.glob("*.txt")}:
            stop_times = read_gtfs_table(gtfs_dir, "stop_times.txt")
            if {"trip_id", "stop_id"}.issubset(stop_times.columns):
                stop_route_map = (
                    stop_times[["trip_id", "stop_id"]]
                    .drop_duplicates()
                    .merge(
                        trip_routes[["trip_id", "route_label"]].drop_duplicates(),
                        on="trip_id",
                        how="left",
                    )
                    .dropna(subset=["route_label"])
                    .groupby("stop_id")["route_label"]
                    .apply(lambda values: sorted(set(values)))
                    .to_dict()
                )
            else:
                stop_route_map = {}
        else:
            stop_route_map = {}

        stops["routes_serving_stop"] = stops["stop_id"].map(
            lambda stop_id: "; ".join(stop_route_map.get(stop_id, []))
        )
    else:
        stops["routes_serving_stop"] = ""

    return stops, routes


# =============================================================================
# 7. NEAREST HTA STOP LOOKUP
# =============================================================================

def nearest_transit_stop(
    origin_lat: float,
    origin_lon: float,
    stops: pd.DataFrame,
) -> dict:
    distances = stops.apply(
        lambda stop: haversine_miles(
            origin_lat,
            origin_lon,
            float(stop["stop_lat"]),
            float(stop["stop_lon"]),
        ),
        axis=1,
    )

    if distances.empty:
        return {
            "nearest_stop_id": None,
            "nearest_stop_name": None,
            "nearest_stop_lat": None,
            "nearest_stop_lon": None,
            "nearest_stop_straight_line_miles": None,
            "nearest_stop_routes": None,
        }

    nearest_index = distances.idxmin()
    nearest = stops.loc[nearest_index]

    return {
        "nearest_stop_id": str(nearest["stop_id"]),
        "nearest_stop_name": str(nearest["stop_name"]),
        "nearest_stop_lat": float(nearest["stop_lat"]),
        "nearest_stop_lon": float(nearest["stop_lon"]),
        "nearest_stop_straight_line_miles": float(distances.loc[nearest_index]),
        "nearest_stop_routes": str(nearest.get("routes_serving_stop", "")),
    }


# =============================================================================
# 8. VALHALLA NETWORK ROUTING
# =============================================================================

def valhalla_route(
    origin_lat: float,
    origin_lon: float,
    destination_lat: float,
    destination_lon: float,
    costing: str,
) -> dict:
    endpoint = f"{VALHALLA_URL.rstrip('/')}/route"

    payload = {
        "locations": [
            {"lat": origin_lat, "lon": origin_lon},
            {"lat": destination_lat, "lon": destination_lon},
        ],
        "costing": costing,
        "directions_options": {"units": "miles"},
    }

    response = SESSION.post(
        endpoint,
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    result = response.json()
    summary = result["trip"]["summary"]

    return {
        "status": "ok",
        "route_miles": summary.get("length"),
        "route_seconds": summary.get("time"),
        "route_minutes": (
            round(float(summary.get("time", 0)) / 60, 2)
            if summary.get("time") is not None
            else None
        ),
    }


def get_valhalla_result(
    origin_lat: float,
    origin_lon: float,
    costing: str,
    cache: dict,
) -> dict:
    key = f"{coordinate_key(origin_lat, origin_lon)}|{costing}|{LIBRARY_LAT}|{LIBRARY_LON}"

    if key in cache:
        return cache[key]

    try:
        result = valhalla_route(
            origin_lat,
            origin_lon,
            LIBRARY_LAT,
            LIBRARY_LON,
            costing,
        )
    except Exception as error:
        result = {
            "status": "error",
            "route_miles": None,
            "route_seconds": None,
            "route_minutes": None,
            "error": str(error)[:300],
        }

    cache[key] = result
    write_json_cache(VALHALLA_CACHE_FILE, cache)
    time.sleep(VALHALLA_REQUEST_DELAY_SECONDS)
    return result


# =============================================================================
# 9. OPENTRIPPLANNER SCHEDULED TRANSIT ROUTING
# =============================================================================

def otp_graphql_query(
    origin_lat: float,
    origin_lon: float,
    destination_lat: float,
    destination_lon: float,
) -> dict:
    """
    Request scheduled public-transport itineraries from OTP 2.x.

    OTP uses coordinates in latitude,longitude order in fromPlace/toPlace.
    The query asks for the best available itineraries for the configured
    weekday and departure time.
    """
    query = f"""
    {{
      plan(
        fromPlace: "{origin_lat},{origin_lon}",
        toPlace: "{destination_lat},{destination_lon}",
        date: "{TRANSIT_DATE}",
        time: "{TRANSIT_TIME}",
        numItineraries: {OTP_NUM_ITINERARIES}
      ) {{
        itineraries {{
          duration
          startTime
          endTime
          walkDistance
          legs {{
            mode
            startTime
            endTime
            duration
            distance
            from {{ name }}
            to {{ name }}
            route {{ shortName longName }}
            trip {{ gtfsId }}
          }}
        }}
      }}
    }}
    """

    response = SESSION.post(
        OTP_GRAPHQL_URL,
        json={"query": query},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    payload = response.json()
    if payload.get("errors"):
        raise RuntimeError(str(payload["errors"])[:500])

    plan = payload.get("data", {}).get("plan")
    if not plan:
        raise RuntimeError("OTP returned no plan object.")

    return plan


def epoch_to_local_time(epoch_ms: Any) -> str | None:
    try:
        timestamp = float(epoch_ms) / 1000
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
    except (TypeError, ValueError, OSError):
        return None


def summarize_otp_itinerary(itinerary: dict) -> dict:
    legs = itinerary.get("legs", []) or []

    transit_legs = [
        leg for leg in legs
        if str(leg.get("mode", "")).upper() not in {"WALK", "BICYCLE", "CAR"}
    ]

    route_names = []
    leg_descriptions = []

    for leg in legs:
        mode = str(leg.get("mode", "Unknown")).upper()
        route = leg.get("route") or {}
        route_name = (
            route.get("shortName")
            or route.get("longName")
            or mode
        )

        if mode not in {"WALK", "BICYCLE", "CAR"}:
            route_names.append(str(route_name))

        from_name = (leg.get("from") or {}).get("name", "")
        to_name = (leg.get("to") or {}).get("name", "")
        leg_descriptions.append(
            f"{mode}: {from_name} to {to_name} ({route_name})"
        )

    duration_seconds = safe_float(itinerary.get("duration"))

    return {
        "transit_total_minutes": (
            round(duration_seconds / 60, 2)
            if duration_seconds is not None
            else None
        ),
        "transit_start_time": epoch_to_local_time(itinerary.get("startTime")),
        "transit_arrival_time": epoch_to_local_time(itinerary.get("endTime")),
        "transit_boardings": len(transit_legs),
        "transit_transfers": max(0, len(transit_legs) - 1),
        "transit_routes": "; ".join(dict.fromkeys(route_names)),
        "transit_itinerary_summary": " | ".join(leg_descriptions),
        "transit_legs": legs,
    }


def get_otp_result(
    origin_lat: float,
    origin_lon: float,
    cache: dict,
) -> dict:
    key = (
        f"{coordinate_key(origin_lat, origin_lon)}|"
        f"{LIBRARY_LAT}|{LIBRARY_LON}|{TRANSIT_DATE}|{TRANSIT_TIME}"
    )

    if key in cache:
        return cache[key]

    try:
        plan = otp_graphql_query(
            origin_lat,
            origin_lon,
            LIBRARY_LAT,
            LIBRARY_LON,
        )
        itineraries = plan.get("itineraries") or []

        if not itineraries:
            result = {
                "transit_status": "no_itinerary",
                "transit_total_minutes": None,
                "transit_start_time": None,
                "transit_arrival_time": None,
                "transit_boardings": None,
                "transit_transfers": None,
                "transit_routes": None,
                "transit_itinerary_summary": "No scheduled itinerary returned.",
                "transit_legs": [],
            }
        else:
            result = summarize_otp_itinerary(itineraries[0])
            result["transit_status"] = "ok"

    except requests.exceptions.ConnectionError:
        result = {
            "transit_status": "otp_unavailable",
            "transit_total_minutes": None,
            "transit_start_time": None,
            "transit_arrival_time": None,
            "transit_boardings": None,
            "transit_transfers": None,
            "transit_routes": None,
            "transit_itinerary_summary": (
                "OTP server unavailable. Configure OTP_GRAPHQL_URL and start OTP."
            ),
            "transit_legs": [],
        }
    except Exception as error:
        result = {
            "transit_status": "error",
            "transit_total_minutes": None,
            "transit_start_time": None,
            "transit_arrival_time": None,
            "transit_boardings": None,
            "transit_transfers": None,
            "transit_routes": None,
            "transit_itinerary_summary": str(error)[:400],
            "transit_legs": [],
        }

    cache[key] = result
    write_json_cache(OTP_CACHE_FILE, cache)
    time.sleep(OTP_REQUEST_DELAY_SECONDS)
    return result


# =============================================================================
# 10. ROUTE ALL UNIQUE ORIGINS AND BUILD ENRICHED DATASET
# =============================================================================

def calculate_route_metrics(
    data: pd.DataFrame,
    stops: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    valhalla_cache = read_json_cache(VALHALLA_CACHE_FILE)
    otp_cache = read_json_cache(OTP_CACHE_FILE)

    valid_data = data.loc[data["coordinate_valid"]].copy()

    unique_origins = (
        valid_data[[LAT_COL, LON_COL]]
        .drop_duplicates()
        .sort_values([LAT_COL, LON_COL])
        .reset_index(drop=True)
    )

    print(f"Survey records: {len(data):,}")
    print(f"Valid coordinate records: {len(valid_data):,}")
    print(f"Unique coordinate pairs to route: {len(unique_origins):,}")
    print(
        "Routing cache entries — Valhalla: "
        f"{len(valhalla_cache):,}; OTP: {len(otp_cache):,}"
    )

    route_lookup: dict[str, dict] = {}
    transit_leg_rows: list[dict] = []

    for row in tqdm(
        unique_origins.itertuples(index=False, name=None),
        total=len(unique_origins),
        desc="Calculating routes for unique origins",
    ):
        origin_lat = float(row[0])
        origin_lon = float(row[1])
        key = coordinate_key(origin_lat, origin_lon)

        result = {
            "straight_line_distance_miles": haversine_miles(
                origin_lat,
                origin_lon,
                LIBRARY_LAT,
                LIBRARY_LON,
            )
        }

        # Network routes from Valhalla.
        for costing, prefix in [
            ("pedestrian", "walk"),
            ("bicycle", "bike"),
            ("auto", "drive"),
        ]:
            route = get_valhalla_result(
                origin_lat,
                origin_lon,
                costing,
                valhalla_cache,
            )

            result[f"{prefix}_route_status"] = route.get("status")
            result[f"{prefix}_route_miles"] = route.get("route_miles")
            result[f"{prefix}_time_minutes"] = route.get("route_minutes")
            result[f"{prefix}_route_error"] = route.get("error")

        # Nearest GTFS stop and route metadata.
        stop_result = nearest_transit_stop(origin_lat, origin_lon, stops)
        result.update(stop_result)

        # Scheduled journey from OTP.
        otp_result = get_otp_result(origin_lat, origin_lon, otp_cache)

        for field, value in otp_result.items():
            if field != "transit_legs":
                result[field] = value

        for leg_number, leg in enumerate(
            otp_result.get("transit_legs", []),
            start=1,
        ):
            route = leg.get("route") or {}
            transit_leg_rows.append(
                {
                    "origin_latitude": origin_lat,
                    "origin_longitude": origin_lon,
                    "itinerary_leg_number": leg_number,
                    "mode": leg.get("mode"),
                    "from_stop_or_place": (leg.get("from") or {}).get("name"),
                    "to_stop_or_place": (leg.get("to") or {}).get("name"),
                    "route_short_name": route.get("shortName"),
                    "route_long_name": route.get("longName"),
                    "leg_duration_seconds": leg.get("duration"),
                    "leg_distance_meters": leg.get("distance"),
                    "leg_start_time": epoch_to_local_time(leg.get("startTime")),
                    "leg_end_time": epoch_to_local_time(leg.get("endTime")),
                }
            )

        route_lookup[key] = result

    # Merge one set of route results back onto every survey row.
    route_rows = []
    for key, values in route_lookup.items():
        row = {"coordinate_key": key}
        row.update(values)
        route_rows.append(row)

    route_metrics = pd.DataFrame(route_rows)

    data = data.copy()
    data["coordinate_key"] = None
    data.loc[data["coordinate_valid"], "coordinate_key"] = data.loc[
        data["coordinate_valid"]
    ].apply(
        lambda row: coordinate_key(row[LAT_COL], row[LON_COL]),
        axis=1,
    )

    enriched = data.merge(
        route_metrics,
        on="coordinate_key",
        how="left",
        validate="many_to_one",
    )

    # Retain the original Haversine concept under an explicit, unambiguous name.
    # The route network distance fields above are independent measurements.
    return enriched, pd.DataFrame(transit_leg_rows)


# =============================================================================
# 11. HTML HOVER TOOLTIP
# =============================================================================

def build_hover_tooltip(row: pd.Series) -> str:
    """Build a compact HTML tooltip for hover interaction."""

    def value(field: str, digits: int = 2, suffix: str = "") -> str:
        return format_number(row.get(field), digits, suffix)

    def text_value(field: str) -> str:
        raw = row.get(field)
        if raw is None or pd.isna(raw) or str(raw).strip() == "":
            return "Unavailable"
        return html.escape(str(raw))

    identity_lines = []
    for column in IDENTIFIER_COLS:
        if column in row.index and pd.notna(row[column]):
            identity_lines.append(
                f"<div><b>{html.escape(column)}:</b> {html_value(row[column])}</div>"
            )

    transit_status = text_value("transit_status")
    transit_minutes = value("transit_total_minutes", 1, " min")

    content = f"""
    <div style="
        width: 340px;
        max-width: 340px;
        font-family: Arial, sans-serif;
        font-size: 12px;
        line-height: 1.45;
        white-space: normal;
    ">
      <div style="
          font-size: 14px;
          font-weight: bold;
          border-bottom: 1px solid #777;
          padding-bottom: 5px;
          margin-bottom: 6px;
      ">
        Commuter Survey Origin
      </div>

      {''.join(identity_lines)}

      <div style="margin-top:6px;">
        <b>Origin:</b> {float(row[LAT_COL]):.6f}, {float(row[LON_COL]):.6f}
      </div>

      <div style="margin-top:7px; font-weight:bold;">Distance to Library</div>
      <div>Straight-line distance: {value('straight_line_distance_miles', 2, ' mi')}</div>

      <div style="margin-top:7px; font-weight:bold;">Valhalla Network Routes</div>
      <div>Walking: {value('walk_route_miles', 2, ' mi')} · {value('walk_time_minutes', 1, ' min')}</div>
      <div>Bicycling: {value('bike_route_miles', 2, ' mi')} · {value('bike_time_minutes', 1, ' min')}</div>
      <div>Driving: {value('drive_route_miles', 2, ' mi')} · {value('drive_time_minutes', 1, ' min')}</div>

      <div style="margin-top:7px; font-weight:bold;">Nearest HTA Transit Stop</div>
      <div>{text_value('nearest_stop_name')}</div>
      <div>Stop distance (straight-line): {value('nearest_stop_straight_line_miles', 2, ' mi')}</div>
      <div>Routes serving stop: {text_value('nearest_stop_routes')}</div>

      <div style="margin-top:7px; font-weight:bold;">
        Scheduled Transit — {html.escape(TRANSIT_DATE)} at {html.escape(TRANSIT_TIME[:5])}
      </div>
      <div>Status: {transit_status}</div>
      <div>Journey duration: {transit_minutes}</div>
      <div>Departure: {text_value('transit_start_time')}</div>
      <div>Arrival: {text_value('transit_arrival_time')}</div>
      <div>Transfers: {text_value('transit_transfers')}</div>
      <div>Routes: {text_value('transit_routes')}</div>
      <div style="margin-top:4px; color:#555;">
        {text_value('transit_itinerary_summary')}
      </div>

      <div style="
          border-top:1px solid #aaa;
          margin-top:7px;
          padding-top:5px;
          color:#555;
          font-size:10px;
      ">
        Hover values are precomputed. Transit reflects the configured GTFS
        service date and departure time, not live vehicle positions.
      </div>
    </div>
    """

    return content


# =============================================================================
# 12. BUILD FOLIUM MAP — PRESERVE ORIGINAL LAYERS / CLUSTER / DESTINATION / BUFFER
# =============================================================================

def build_interactive_map(enriched: pd.DataFrame) -> None:
    map_object = folium.Map(
        location=MAP_CENTER,
        zoom_start=MAP_ZOOM_START,
        tiles=None,
        control_scale=True,
    )

    # Esri basemap layers retained from the original map.
    folium.TileLayer(
        tiles=(
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Street_Map/MapServer/tile/{z}/{y}/{x}"
        ),
        attr="Tiles &copy; Esri",
        name="Esri Street Map",
        overlay=False,
        control=True,
    ).add_to(map_object)

    folium.TileLayer(
        tiles=(
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}"
        ),
        attr="Tiles &copy; Esri",
        name="Esri Imagery",
        overlay=False,
        control=True,
    ).add_to(map_object)

    # Destination marker.
    folium.Marker(
        location=[LIBRARY_LAT, LIBRARY_LON],
        tooltip=DESTINATION_NAME,
        popup=folium.Popup(
            f"<b>{html.escape(DESTINATION_NAME)}</b><br>"
            f"Latitude: {LIBRARY_LAT}<br>Longitude: {LIBRARY_LON}",
            max_width=300,
        ),
        icon=folium.Icon(color="red", icon="book", prefix="fa"),
    ).add_to(map_object)

    # Original 0.5-mile destination buffer.
    folium.Circle(
        location=[LIBRARY_LAT, LIBRARY_LON],
        radius=BUFFER_METERS,
        color="red",
        weight=2,
        fill=True,
        fill_color="red",
        fill_opacity=0.10,
        tooltip="0.5-mile buffer around the library destination",
    ).add_to(map_object)

    marker_cluster = MarkerCluster(
        name="Commuter Survey Origins",
        disableClusteringAtZoom=12,
    ).add_to(map_object)

    valid_rows = enriched.loc[enriched["coordinate_valid"]].copy()

    for _, row in tqdm(
        valid_rows.iterrows(),
        total=len(valid_rows),
        desc="Adding survey markers",
    ):
        latitude = float(row[LAT_COL])
        longitude = float(row[LON_COL])

        tooltip_html = build_hover_tooltip(row)

        # Dashed straight connector retained as a visual reference only.
        # It is NOT a routed street/transit path.
        folium.PolyLine(
            locations=[
                [latitude, longitude],
                [LIBRARY_LAT, LIBRARY_LON],
            ],
            color="#3388ff",
            weight=1,
            opacity=0.35,
            dash_array="5, 7",
        ).add_to(marker_cluster)

        folium.CircleMarker(
            location=[latitude, longitude],
            radius=5,
            color="#174a7e",
            weight=1,
            fill=True,
            fill_color="#2b83ba",
            fill_opacity=0.75,
            tooltip=folium.Tooltip(
                tooltip_html,
                sticky=True,
                parse_html=True,
                style=(
                    "background-color: white; "
                    "border: 1px solid #555; "
                    "border-radius: 4px; "
                    "padding: 8px;"
                ),
            ),
        ).add_to(marker_cluster)

    folium.LayerControl(collapsed=False).add_to(map_object)

    # Fit map to survey locations and destination where possible.
    if not valid_rows.empty:
        bounds = [
            [
                float(valid_rows[LAT_COL].min()),
                float(valid_rows[LON_COL].min()),
            ],
            [
                float(valid_rows[LAT_COL].max()),
                float(valid_rows[LON_COL].max()),
            ],
            [LIBRARY_LAT, LIBRARY_LON],
        ]
        map_object.fit_bounds(bounds, padding=(25, 25))

    OUTPUT_MAP.parent.mkdir(parents=True, exist_ok=True)
    map_object.save(str(OUTPUT_MAP))


# =============================================================================
# 13. EXPORTS AND RUN SUMMARY
# =============================================================================

def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    section("1. LOAD AND VALIDATE COMMUTER SURVEY DATA")
    survey = load_survey_data()
    print(f"Rows: {len(survey):,}")
    print(f"Columns: {len(survey.columns):,}")
    print(f"Valid coordinate rows: {int(survey['coordinate_valid'].sum()):,}")

    section("2. DOWNLOAD / LOAD HUMBOLDT TRANSIT AUTHORITY GTFS")
    stops, routes = load_gtfs()
    print(f"GTFS stops: {len(stops):,}")
    print(f"GTFS routes: {len(routes):,}")

    section("3. CALCULATE WALK / BIKE / DRIVE AND SCHEDULED TRANSIT")
    enriched, transit_legs = calculate_route_metrics(survey, stops)

    section("4. EXPORT ENRICHED SURVEY DATA")
    enriched.to_csv(OUTPUT_ENRICHED_CSV, index=False)
    print(f"Enriched CSV saved:\n{OUTPUT_ENRICHED_CSV}")

    transit_legs.to_csv(OUTPUT_TRANSIT_LEGS_CSV, index=False)
    print(f"Transit leg details saved:\n{OUTPUT_TRANSIT_LEGS_CSV}")

    section("5. BUILD INTERACTIVE FOLIUM MAP")
    build_interactive_map(enriched)
    print(f"Interactive map saved:\n{OUTPUT_MAP}")

    section("6. ROUTING STATUS AUDIT")
    if "walk_route_status" in enriched.columns:
        print("Walking route status:")
        print(enriched["walk_route_status"].value_counts(dropna=False).to_string())

    if "bike_route_status" in enriched.columns:
        print("\nBicycle route status:")
        print(enriched["bike_route_status"].value_counts(dropna=False).to_string())

    if "drive_route_status" in enriched.columns:
        print("\nDriving route status:")
        print(enriched["drive_route_status"].value_counts(dropna=False).to_string())

    if "transit_status" in enriched.columns:
        print("\nScheduled transit status:")
        print(enriched["transit_status"].value_counts(dropna=False).to_string())

    print("\nCompleted.")
    print("Open the HTML map in a browser. Hover over a survey marker to inspect routing.")


if __name__ == "__main__":
    main()
