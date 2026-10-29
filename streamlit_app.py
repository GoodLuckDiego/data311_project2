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


# WORKSHOPING VALHALLA TRANSIT TIMES

import streamlit as st
import requests

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


st.title("Nearby Places and Travel Times")

# Test coordinates; replace these with address lookup results later.
origin = {"lon": -122.4194, "lat": 37.7749}
destination = {"lon": -122.4094, "lat": 37.7849}

travel_mode = st.selectbox(
    "Travel mode",
    ["Walking", "Biking", "Driving"],
)

costing_options = {
    "Walking": "pedestrian",
    "Biking": "bicycle",
    "Driving": "auto",
}

if st.button("Calculate travel time"):
    try:
        with st.spinner("Finding a route..."):
            result = valhalla_route(
                origin,
                destination,
                costing=costing_options[travel_mode],
            )
            summary = summarize_route(result)

        st.metric(
            "Estimated travel time",
            f"{summary['time_minutes']:.1f} minutes",
        )
        st.metric(
            "Route distance",
            f"{summary['distance_miles']:.2f} miles",
        )

    except requests.RequestException as error:
        st.error(f"The routing request failed: {error}")

    except (KeyError, TypeError, ValueError):
        st.error("The routing service did not return a usable route.")

# Set page configuration
st.set_page_config(page_title="Campus Living Decision", layout="centered")

# Title
st.title("Campus Living Decision Helper")

st.page_link('https://www.kbb.com/new-cars/total-cost-of-ownership/', label='Car Ownership Calculator', icon="🚗")

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
