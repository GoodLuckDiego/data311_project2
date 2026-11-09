import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import io
import json
import time
import zipfile
from pathlib import Path
import requests
from IPython.display import display
from math import radians, sin, cos, sqrt, atan2
import pydeck as pdk
import polyline
from pathlib import Path

# WORKSHOPING VALHALLA TRANSIT TIMES

# Title
st.title("Campus Living Decision Helper")

VALHALLA_URL = "https://valhalla1.openstreetmap.de"


def valhalla_route(origin, destination, costing="pedestrian"):
    """Request a route between two latitude/longitude locations."""
    payload = {
        "locations": [origin, destination],
        "costing": costing,
        "directions_options": {"units": "miles"},
    }

    response = requests.post(
        f"{VALHALLA_URL}/route",
        headers={
            "X-Client-Id": "data311-calpolyhumboldt-classroom",
        },
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def summarize_route(result):
    """Return distance and estimated travel time."""
    summary = result["trip"]["summary"]

    return {
        "distance_miles": summary["length"],
        "time_minutes": summary["time"] / 60,
    }

def geocode_address(address):
    """Convert a U.S. street address into coordinates."""
    response = requests.get(
        "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
        params={
            "address": address,
            "benchmark": "Public_AR_Current",
            "format": "json",
        },
        timeout=30,
    )
    response.raise_for_status()

    matches = response.json()["result"]["addressMatches"]

    if not matches:
        raise ValueError(
            f"Could not find: {address}. "
            "Include the street number, street name, city, and state."
        )

    if len(matches) > 1:
        raise ValueError(
            f"More than one match found for: {address}. "
            "Add a ZIP code or a more specific address."
        )

    match = matches[0]

    return {
        "lat": match["coordinates"]["y"],
        "lon": match["coordinates"]["x"],
    }, match["matchedAddress"]

def straight_line_miles(origin, destination):
    """Calculate approximate straight-line distance."""
    lat1 = radians(origin["lat"])
    lat2 = radians(destination["lat"])
    delta_lat = lat2 - lat1
    delta_lon = radians(destination["lon"] - origin["lon"])

    a = (
        sin(delta_lat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    )

    return 3958.8 * 2 * atan2(
        sqrt(max(0, a)),
        sqrt(max(0, 1 - a)),
    )

def find_saved_places(lat, lon, radius_miles, category):
    """Find nearby destinations from our CSV directory."""
    csv_path = (
        Path(__file__).resolve().parent
        / "data"
        / "raw"
        / "destinations.csv"
    )

    if not csv_path.is_file():
        raise ValueError(
            "Missing destinations.csv. "
            "Place it inside the project's data folder."
        )

    destinations = pd.read_csv(csv_path)

    matching_places = destinations[
        destinations["category"] == category
    ]

    origin = {"lat": lat, "lon": lon}
    places = []

    for place in matching_places.to_dict(orient="records"):
        distance = straight_line_miles(origin, place)

        if distance <= radius_miles:
            place["straight_line_miles"] = distance
            places.append(place)

    return sorted(
        places,
        key=lambda place: place["straight_line_miles"],
    )


def find_supermarkets(lat, lon, radius_miles):
    return find_saved_places(
        lat, lon, radius_miles, "supermarket"
    )[:10]


def find_department_stores(lat, lon, radius_miles):
    return find_saved_places(
        lat, lon, radius_miles, "department_store"
    )

def find_gas_stations(lat, lon, radius_miles):
    return find_saved_places(
        lat, lon, radius_miles, "gas_station"
    )

def show_route_map(result, origin, destination, destination_name):
                    """Display Valhalla's route and the requested endpoints."""
                    paths = []

                    for leg in result["trip"]["legs"]:
                        # Decoder returns (latitude, longitude).
                        coordinates = polyline.decode(leg["shape"], precision=6)

                        # Pydeck expects [longitude, latitude].
                        path = [[lon, lat] for lat, lon in coordinates]

                        if path:
                            paths.append({"path": path, "label": "Calculated route"})

                    if not paths:
                        st.warning("Travel time was calculated, but no route shape was returned.")
                        return

                    markers = [
                        {
                            "position": [origin["lon"], origin["lat"]],
                            "label": "Starting address",
                            "color": [0, 150, 80],
                        },
                        {
                            "position": [destination["lon"], destination["lat"]],
                            "label": destination_name,
                            "color": [220, 50, 50],
                        },
                    ]

                    route_layer = pdk.Layer(
                        "PathLayer",
                        data=paths,
                        get_path="path",
                        get_color=[30, 110, 230],
                        get_width=4,
                        width_min_pixels=4,
                        width_max_pixels=4,
                        pickable=True,
                    )

                    marker_layer = pdk.Layer(
                        "ScatterplotLayer",
                        data=markers,
                        get_position="position",
                        get_fill_color="color",
                        get_radius=8,
                        radius_min_pixels=6,
                        radius_max_pixels=8,
                        pickable=True,
                    )

                    # Fit the map to the route and both address markers.
                    points = [
                        point
                        for route in paths
                        for point in route["path"]
                    ]
                    points.extend(marker["position"] for marker in markers)

                    # Use a fixed close-up view if every point is identical.
                    if len({tuple(point) for point in points}) == 1:
                        view = pdk.ViewState(
                            longitude=points[0][0],
                            latitude=points[0][1],
                            zoom=15,
                        )
                    else:
                        view = pdk.data_utils.compute_view(points)
                        view.zoom = min(view.zoom - 0.5, 16)

                    st.pydeck_chart(
                        pdk.Deck(
                            layers=[route_layer, marker_layer],
                            initial_view_state=view,
                            map_provider="carto",
                            map_style="light",
                            tooltip={"text": "{label}"},
                        ),
                        height=450,
                    )

                    st.caption(
                        "Blue: calculated route · Green: starting address · "
                        "Red: destination. Hover over a marker for its name. "
                        "Address pins may be slightly away from the routed street or path."
                    )

st.title("Nearby Places and Travel Times")
st.sidebar.markdown("## Input your home/ permanet address")

with st.sidebar.form("places_search"):
    origin_address = st.text_input(
        "Home or potential home address",
        placeholder="Street number, street name, city, state, ZIP",
    )

    category = st.selectbox(
        "Where would you like to go?",
        ["Campus", "Supermarkets", "Department stores", "Gas stations",],
    )

    radius = st.selectbox(
        "Search radius",
        [1, 3, 5, 10, 15, 25],
        index=2,
        format_func=lambda miles: f"{miles} miles",
    )

    search_submitted = st.form_submit_button("Find destinations")

if search_submitted:
    st.session_state.pop("places_search_result", None)

    if not origin_address.strip():
        st.warning("Please enter your starting address.")
    else:
        try:
            with st.spinner("Finding your location and destinations..."):
                origin, matched_origin = geocode_address(
                    origin_address.strip()
                )

                if category == "Campus":
                    campus, matched_campus = geocode_address(
                        "1 Harpst Street, Arcata, CA 95521"
                    )

                    places = [{
                        "id": "campus",
                        "name": "Cal Poly Humboldt",
                        **campus,
                    }]

                elif category == "Supermarkets":
                    places = find_supermarkets(
                        origin["lat"],
                        origin["lon"],
                        radius,
                    )

                elif category in ["Department stores", "Shopping"]:
                    places = find_department_stores(
                        origin["lat"],
                        origin["lon"],
                        radius,
                    )

                elif category == "Gas stations":
                    places = find_gas_stations(
                        origin["lat"],
                        origin["lon"],
                        radius,
                    )

                else:
                    raise ValueError(
                        f"Unknown destination category: {category}"
                    )

                st.session_state["places_search_result"] = {
                    "origin": origin,
                    "matched_origin": matched_origin,
                    "places": places,
                    "category": category,
                }

                st.session_state.pop("selected_place_index", None)

        except requests.RequestException as error:
            st.error(f"The location search failed: {error}")

        except (KeyError, TypeError):
            st.error("The location service returned unexpected data.")

        except ValueError as error:
            st.error(str(error))

# Session state keeps results available when widgets rerun the app.
search = st.session_state.get("places_search_result")

if search:
    st.write(f"**Starting from:** {search['matched_origin']}")
    places = search["places"]

    if not places:
        st.info(
            "No destinations in our saved directory are within this radius. "
            "Try a larger search radius."
            )
    else:
        def destination_label(index):
            place = places[index]

            if search["category"] == "Campus":
                return place["name"]

            distance = place["straight_line_miles"]
            return (
                f"{place['name']} — "
                f"{distance:.2f} miles straight-line"
            )

        selected_index = st.selectbox(
            "Choose a destination",
            options=list(range(len(places))),
            format_func=destination_label,
            key="selected_place_index",
        )

        travel_mode = st.selectbox(
            "Travel mode",
            ["Walking", "Biking", "Driving"],
        )

        st.caption(
                "Results from our saved Humboldt grocery directory, "
                "ordered by straight-line distance. "
                "This directory does not include every store. "
                "Route distance and travel time may differ."
            )

        if st.button("Calculate travel time"):
            selected_place = places[selected_index]

            destination = {
                "lat": selected_place["lat"],
                "lon": selected_place["lon"],
            }

            costing_options = {
                "Walking": "pedestrian",
                "Biking": "bicycle",
                "Driving": "auto",
            }

            try:
                with st.spinner("Calculating your route..."):
                    result = valhalla_route(
                        search["origin"],
                        destination,
                        costing=costing_options[travel_mode],
                    )
                    summary = summarize_route(result)

                st.write(f"**Destination:** {selected_place['name']}")

                st.metric(
                    f"Estimated {travel_mode.lower()} time",
                    f"{summary['time_minutes']:.1f} minutes",
                )

                st.metric(
                    "Route distance",
                    f"{summary['distance_miles']:.2f} miles",
                )

                st.subheader("Your route")

                show_route_map(
                    result,
                    search["origin"],
                    destination,
                    selected_place["name"],
                )

            except requests.RequestException as error:
                st.error(f"The routing request failed: {error}")

            except (KeyError, TypeError, ValueError):
                st.error("The service did not return a usable route.")

st.page_link('https://www.kbb.com/new-cars/total-cost-of-ownership/', label='Car Ownership Calculator', icon="🚗")

st.page_link('https://gsp.humboldt.edu/websites/ComuterCostCalculator/', label='Comuter Cost Calculator', icon="💰")

# Question
st.write("## Do you plan to live on campus?")

# Create radio buttons for yes/no response
response = st.radio(
    "Select your answer:",
    ["Yes", "No"],
    index=None,
    horizontal=True
)

# Simulated commuting times data (in minutes)
np.random.seed(42)
commuting_times = np.random.normal(25, 10, 100)  # Mean of 25 minutes, std dev of 10
commuting_times = np.clip(commuting_times, 5, 60)  # Clip between 5 and 60 minutes

# Process response
if response == "Yes":
    st.success("Good! You don't need to bring a car")
elif response == "No":
    st.write("### Commuting Times Analysis")
    
    # Calculate average
    avg_commute = np.mean(commuting_times)
    
    # Create a plot
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.hist(commuting_times, bins=20, edgecolor='black', alpha=0.7, color='steelblue')
    ax.axvline(avg_commute, color='red', linestyle='--', linewidth=2, label=f'Average: {avg_commute:.1f} minutes')
    ax.set_xlabel('Commuting Time (minutes)', fontsize=12)
    ax.set_ylabel('Frequency', fontsize=12)
    ax.set_title('Distribution of Simulated Commuting Times', fontsize=14, fontweight='bold')
    ax.legend()
    ax.grid(alpha=0.3)
    plt.tight_layout()
    
    # Display the plot
    st.pyplot(fig)
    
    # Display the average
    st.write(f"**Average commuting time:** {avg_commute:.1f} minutes")
else:
    st.info("Please select an answer above.")
