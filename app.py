import json
import math
import os
import re
import time
from datetime import datetime, timedelta

import pandas as pd
import requests
import streamlit as st


st.set_page_config(page_title="MRB 75/55 Comp Finder", layout="wide")


# =============================
# API KEYS - DEPLOY SAFE
# =============================

AIRBTICS_API_KEY = st.secrets.get(
    "AIRBTICS_API_KEY",
    os.getenv("AIRBTICS_API_KEY", "")
)

BRIGHTDATA_API_KEY = st.secrets.get(
    "BRIGHTDATA_API_KEY",
    os.getenv("BRIGHTDATA_API_KEY", "")
)

AIRBTICS_LISTINGS_ENDPOINT = "https://crap0y5bx5.execute-api.us-east-2.amazonaws.com/prod/listings/search/bounds"

BRIGHTDATA_DATASET_ID = "gd_ld7ll037kqy322v05"
BRIGHTDATA_SCRAPE_ENDPOINT = "https://api.brightdata.com/datasets/v3/scrape"
BRIGHTDATA_SNAPSHOT_ENDPOINT = "https://api.brightdata.com/datasets/v3/snapshot"


# =============================
# HELPERS
# =============================

def extract_address_from_zillow_url(url):
    if not url:
        return ""

    match = re.search(r"/homedetails/([^/]+)", url)

    if not match:
        return ""

    address = match.group(1)
    address = address.split("_zpid")[0]
    address = address.replace("-", " ")

    return address.strip()


def geocode_address(address):
    cleaned_versions = [
        address,
        re.sub(r"\bUNIT\b.*", "", address, flags=re.IGNORECASE),
        re.sub(r"\bAPT\b.*", "", address, flags=re.IGNORECASE),
        re.sub(r"#.*", "", address),
        address.replace(" St ", " Street "),
        address.replace(" Ave ", " Avenue "),
    ]

    for query in cleaned_versions:
        query = query.strip().strip(",")

        if not query:
            continue

        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": "MRB-7555-Comp-Finder"},
            timeout=30,
        )

        response.raise_for_status()
        data = response.json()

        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])

    return None, None


def make_bounds(lat, lng, radius_miles):
    lat_change = radius_miles / 69
    lng_change = radius_miles / (69 * math.cos(math.radians(lat)))

    return {
        "ne_lat": lat + lat_change,
        "ne_lng": lng + lng_change,
        "sw_lat": lat - lat_change,
        "sw_lng": lng - lng_change,
    }


def safe_parse(response):
    try:
        return response.json()
    except Exception:
        rows = []

        for line in response.text.splitlines():
            line = line.strip()

            if not line:
                continue

            try:
                rows.append(json.loads(line))
            except:
                pass

        return rows


def normalize_airbtics_response(data):
    try:
        if isinstance(data, dict):
            message = data.get("message")

            if isinstance(message, dict):
                listings = message.get("listings")

                if isinstance(listings, str):
                    parsed = json.loads(listings)

                    if isinstance(parsed, dict):
                        inner = parsed.get("message")

                        if isinstance(inner, list):
                            return inner

                    if isinstance(parsed, list):
                        return parsed

                if isinstance(listings, list):
                    return listings

            for key in ["listings", "results", "data", "message"]:
                if isinstance(data.get(key), list):
                    return data[key]

        if isinstance(data, list):
            return data

        return []

    except Exception as e:
        st.error(f"Airbtics parse error: {e}")
        return []


def get_listing_id(comp):
    return (
        comp.get("listingID")
        or comp.get("listing_id")
        or comp.get("listingId")
        or comp.get("id")
        or comp.get("property_id")
    )


def get_sleeps(comp):
    try:
        return int(comp.get("accommodates") or comp.get("sleeps") or comp.get("guests"))
    except:
        return None


def get_rating(comp):
    raw = (
        comp.get("reveiw_scores_rating")
        or comp.get("review_scores_rating")
        or comp.get("rating")
    )

    try:
        raw = float(raw)

        if raw > 5:
            return round(raw / 20, 2)

        return raw

    except:
        return None


def get_adr(comp):
    return comp.get("avg_booked_daily_rate_ltm") or comp.get("adr")


def get_occupancy(comp):
    return comp.get("avg_occupancy_rate_ltm") or comp.get("occupancy")


def get_revenue(comp):
    return comp.get("annual_revenue_ltm") or comp.get("revenue") or comp.get("revenue_potential")


def get_reviews(comp):
    return comp.get("visible_review_count") or comp.get("reviews")


def call_airbtics(bounds, bedrooms, bathrooms, min_sleeps, max_sleeps, min_reviews, min_adr, max_adr, min_revenue, min_rating):
    headers = {
        "x-api-key": AIRBTICS_API_KEY.strip(),
        "Content-Type": "application/json",
    }

    payload = {
        "bounds": bounds,
        "page": 1,
        "filters": {
            "property_type": ["entire_home", "private_room"],
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "sleeps": {
                "min": int(min_sleeps),
                "max": int(max_sleeps),
            },
            "minstay": {
                "min": 1,
            },
            "rating": {
                "min": float(min_rating),
            },
            "reviews": min_reviews,
            "adr": {
                "min": int(min_adr),
                "max": int(max_adr),
            },
            "revenue": {
                "min": int(min_revenue),
            },
        },
    }

    return requests.post(
        AIRBTICS_LISTINGS_ENDPOINT,
        headers=headers,
        json=payload,
        timeout=45,
    )


def call_brightdata(urls):
    headers = {
        "Authorization": f"Bearer {BRIGHTDATA_API_KEY.strip()}",
        "Content-Type": "application/json",
    }

    payload = {
        "input": [
            {
                "url": url,
                "country": "",
            }
            for url in urls
        ]
    }

    return requests.post(
        f"{BRIGHTDATA_SCRAPE_ENDPOINT}?dataset_id={BRIGHTDATA_DATASET_ID}&notify=false&include_errors=true",
        headers=headers,
        json=payload,
        timeout=120,
    )


def download_snapshot(snapshot_id):
    headers = {
        "Authorization": f"Bearer {BRIGHTDATA_API_KEY.strip()}",
    }

    response = requests.get(
        f"{BRIGHTDATA_SNAPSHOT_ENDPOINT}/{snapshot_id}?format=json",
        headers=headers,
        timeout=120,
    )

    response.raise_for_status()
    return safe_parse(response)


def normalize_brightdata(data):
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ["data", "results", "message"]:
            if isinstance(data.get(key), list):
                return data[key]

    return []


def bright_reviews_count(listing):
    reviews = listing.get("reviews", [])

    if isinstance(reviews, list):
        return len(reviews)

    try:
        return int(listing.get("property_number_of_reviews", 0))
    except:
        return 0


def calculate_7555(available_dates, first_threshold, second_threshold, second_slack_days):
    today = datetime.today().date()
    available_set = set(available_dates)

    first_available = 0

    for i in range(0, 30):
        d = (today + timedelta(days=i)).strftime("%Y-%m-%d")

        if d in available_set:
            first_available += 1

    first_booked = 30 - first_available
    first_occ = round((first_booked / 30) * 100, 1)

    second_windows = []

    for offset in range(-second_slack_days, second_slack_days + 1):
        start = 30 + offset
        end = start + 30

        available_count = 0

        for i in range(start, end):
            d = (today + timedelta(days=i)).strftime("%Y-%m-%d")

            if d in available_set:
                available_count += 1

        booked_count = 30 - available_count
        occ = round((booked_count / 30) * 100, 1)

        second_windows.append(
            {
                "window": f"Days {start + 1}-{end}",
                "booked": booked_count,
                "available": available_count,
                "occupancy": occ,
            }
        )

    best_second = max(second_windows, key=lambda x: x["occupancy"])

    passes_first = first_occ >= first_threshold
    passes_second = best_second["occupancy"] >= second_threshold

    return {
        "first_booked": first_booked,
        "first_available": first_available,
        "first_occ": first_occ,
        "second_window": best_second["window"],
        "second_booked": best_second["booked"],
        "second_available": best_second["available"],
        "second_occ": best_second["occupancy"],
        "passes_first": passes_first,
        "passes_second": passes_second,
        "passes_both": passes_first and passes_second,
    }


# =============================
# UI
# =============================

st.title("MRB 75/55 Airbnb Comp Finder")

st.write(
    "Enter a Zillow URL or property address. The app finds nearby Airbnb comps and checks whether each comp meets the MRB 75/55 future-booking rule."
)

st.info(
    "75/55 Rule: Days 1-30 must be booked at least 75%. "
    "Days 31-60 must be booked at least 55%. "
    "The second window can move plus or minus days, default ±3 days."
)


st.header("Target Property")

zillow_url = st.text_input("Zillow URL Optional")

manual_address = st.text_input(
    "Property Address",
    placeholder="5711 Washington St Unit 404, West New York, NJ 07093",
)

detected_address = extract_address_from_zillow_url(zillow_url)

if detected_address and not manual_address:
    st.caption(f"Detected address from Zillow URL: {detected_address}")

target_address = manual_address or detected_address


st.header("Comp Filters")

col1, col2, col3 = st.columns(3)

with col1:
    radius_miles = st.number_input("Comp Radius Miles", 0.5, 25.0, 5.0, 0.5)
    min_sleeps = st.number_input("Minimum Guests", 1, 30, 1)
    max_sleeps = st.number_input("Maximum Guests", 1, 30, 16)

with col2:
    bedrooms = st.multiselect(
        "Bedrooms",
        [0, 1, 2, 3, 4, 5, "6+"],
        default=[0, 1, 2, 3, 4, 5, "6+"],
    )

    bathrooms = st.multiselect(
        "Bathrooms",
        [0, 1, 2, 3, 4, 5, "6+"],
        default=[0, 1, 2, 3, 4, 5, "6+"],
    )

    min_reviews = st.selectbox(
        "Minimum Reviews",
        ["0+", "5+", "10+", "25+", "50+", "100+"],
        index=0,
    )

with col3:
    min_adr = st.number_input("Minimum ADR", 0, 5000, 0, 25)
    max_adr = st.number_input("Maximum ADR", 0, 5000, 1000, 25)
    min_revenue = st.number_input("Minimum Annual Revenue", 0, 500000, 0, 500)
    min_rating = st.number_input("Minimum Rating", 0.0, 5.0, 0.0, 0.1)


st.header("75/55 Rule Settings")

r1, r2, r3 = st.columns(3)

with r1:
    first_threshold = st.number_input("Days 1-30 Required Occupancy %", 0, 100, 75)

with r2:
    second_threshold = st.number_input("Days 31-60 Required Occupancy %", 0, 100, 55)

with r3:
    second_slack_days = st.number_input("Second Window Flexibility +/- Days", 0, 10, 3)


max_to_test = st.number_input(
    "Airbnb Comps To Test With Bright Data",
    min_value=1,
    max_value=50,
    value=50,
    step=1,
)

wait_seconds = st.number_input(
    "Bright Data Wait Time Seconds",
    min_value=10,
    max_value=180,
    value=45,
    step=5,
)

show_debug = st.checkbox("Show Developer Debug Info", value=False)


# =============================
# RUN
# =============================

if st.button("Find 75/55 Comps"):

    if not target_address:
        st.error("Enter a property address or Zillow URL.")
        st.stop()

    if not AIRBTICS_API_KEY:
        st.error("Airbtics API key is missing. Add it in Streamlit Secrets.")
        st.stop()

    if not BRIGHTDATA_API_KEY:
        st.error("Bright Data API key is missing. Add it in Streamlit Secrets.")
        st.stop()

    with st.spinner("Finding property location..."):
        try:
            lat, lng = geocode_address(target_address)
        except Exception as e:
            st.error("Could not geocode property address.")
            st.write(e)
            st.stop()

    if lat is None:
        st.error("Could not find that address. Try adding city, state, and ZIP.")
        st.stop()

    bounds = make_bounds(lat, lng, radius_miles)

    with st.expander("Search Area"):
        st.write("Search Center:", lat, lng)
        st.json(bounds)

    with st.spinner("Searching Airbtics comps..."):
        airbtics_response = call_airbtics(
            bounds,
            bedrooms,
            bathrooms,
            min_sleeps,
            max_sleeps,
            min_reviews,
            min_adr,
            max_adr,
            min_revenue,
            min_rating,
        )

    if airbtics_response.status_code != 200:
        st.error("Airbtics API returned an error.")
        st.text(airbtics_response.text)
        st.stop()

    airbtics_data = safe_parse(airbtics_response)

    if show_debug:
        with st.expander("Developer Debug - Airbtics Raw"):
            st.json(airbtics_data)

    comps = normalize_airbtics_response(airbtics_data)

    if not comps:
        st.error("No comps found after parsing Airbtics response.")
        st.stop()

    comp_rows = []
    airbnb_urls = []

    for comp in comps:
        listing_id = get_listing_id(comp)
        sleeps = get_sleeps(comp)

        if sleeps is not None:
            if sleeps < int(min_sleeps) or sleeps > int(max_sleeps):
                continue

        if not listing_id:
            continue

        airbnb_url = f"https://www.airbnb.com/rooms/{listing_id}"

        airbnb_urls.append(airbnb_url)

        comp_rows.append(
            {
                "Listing ID": listing_id,
                "Name": comp.get("name"),
                "Guests": sleeps,
                "ADR": get_adr(comp),
                "LTM Occupancy": get_occupancy(comp),
                "Annual Revenue": get_revenue(comp),
                "Reviews": get_reviews(comp),
                "Rating": get_rating(comp),
                "Airbnb URL": airbnb_url,
            }
        )

    comp_df = pd.DataFrame(comp_rows)

    if comp_df.empty:
        st.warning("No comps matched your filters.")
        st.stop()

    comp_df["Revenue Sort"] = pd.to_numeric(comp_df["Annual Revenue"], errors="coerce").fillna(0)
    comp_df["Occupancy Sort"] = pd.to_numeric(comp_df["LTM Occupancy"], errors="coerce").fillna(0)

    comp_df = comp_df.sort_values(
        by=["Occupancy Sort", "Revenue Sort"],
        ascending=[False, False],
    ).drop(columns=["Revenue Sort", "Occupancy Sort"])

    airbnb_urls = list(dict.fromkeys(comp_df["Airbnb URL"].dropna().tolist()))[: int(max_to_test)]

    st.header("Airbtics Comp Summary")

    m1, m2, m3, m4 = st.columns(4)

    m1.metric("Comps Found", len(comp_df))
    m2.metric("Calendar Tests", len(airbnb_urls))

    avg_occ = pd.to_numeric(comp_df["LTM Occupancy"], errors="coerce").mean()
    avg_adr = pd.to_numeric(comp_df["ADR"], errors="coerce").mean()

    m3.metric("Average ADR", f"${avg_adr:,.0f}" if pd.notna(avg_adr) else "N/A")
    m4.metric("Average LTM Occupancy", f"{avg_occ:.1f}%" if pd.notna(avg_occ) else "N/A")

    with st.expander("Airbtics Nearby Comps"):
        st.dataframe(comp_df, width="stretch")

    with st.spinner("Submitting comps to Bright Data..."):
        bright_response = call_brightdata(airbnb_urls)

    if bright_response.status_code not in [200, 202]:
        st.error("Bright Data request failed.")
        st.text(bright_response.text)
        st.stop()

    initial_bright = safe_parse(bright_response)

    if show_debug:
        with st.expander("Developer Debug - Bright Data Initial"):
            st.json(initial_bright)

    snapshot_id = None

    if isinstance(initial_bright, dict):
        snapshot_id = initial_bright.get("snapshot_id")

    if snapshot_id:
        st.success(f"Bright Data snapshot created: {snapshot_id}")
        st.info(f"Waiting {wait_seconds} seconds for Bright Data...")
        time.sleep(int(wait_seconds))

        bright_records = normalize_brightdata(download_snapshot(snapshot_id))

    else:
        bright_records = normalize_brightdata(initial_bright)

    if show_debug:
        with st.expander("Developer Debug - Bright Data Records"):
            st.json(bright_records[:3])

    if not bright_records:
        st.warning("Bright Data returned no records yet. Increase wait time and retry.")
        st.stop()

    result_rows = []

    for listing in bright_records:
        available_dates = listing.get("available_dates", [])

        if not available_dates:
            continue

        guests = listing.get("guests", 0)

        try:
            guests = int(guests)
        except:
            guests = 0

        if guests < int(min_sleeps) or guests > int(max_sleeps):
            continue

        rule = calculate_7555(
            available_dates,
            first_threshold,
            second_threshold,
            int(second_slack_days),
        )

        result_rows.append(
            {
                "75/55 Pass": "YES" if rule["passes_both"] else "NO",
                "Listing": listing.get("name", "Unknown"),
                "Guests": guests,
                "Rating": listing.get("ratings"),
                "Reviews": bright_reviews_count(listing),
                "Days 1-30 Booked": rule["first_booked"],
                "Days 1-30 Available": rule["first_available"],
                "Days 1-30 Occ %": rule["first_occ"],
                "Best Second Window": rule["second_window"],
                "Second Window Booked": rule["second_booked"],
                "Second Window Available": rule["second_available"],
                "Second Window Occ %": rule["second_occ"],
                "Location": listing.get("location"),
                "URL": listing.get("url", ""),
            }
        )

    if not result_rows:
        st.warning("No listings had available_dates or matched your filters.")
        st.stop()

    result_df = pd.DataFrame(result_rows)

    result_df = result_df.sort_values(
        by=[
            "75/55 Pass",
            "Days 1-30 Occ %",
            "Second Window Occ %",
            "Rating",
            "Reviews",
        ],
        ascending=[False, False, False, False, False],
    )

    passing_count = len(result_df[result_df["75/55 Pass"] == "YES"])

    st.header("75/55 Comp Results")

    k1, k2, k3, k4 = st.columns(4)

    k1.metric("Listings Analyzed", len(result_df))
    k2.metric("Passing 75/55", passing_count)
    k3.metric("Days 1-30 Rule", f"{first_threshold}%")
    k4.metric("Days 31-60 Rule", f"{second_threshold}% ± {second_slack_days} days")

    if passing_count > 0:
        st.success(f"{passing_count} listing(s) passed the MRB 75/55 rule.")
    else:
        st.warning("No listings passed the MRB 75/55 rule.")

    st.dataframe(result_df, width="stretch")

    st.download_button(
        "Download 75/55 Comp Results CSV",
        result_df.to_csv(index=False),
        file_name="mrb_7555_comp_results.csv",
        mime="text/csv",
    )