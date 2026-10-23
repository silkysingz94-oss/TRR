
# ================================================================
# GBoS GEOAI | TRAVEL TIME ROUTING — THE GAMBIA
# Platform: Python + VS Code + Streamlit
#
# Features:
# 1. Vehicle routing using OSRM
# 2. Origin and destination search
# 3. Distance and estimated driving time
# 4. Alternative routes where available
# 5. Local OSM road-network accessibility analysis
# 6. Reachable nodes within 5, 10, 15, 30, 45 and 60 minutes
# 7. Interactive map, basemaps and permanent legend
# 8. Route instructions and analytics
# 9. CSV and GeoJSON exports
#
# Produced by: Ansu Singhateh
# GIS / Cartography Unit | GBoS
# GBoS GEOAI • Research and educational use
# ================================================================

import json
import math
from datetime import datetime
from pathlib import Path

import folium
import networkx as nx
import pandas as pd
import requests
import streamlit as st
from folium.plugins import Fullscreen, MeasureControl, MousePosition
from streamlit_folium import st_folium

try:
    import osmnx as ox
    OSMNX_AVAILABLE = True
except ImportError:
    OSMNX_AVAILABLE = False


# ================================================================
# CONFIGURATION
# ================================================================

st.set_page_config(
    page_title="GBoS GEOAI | Travel Time Routing",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_TITLE = "SILKYSINGZ GEOAI | TRAVEL TIME ROUNTING"

OSRM_URL = "https://router.project-osrm.org"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Approximate bounding box for The Gambia.
# This is NOT an official administrative boundary.
GAMBIA_BBOX = {
    "west": -17.06,
    "south": 13.00,
    "east": -13.78,
    "north": 13.83,
}

GAMBIA_CENTER = [13.45, -16.0]

OUTPUT_DIR = Path.home() / "Gambia_Travel_Routing" / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "GBoS-GEOAI-Travel-Routing/1.0 (research use)"
}


# ================================================================
# SESSION STATE
# ================================================================

DEFAULTS = {
    "origin": None,
    "destination": None,
    "origin_name": "",
    "destination_name": "",
    "routes": None,
    "accessibility_nodes": None,
    "accessibility_summary": None,
    "accessibility_origin": None,
    "route_error": None,
    "access_error": None,
}

for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ================================================================
# HELPER FUNCTIONS
# ================================================================

def within_gambia(lat, lon):
    """Check whether coordinates fall within the approximate study extent."""
    return (
        GAMBIA_BBOX["south"] <= lat <= GAMBIA_BBOX["north"]
        and GAMBIA_BBOX["west"] <= lon <= GAMBIA_BBOX["east"]
    )


def parse_coordinates(value, label):
    """Read coordinates in latitude, longitude order."""
    try:
        parts = [p.strip() for p in value.split(",")]
        if len(parts) != 2:
            raise ValueError

        lat = float(parts[0])
        lon = float(parts[1])

        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError

        return lat, lon

    except Exception:
        raise ValueError(
            f"{label}: enter coordinates as latitude, longitude. "
            "Example: 13.4549, -16.5790"
        )


@st.cache_data(ttl=900, show_spinner=False)
def geocode_place(place):
    """Find one place using OpenStreetMap Nominatim."""
    response = requests.get(
        NOMINATIM_URL,
        params={
            "q": place,
            "format": "jsonv2",
            "limit": 1,
            "countrycodes": "gm",
        },
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    results = response.json()

    if not results:
        raise ValueError(
            f"No location found for '{place}'. Try a more specific place "
            "name or enter coordinates manually."
        )

    result = results[0]
    return (
        float(result["lat"]),
        float(result["lon"]),
        result["display_name"],
    )


@st.cache_data(ttl=300, show_spinner=False)
def calculate_route(origin, destination, alternatives=True):
    """
    Calculate driving routes using the public OSRM demo service.
    Coordinates passed to OSRM must be longitude,latitude.
    """
    olat, olon = origin
    dlat, dlon = destination

    url = (
        f"{OSRM_URL}/route/v1/driving/"
        f"{olon},{olat};{dlon},{dlat}"
    )

    response = requests.get(
        url,
        params={
            "overview": "full",
            "geometries": "geojson",
            "steps": "true",
            "alternatives": "true" if alternatives else "false",
        },
        headers=HEADERS,
        timeout=60,
    )
    response.raise_for_status()
    result = response.json()

    if result.get("code") != "Ok" or not result.get("routes"):
        raise RuntimeError(
            result.get(
                "message",
                "No drivable route was found between these locations.",
            )
        )

    return result


def format_distance(metres):
    if metres >= 1000:
        return f"{metres / 1000:,.2f} km"
    return f"{metres:,.0f} m"


def format_duration(seconds):
    minutes = round(seconds / 60)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f"{hours} h {minutes} min"

    return f"{minutes} min"


def make_legend():
    return """
    <div style="
        position:fixed; bottom:25px; left:20px; z-index:9999;
        background:white; padding:12px; border:1px solid #999;
        border-radius:6px; box-shadow:0 1px 5px #888;
        font-size:12px; line-height:1.8;">
      <b>GBoS GEOAI — Map Legend</b><br>
      <span style="color:#1769aa;">━━</span> Primary driving route<br>
      <span style="color:#ef6c00;">━━</span> Alternative route<br>
      <span style="color:#16803c;">●</span> Origin<br>
      <span style="color:#c62828;">●</span> Destination<br>
      <span style="color:#7b1fa2;">━━</span> Reachable road network<br>
      <small>Approximate national study extent</small>
    </div>
    """


def create_map(
    origin=None,
    destination=None,
    routes=None,
    reachable_segments=None,
):
    """Build an interactive Leaflet map with selectable basemaps."""
    fmap = folium.Map(
        location=GAMBIA_CENTER,
        zoom_start=8,
        tiles=None,
        control_scale=True,
        prefer_canvas=True,
    )

    folium.TileLayer(
        "OpenStreetMap",
        name="Street map",
        control=True,
    ).add_to(fmap)

    folium.TileLayer(
        tiles=(
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}"
        ),
        attr="Tiles © Esri",
        name="Satellite imagery",
        control=True,
    ).add_to(fmap)

    folium.TileLayer(
        tiles="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        attr="© OpenTopoMap contributors",
        name="Terrain",
        control=True,
    ).add_to(fmap)

    folium.Rectangle(
        bounds=[
            [GAMBIA_BBOX["south"], GAMBIA_BBOX["west"]],
            [GAMBIA_BBOX["north"], GAMBIA_BBOX["east"]],
        ],
        color="#777777",
        weight=1,
        dash_array="5, 5",
        fill=False,
        tooltip="Approximate Gambia study extent",
    ).add_to(fmap)

    # Draw accessibility road segments independently.
    if reachable_segments:
        for segment in reachable_segments:
            if len(segment) >= 2:
                folium.PolyLine(
                    segment,
                    color="#7b1fa2",
                    weight=3,
                    opacity=0.65,
                    tooltip="Road segment within accessibility threshold",
                ).add_to(fmap)

    # Draw alternatives first; primary route remains prominent.
    if routes:
        for index, route in reversed(list(enumerate(routes))):
            coordinates = route["geometry"]["coordinates"]
            latlon = [(lat, lon) for lon, lat in coordinates]

            folium.PolyLine(
                latlon,
                color="#1769aa" if index == 0 else "#ef6c00",
                weight=7 if index == 0 else 4,
                opacity=0.9,
                tooltip=(
                    "Primary route" if index == 0
                    else f"Alternative route {index}"
                ),
            ).add_to(fmap)

    if origin:
        folium.Marker(
            origin,
            tooltip="Origin",
            popup=f"Origin: {origin[0]:.5f}, {origin[1]:.5f}",
            icon=folium.Icon(color="green", icon="play"),
        ).add_to(fmap)

    if destination:
        folium.Marker(
            destination,
            tooltip="Destination",
            popup=(
                f"Destination: {destination[0]:.5f}, "
                f"{destination[1]:.5f}"
            ),
            icon=folium.Icon(color="red", icon="flag"),
        ).add_to(fmap)

    if routes:
        coords = routes[0]["geometry"]["coordinates"]
        lats = [p[1] for p in coords]
        lons = [p[0] for p in coords]
        fmap.fit_bounds([
            [min(lats), min(lons)],
            [max(lats), max(lons)],
        ], padding=(30, 30))

    elif origin and destination:
        fmap.fit_bounds([origin, destination], padding=(30, 30))

    elif origin:
        fmap.location = origin
        fmap.zoom_start = 11

    folium.LayerControl(collapsed=False).add_to(fmap)
    Fullscreen(position="topright").add_to(fmap)
    MeasureControl(
        position="topleft",
        primary_length_unit="kilometers",
    ).add_to(fmap)
    MousePosition(
        position="bottomright",
        separator=" | ",
        prefix="Lat/Lon:",
    ).add_to(fmap)

    fmap.get_root().html.add_child(
        folium.Element(make_legend())
    )

    return fmap


# ================================================================
# ACCESSIBILITY ANALYSIS
# ================================================================

@st.cache_data(ttl=1800, show_spinner=False)
def run_accessibility(lat, lon, radius_km, speed_kmh):
    """
    Download a LOCAL OSM drivable road graph and calculate shortest
    network travel times from the origin.

    The assumed average speed is applied to road-edge lengths.
    Results are estimates, not live traffic predictions.
    """
    if not OSMNX_AVAILABLE:
        raise RuntimeError(
            "OSMnx is not installed. Run: python -m pip install osmnx networkx"
        )

    graph = ox.graph_from_point(
        (lat, lon),
        dist=int(radius_km * 1000),
        network_type="drive",
        simplify=True,
        retain_all=False,
    )

    if graph.number_of_nodes() == 0:
        raise RuntimeError("No drivable roads found near the selected origin.")

    speed_mps = max(5, float(speed_kmh)) * 1000 / 3600

    for _, _, _, data in graph.edges(keys=True, data=True):
        length = float(data.get("length", 0))
        data["custom_time"] = length / speed_mps

    origin_node = ox.distance.nearest_nodes(
        graph,
        X=lon,
        Y=lat,
    )

    travel_seconds = nx.single_source_dijkstra_path_length(
        graph,
        origin_node,
        weight="custom_time",
    )

    nodes = []
    for node_id, seconds in travel_seconds.items():
        node = graph.nodes[node_id]

        # Approximate straight-line distance for context only.
        dx = (
            (float(node["x"]) - lon)
            * 111320
            * math.cos(math.radians(lat))
        )
        dy = (float(node["y"]) - lat) * 110540

        nodes.append({
            "node_id": str(node_id),
            "latitude": float(node["y"]),
            "longitude": float(node["x"]),
            "travel_time_min": seconds / 60,
            "approx_distance_km": math.hypot(dx, dy) / 1000,
        })

    node_df = pd.DataFrame(nodes)
    thresholds = [5, 10, 15, 30, 45, 60]

    summary = []
    for threshold in thresholds:
        reached = node_df[node_df["travel_time_min"] <= threshold]

        summary.append({
            "Travel_time_minutes": threshold,
            "Reachable_road_nodes": int(len(reached)),
            "Farthest_node_distance_km": (
                round(float(reached["approx_distance_km"].max()), 2)
                if not reached.empty else 0
            ),
        })

    # Select edges where both endpoints are reachable within 30 minutes.
    # Each segment is kept separate so the map does not join unrelated roads.
    segments = []
    threshold_seconds = 30 * 60

    for u, v, data in graph.edges(data=True):
        if (
            travel_seconds.get(u, float("inf")) <= threshold_seconds
            and travel_seconds.get(v, float("inf")) <= threshold_seconds
        ):
            segments.append([
                (float(graph.nodes[u]["y"]), float(graph.nodes[u]["x"])),
                (float(graph.nodes[v]["y"]), float(graph.nodes[v]["x"])),
            ])

    metadata = {
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "reachable_nodes": len(travel_seconds),
        "radius_km": radius_km,
        "assumed_speed_kmh": speed_kmh,
    }

    return node_df, pd.DataFrame(summary), segments, metadata


# ================================================================
# USER INTERFACE
# ================================================================

st.title("🗺️ GBoS GEOAI | Travel Time Routing")
st.markdown(
    "### The Gambia — Vehicle routing and travel-time accessibility"
)
st.caption(
    "OpenStreetMap road data • OSRM routing • Local network analysis"
)

with st.sidebar:
    st.header("1. Route settings")

    input_type = st.radio(
        "Location input method",
        ["Place names", "Coordinates"],
    )

    if input_type == "Place names":
        origin_input = st.text_input(
            "Origin",
            "Serrekunda, The Gambia",
        )
        destination_input = st.text_input(
            "Destination",
            "Banjul, The Gambia",
        )
    else:
        origin_input = st.text_input(
            "Origin (latitude, longitude)",
            "13.4380, -16.6780",
        )
        destination_input = st.text_input(
            "Destination (latitude, longitude)",
            "13.4549, -16.5790",
        )

    show_alternatives = st.checkbox(
        "Request alternative routes",
        value=True,
    )

    route_button = st.button(
        "Calculate driving route",
        type="primary",
        use_container_width=True,
    )

    st.divider()
    st.header("2. Accessibility settings")

    radius = st.slider(
        "Road network radius (km)",
        5, 40, 15, step=5,
    )

    speed = st.slider(
        "Assumed average driving speed (km/h)",
        10, 80, 30, step=5,
    )

    access_button = st.button(
        "Run accessibility analysis",
        use_container_width=True,
    )

    st.info(
        "Accessibility downloads roads around the origin only. "
        "It does not download the entire national road network."
    )


# ================================================================
# RESOLVE LOCATIONS
# ================================================================

def resolve_locations():
    if input_type == "Place names":
        if not origin_input.strip() or not destination_input.strip():
            raise ValueError("Enter both origin and destination.")

        olat, olon, oname = geocode_place(origin_input.strip())
        dlat, dlon, dname = geocode_place(destination_input.strip())

    else:
        olat, olon = parse_coordinates(origin_input, "Origin")
        dlat, dlon = parse_coordinates(destination_input, "Destination")
        oname = f"{olat:.5f}, {olon:.5f}"
        dname = f"{dlat:.5f}, {dlon:.5f}"

    if not within_gambia(olat, olon):
        raise ValueError(
            "Origin is outside the approximate Gambia study extent. "
            "Check the place name or coordinates."
        )

    if not within_gambia(dlat, dlon):
        raise ValueError(
            "Destination is outside the approximate Gambia study extent. "
            "Check the place name or coordinates."
        )

    return (olat, olon), (dlat, dlon), oname, dname


# ================================================================
# ROUTING ACTION
# ================================================================

if route_button:
    st.session_state["route_error"] = None

    try:
        with st.spinner("Finding locations and calculating the route..."):
            origin, destination, oname, dname = resolve_locations()

            result = calculate_route(
                origin,
                destination,
                show_alternatives,
            )

            st.session_state["origin"] = origin
            st.session_state["destination"] = destination
            st.session_state["origin_name"] = oname
            st.session_state["destination_name"] = dname
            st.session_state["routes"] = result["routes"]

    except Exception as exc:
        st.session_state["route_error"] = str(exc)


# ================================================================
# ACCESSIBILITY ACTION
# ================================================================

if access_button:
    st.session_state["access_error"] = None

    try:
        if input_type == "Place names":
            lat, lon, name = geocode_place(origin_input.strip())
            origin = (lat, lon)
        else:
            lat, lon = parse_coordinates(origin_input, "Origin")
            origin = (lat, lon)
            name = f"{lat:.5f}, {lon:.5f}"

        if not within_gambia(lat, lon):
            raise ValueError(
                "Accessibility origin is outside the approximate "
                "Gambia study extent."
            )

        with st.spinner(
            f"Downloading local roads within {radius} km and calculating "
            "network travel times..."
        ):
            node_df, summary_df, segments, metadata = run_accessibility(
                lat,
                lon,
                radius,
                speed,
            )

        st.session_state["origin"] = origin
        st.session_state["origin_name"] = name
        st.session_state["accessibility_origin"] = origin
        st.session_state["accessibility_nodes"] = node_df
        st.session_state["accessibility_summary"] = summary_df
        st.session_state["accessibility_segments"] = segments
        st.session_state["accessibility_metadata"] = metadata

    except Exception as exc:
        st.session_state["access_error"] = str(exc)


# ================================================================
# SHOW ERRORS
# ================================================================

if st.session_state["route_error"]:
    st.error("Routing error: " + st.session_state["route_error"])

if st.session_state["access_error"]:
    st.error("Accessibility error: " + st.session_state["access_error"])


# ================================================================
# ROUTE KPIs
# ================================================================

routes = st.session_state["routes"]
origin = st.session_state["origin"]
destination = st.session_state["destination"]

if routes:
    primary = routes[0]

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Route distance", format_distance(primary["distance"]))
    k2.metric("Estimated driving time", format_duration(primary["duration"]))
    k3.metric("Alternative routes", max(0, len(routes) - 1))
    k4.metric("Route options", len(routes))

    st.caption(
        f"Origin: {st.session_state['origin_name']}  |  "
        f"Destination: {st.session_state['destination_name']}"
    )
else:
    st.info(
        "Choose an origin and destination in the sidebar and click "
        "**Calculate driving route**."
    )


# ================================================================
# INTERACTIVE MAP
# ================================================================

segments = st.session_state.get("accessibility_segments", [])

fmap = create_map(
    origin=origin,
    destination=destination,
    routes=routes,
    reachable_segments=segments,
)

st_folium(
    fmap,
    height=620,
    use_container_width=True,
    returned_objects=[],
    key="gambia_travel_time_map",
)


# ================================================================
# ANALYTICS
# ================================================================

tab_route, tab_access, tab_export, tab_about = st.tabs([
    "Route analytics",
    "Accessibility",
    "Downloads",
    "Methodology and credits",
])


with tab_route:
    if routes:
        route_rows = []

        for index, route in enumerate(routes):
            route_rows.append({
                "Route": "Primary" if index == 0 else f"Alternative {index}",
                "Distance_km": round(route["distance"] / 1000, 2),
                "Estimated_time_min": round(route["duration"] / 60, 1),
                "Distance": format_distance(route["distance"]),
                "Duration": format_duration(route["duration"]),
            })

        route_df = pd.DataFrame(route_rows)

        st.subheader("Route comparison")
        st.dataframe(
            route_df,
            use_container_width=True,
            hide_index=True,
        )

        st.subheader("Driving instructions")

        instructions = []

        for leg in routes[0].get("legs", []):
            for step in leg.get("steps", []):
                maneuver = step.get("maneuver", {})

                instructions.append({
                    "Road": step.get("name") or "Unnamed road",
                    "Instruction": maneuver.get("type", "Continue"),
                    "Distance_km": round(step.get("distance", 0) / 1000, 3),
                    "Estimated_time_min": round(step.get("duration", 0) / 60, 1),
                })

        if instructions:
            st.dataframe(
                pd.DataFrame(instructions),
                use_container_width=True,
                hide_index=True,
            )

    else:
        st.write("Calculate a route to display route analytics.")


with tab_access:
    node_df = st.session_state.get("accessibility_nodes")
    summary_df = st.session_state.get("accessibility_summary")
    metadata = st.session_state.get("accessibility_metadata")

    if node_df is not None and summary_df is not None:
        st.subheader("Road network statistics")

        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Network nodes", f"{metadata['nodes']:,}")
        a2.metric("Network edges", f"{metadata['edges']:,}")
        a3.metric("Reachable nodes", f"{metadata['reachable_nodes']:,}")
        a4.metric("Network radius", f"{metadata['radius_km']} km")

        st.subheader("Reachability by travel-time threshold")

        st.dataframe(
            summary_df,
            use_container_width=True,
            hide_index=True,
        )

        chart_df = summary_df.set_index("Travel_time_minutes")
        st.bar_chart(chart_df["Reachable_road_nodes"])

        st.subheader("Accessibility node sample")
        st.dataframe(
            node_df.head(1000),
            use_container_width=True,
            hide_index=True,
        )

        st.warning(
            "Accessibility uses a constant average speed over mapped road edges. "
            "These counts represent reachable road-network nodes, not the number "
            "of people or settlements served. The road network is limited to the "
            "selected local radius."
        )

    else:
        st.write(
            "Click **Run accessibility analysis** in the sidebar to calculate "
            "local road-network travel times."
        )


with tab_export:
    st.subheader("Download results")

    if routes:
        features = []

        for index, route in enumerate(routes):
            features.append({
                "type": "Feature",
                "properties": {
                    "route": "primary" if index == 0 else f"alternative_{index}",
                    "distance_m": route["distance"],
                    "distance_km": route["distance"] / 1000,
                    "duration_seconds": route["duration"],
                    "duration_minutes": route["duration"] / 60,
                    "travel_mode": "driving",
                    "routing_service": "OSRM public demo",
                    "created_utc": datetime.utcnow().isoformat() + "Z",
                },
                "geometry": route["geometry"],
            })

        geojson = {
            "type": "FeatureCollection",
            "name": "Gambia_Travel_Time_Routes",
            "features": features,
        }

        st.download_button(
            "Download routes as GeoJSON",
            data=json.dumps(geojson, indent=2),
            file_name="Gambia_Travel_Time_Routes.geojson",
            mime="application/geo+json",
            use_container_width=True,
        )

        primary = routes[0]

        summary = pd.DataFrame([{
            "Origin": st.session_state["origin_name"],
            "Destination": st.session_state["destination_name"],
            "Distance_km": round(primary["distance"] / 1000, 3),
            "Estimated_time_min": round(primary["duration"] / 60, 2),
            "Travel_mode": "Driving",
            "Routing_service": "OSRM public demo",
        }])

        st.download_button(
            "Download route summary as CSV",
            data=summary.to_csv(index=False),
            file_name="Gambia_Travel_Time_Summary.csv",
            mime="text/csv",
            use_container_width=True,
        )

    if node_df is not None:
        st.download_button(
            "Download accessibility nodes as CSV",
            data=node_df.to_csv(index=False),
            file_name="Gambia_Accessibility_Nodes.csv",
            mime="text/csv",
            use_container_width=True,
        )

    if summary_df is not None:
        st.download_button(
            "Download accessibility summary as CSV",
            data=summary_df.to_csv(index=False),
            file_name="Gambia_Accessibility_Summary.csv",
            mime="text/csv",
            use_container_width=True,
        )

    if not routes and node_df is None:
        st.info("Run a route or accessibility analysis to enable downloads.")


with tab_about:
    st.subheader("Methodology")

    st.markdown("""
    **Vehicle routing**
    - Geocode place names with OpenStreetMap Nominatim.
    - Request driving routes from OSRM.
    - Display route geometry, distance, estimated time and road instructions.
    - Compare alternative routes when the routing service provides them.

    **Travel-time accessibility**
    - Download the drivable OpenStreetMap network near the origin.
    - Assign each road edge a travel time based on length and assumed average speed.
    - Use network shortest paths to calculate reachable road nodes.
    - Summarize nodes reachable within 5, 10, 15, 30, 45 and 60 minutes.

    **Important distinction**
    - OSRM route estimates use its routing profile and mapped road network.
    - Accessibility estimates use the selected constant average speed.
    - Neither calculation uses live traffic in this application.
    - The accessibility analysis is local to the origin, not a full national analysis.
    """)

    st.subheader("Data sources and credits")

    st.markdown("""
    - **Road network:** OpenStreetMap contributors.
    - **Routing engine:** OSRM public demo service.
    - **Geocoding:** OpenStreetMap Nominatim.
    - **Mapping:** Leaflet and Folium.
    - **Satellite basemap:** Esri World Imagery.
    - **Terrain basemap:** OpenTopoMap.
    - **Produced by:** Ansu Singhateh.
    - **GIS / Cartography Unit | GBoS**
    - **GBoS GEOAI • Research and educational use**
    """)

    st.subheader("Limitations")
    st.markdown("""
    Public routing and geocoding services may throttle requests or become unavailable.
    OpenStreetMap road coverage may be incomplete, and estimated travel times may not
    reflect road conditions, traffic, seasonal access or closures. The national
    bounding box is approximate and is not an official boundary polygon.

    Validate outputs before using them for operational planning, public service
    allocation or emergency response. For production deployment, use an appropriately
    licensed, reliable routing service or host your own routing engine.
    """)


st.divider()
st.caption(
    "GBoS GEOAI • Travel Time Routing for The Gambia • "
    "Produced by Ansu Singhateh • Research and educational use"
)
