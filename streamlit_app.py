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


@st.cache_data(ttl=3600, show_spinner=False)
def find_supermarkets(lat, lon, radius_miles):
    """Find mapped supermarkets within the search radius."""
    radius_meters = int(radius_miles * 1609.344)

    query = f"""
    [out:json][timeout:25];
    nwr["shop"="supermarket"]
       (around:{radius_meters},{lat},{lon});
    out center;
    """

    response = requests.post(
        "https://overpass.private.coffee/api/interpreter",
        data={"data": query},
        timeout=40,
    )
    response.raise_for_status()
    data = response.json()

    if data.get("remark"):
        raise ValueError(
            "The places search could not finish. Please try again."
        )

    origin = {"lat": lat, "lon": lon}
    places = []

    for element in data["elements"]:
        # Nodes have coordinates directly; ways/relations have a center.
        coordinates = element.get("center", element)

        if "lat" not in coordinates or "lon" not in coordinates:
            continue

        place = {
            "id": f"{element['type']}/{element['id']}",
            "name": element.get("tags", {}).get(
                "name", "Unnamed supermarket"
            ),
            "lat": coordinates["lat"],
            "lon": coordinates["lon"],
        }
        place["straight_line_miles"] = straight_line_miles(
            origin, place
        )
        places.append(place)

    return sorted(
        places,
        key=lambda place: place["straight_line_miles"],
    )[:10]


st.title("Nearby Places and Travel Times")
st.sidebar.markdown("## Input your home/ permanet address")

with st.sidebar.form("places_search"):
    origin_address = st.text_input(
        "Home or potential home address",
        placeholder="Street number, street name, city, state, ZIP",
    )

    category = st.selectbox(
        "Where would you like to go?",
        ["Campus", "Nearby supermarkets"],
    )

    radius = st.selectbox(
        "Supermarket search radius",
        [1, 3, 5, 10],
        index=2,
        format_func=lambda miles: f"{miles} miles",
    )

    search_submitted = st.form_submit_button("Find destinations")

if search_submitted:
    # Clear the previous search so failed searches cannot show old results.
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
                else:
                    places = find_supermarkets(
                        origin["lat"],
                        origin["lon"],
                        radius,
                    )

                st.session_state["places_search_result"] = {
                    "origin": origin,
                    "matched_origin": matched_origin,
                    "places": places,
                    "category": category,
                }

                # Reset the destination selection for the new search.
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
            "No mapped supermarkets were found. "
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

        if search["category"] == "Nearby supermarkets":
            st.caption(
                "Up to 10 results, ordered by straight-line distance. "
                "The closest by road or travel time may differ. "
                "Place data: © OpenStreetMap contributors."
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
