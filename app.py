"""
app.py — Halibut Predictor for San Diego & Mission Bay.

Streamlit front-end. Pulls live shoreline/bathymetry/tide/buoy data once per
session (cached in data_fetcher), then re-scores and re-draws the heatmap
instantly whenever you change the Tide Stage or Weekend Pressure controls.

Run:   streamlit run app.py
"""

from __future__ import annotations

import branca.colormap as bcm
import matplotlib
import numpy as np
import folium
import streamlit as st
from streamlit_folium import st_folium

import engine
import data_fetcher as df

matplotlib.use("Agg")

st.set_page_config(page_title="Halibut Predictor — San Diego", layout="wide")

ESRI_SAT = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
BATHY_RES_M = 15.0        # target DEM raster resolution (m/pixel)
# Fallback DEM contours (metres) if NOAA ENC is unavailable.
CONTOUR_LEVELS = [2, 5, 10, 15, 20, 30]
SCORE_CMAP = matplotlib.colormaps["turbo"]

# ---- imperial conversions (UI is feet / °F; model math stays metric) -------
M_TO_FT = 3.280839895


def ft(m):
    return m * M_TO_FT


def c2f(c):
    return c * 9.0 / 5.0 + 32.0


def ampm(t):
    """12-hour time, no leading zero (e.g. '7:35 AM')."""
    return t.strftime("%I:%M %p").lstrip("0")


# --------------------------------------------------------------------------
# Cached per-zone base data (fetched once; independent of the live controls)
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=True)
def load_zone_base(zone_key: str):
    """Bathymetry raster (+ shoreline, marinas) for one zone. Cached/session."""
    zone = engine.ZONES[zone_key]
    bathy = df.fetch_bathymetry(zone.bbox, BATHY_RES_M, zone.flood_bearing)
    shore = df.fetch_shore(zone.bbox)
    structures = df.fetch_structures(zone.bbox)
    proj = engine.LocalMeters(*zone.center)
    return zone, shore, bathy, proj, structures


def compute_contours(bathy: df.Bathy):
    """
    Nautical-style depth contours from the bathy grid.

    Land (depth < 0.3 m) is masked out so no lines are drawn over shore/marinas;
    the wet grid is upsampled and NaN-aware Gaussian-smoothed before contouring
    so the lines are smooth curves rather than blocky DEM stair-steps.
    """
    try:
        from scipy.ndimage import gaussian_filter, zoom
        import matplotlib.pyplot as plt

        depth = bathy.depth2d.astype(float)
        wet = np.isfinite(depth) & (depth >= 0.3)
        if wet.sum() < 8:
            return []

        filled = np.where(wet, depth, 0.0)
        m = wet.astype(float)

        zf = 3  # upsample for smoother vertices (no new DEM calls)
        fz = zoom(filled, zf, order=1)
        mz = zoom(m, zf, order=1)
        sm = gaussian_filter(fz, sigma=1.6)
        nm = gaussian_filter(mz, sigma=1.6)
        depth_s = np.where(nm > 1e-6, sm / np.maximum(nm, 1e-6), np.nan)
        depth_s[mz < 0.5] = np.nan  # keep land transparent

        lons = np.linspace(bathy.lon2d[0, 0], bathy.lon2d[0, -1], depth_s.shape[1])
        lats = np.linspace(bathy.lat2d[0, 0], bathy.lat2d[-1, 0], depth_s.shape[0])
        LON, LAT = np.meshgrid(lons, lats)

        fig = plt.figure()
        ax = fig.add_subplot(111)
        cs = ax.contour(LON, LAT, depth_s, levels=CONTOUR_LEVELS)
        out = []
        for level, segs in zip(cs.levels, cs.allsegs):
            for seg in segs:
                if len(seg) >= 2:
                    out.append((float(level), [(float(y), float(x)) for x, y in seg]))
        plt.close(fig)
        return out
    except Exception:
        return []


_BLUES = matplotlib.colormaps["Blues"]


def depth_color(level_ft: float) -> str:
    """Deeper contour -> darker blue (nautical convention). Level in FEET."""
    t = min(level_ft / 120.0, 1.0)
    return matplotlib.colors.to_hex(_BLUES(0.30 + 0.65 * t))


def explain(sp: dict) -> str:
    """Plain-English reason a spot scored the way it did, from its factors."""
    d_ft = ft(sp["depth_m"])
    bits = []
    # depth
    if sp["f_depth"] >= 0.85:
        bits.append(f"prime depth ({d_ft:.0f} ft)")
    elif sp["f_depth"] >= 0.5:
        bits.append(f"workable depth ({d_ft:.0f} ft)")
    else:
        bits.append(f"marginal depth ({d_ft:.0f} ft)")
    # substrate
    if sp["f_substrate"] >= 0.8:
        bits.append("flat buryable sand")
    elif sp["f_substrate"] >= 0.5:
        bits.append("mixed bottom")
    else:
        bits.append("steep/hard bottom")
    # structure
    if sp["f_edge"] >= 0.7:
        bits.append("right on a drop-off edge")
    elif sp["f_edge"] >= 0.45:
        bits.append("near a drop-off")
    # current / ambush
    if sp["f_ambush"] >= 0.7:
        bits.append("current sweeping across the ledge (strong ambush)")
    elif sp["f_ambush"] >= 0.45:
        bits.append("some current over the structure")
    else:
        bits.append("current poorly aligned")
    # temp
    if sp["f_temp"] >= 0.9:
        bits.append("water temp ideal")
    elif sp["f_temp"] < 0.6:
        bits.append("water temp off-optimum")
    lead = "⚓ In a marina basin — " if sp.get("in_marina") else ""
    return lead + "; ".join(bits) + "."


def zone_contours(zone, bathy):
    """Authoritative NOAA ENC contours (feet); fall back to DEM-derived lines."""
    cset = df.fetch_contours(zone.bbox)
    if cset.lines:
        return [(d * M_TO_FT, latlon) for d, latlon in cset.lines], cset.source
    # fallback: DEM contours (metres) -> feet
    return ([(lvl * M_TO_FT, latlon) for lvl, latlon in compute_contours(bathy)],
            "DEM-derived (ENC unavailable)")


def score_overlay(result: engine.ScoreResult, name: str):
    """
    Paint the model score field directly: colour == score, not point density.

    Alpha is driven by the score, so weak/zero water is transparent and only
    genuinely good areas glow — keeps the map readable and stops shore-mode
    (where most of the bay is zeroed out) from washing everything in colour.
    """
    Z = result.raster
    if Z is None or result.extent is None or not np.isfinite(Z).any():
        return None

    fin = np.isfinite(Z)
    field = np.where(fin, Z, 0.0)  # raster is dense; browser bilinear-scales it

    norm = np.clip(field / 100.0, 0.0, 1.0)
    rgba = SCORE_CMAP(norm)
    # Alpha tracks the SCORE, not coverage: weak/zero water fades to transparent
    # so only genuinely good areas glow (declutters, and stops shore-mode from
    # painting the whole zeroed-out bay). Hard-mask original land cells.
    alpha = np.clip((field - 25.0) / 45.0, 0.0, 1.0) * 0.78
    alpha[~fin] = 0.0
    rgba[..., 3] = alpha
    rgba = np.flipud(rgba)  # raster row 0 = south; ImageOverlay 'upper' wants north
    w, s, e, n = result.extent
    return folium.raster_layers.ImageOverlay(
        image=rgba, bounds=[[s, w], [n, e]], name=name,
        opacity=1.0, origin="upper", mercator_project=False,
    )


# --------------------------------------------------------------------------
# Sidebar — controls + live environmental readout
# --------------------------------------------------------------------------
st.sidebar.title("🎣 Halibut Predictor")
st.sidebar.caption("San Diego Bay & Mission Bay — shore-cast ambush model")

zone_labels = {k: z.name for k, z in engine.ZONES.items()}
default_zones = ["shelter_island", "harbor_island"]
selected = st.sidebar.multiselect(
    "Fishing zones", options=list(zone_labels.keys()),
    default=default_zones, format_func=lambda k: zone_labels[k],
)

# reference tide = first selected water's own NOAA station (each zone uses its
# own station when scoring; this just drives the sidebar readout)
ref_station = engine.ZONES[selected[0]].tide_station if selected else "9410170"
tide = df.fetch_tides(ref_station)
water = df.fetch_water_conditions()

stage_options = engine.TIDE_STAGES
live_idx = stage_options.index(tide.stage) if tide.stage in stage_options else 0
use_live = st.sidebar.checkbox("Use live tide stage (per water)", value=True)
if use_live:
    manual_stage = None
    st.sidebar.selectbox("Tide stage", stage_options, index=live_idx, disabled=True)
else:
    manual_stage = st.sidebar.selectbox("Tide stage", stage_options, index=live_idx)

mode = st.sidebar.radio(
    "Fishing mode", ["Boat / full bay", "Shore-cast only"], index=0,
    help="Boat scores the whole water area; Shore-cast restricts to within the "
         "cast radius of land (the strict 35 m constraint).",
)
shore_only = mode == "Shore-cast only"
weekend_pressure = st.sidebar.toggle(
    "Weekend pressure", value=False,
    help="Knocks down easy-access shallow spots that get hammered on weekends.",
)
cast_radius_ft = st.sidebar.slider("Cast radius (ft)", 50, 200, 115, 5,
                                   disabled=not shore_only)
cast_radius = cast_radius_ft / M_TO_FT   # -> metres for the engine

st.sidebar.markdown("---")
ref_name = engine.ZONES[selected[0]].name if selected else "—"
st.sidebar.subheader("Live conditions")
st.sidebar.caption(f"Reference water: **{ref_name}**")
st.sidebar.metric("Tide stage", manual_stage if manual_stage else tide.stage)
st.sidebar.metric("Current strength", f"{tide.strength*100:.0f}%",
                  help="From rate of water-level change (dh/dt). Halibut feed "
                       "best near the slack turns in light/moderate current "
                       "(<~1.5 kt); a strong rip is too fast for them.")
st.sidebar.metric("Tide height", f"{ft(tide.height_now_m):.1f} ft")
st.sidebar.metric("Water temp",
                  f"{c2f(water.water_temp_c):.1f} °F" if water.water_temp_c else "n/a")
if tide.next_events:
    nxt = tide.next_events[0]
    st.sidebar.caption(
        f"Next {'High' if nxt[1]=='H' else 'Low'} @ "
        f"{ampm(nxt[0])} ({ft(nxt[2]):.1f} ft)"
    )
st.sidebar.caption(f"Tides: {tide.source}")
st.sidebar.caption(f"Buoy: {water.source}")


# --------------------------------------------------------------------------
# Main — title + map
# --------------------------------------------------------------------------
st.title("Halibut Probability Heatmap")
st.caption(
    "Habitat model from CA-halibut literature — depth band (prime 3–12 m), "
    "buryable-sand substrate, drop-off/edge proximity, tide-vector vs. "
    "seabed-aspect ambush, water-temp fit (~16–18 °C), and a tide preference "
    "that peaks at the **slack turns / light current** (halibut can't hold in a "
    "strong rip). Heuristic weights, not catch-calibrated; a prioritisation map."
)

if not selected:
    st.info("Select at least one fishing zone in the sidebar to begin.")
    st.stop()

# --- 1. compute every selected zone first (need spots before building map) --
source_notes: list[str] = []
overlays = []          # (zone, score ImageOverlay, contour polylines)
all_spots: list[dict] = []

for key in selected:
    zone, shore, bathy, proj, structures = load_zone_base(key)
    tz = df.fetch_tides(zone.tide_station)          # this water's own tide
    cur = df.fetch_current(zone.current_station)    # this water's flood/ebb dir
    stage_z = manual_stage if manual_stage else tz.stage
    result = engine.score_zone(
        zone, bathy, proj,
        tide_stage=stage_z, tide_height_m=tz.height_now_m,
        weekend_pressure=weekend_pressure, water_temp_c=water.water_temp_c,
        cast_radius_m=float(cast_radius), shore_only=shore_only,
        water_geom=shore.water_geom, structure_geom=structures,
        flood_dir=cur.flood_dir, ebb_dir=cur.ebb_dir,
    )
    contours, csource = zone_contours(zone, bathy)
    overlays.append((zone, score_overlay(result, f"{zone.name} score"), contours))
    all_spots.extend(result.spots)
    cdir = (f"flood {cur.flood_dir:.0f}°/ebb {cur.ebb_dir:.0f}°"
            if cur.flood_dir is not None else "heuristic dir")
    source_notes.append(
        f"**{zone.name}** — tide: {tz.source} ({stage_z}); current: {cur.source} "
        f"({cdir}); contours: {csource} — peak {result.max_score:.0f}")

all_spots.sort(key=lambda d: d["score"], reverse=True)
all_spots = all_spots[:15]

# --- 2. "fly to" control (needs the spot list) ------------------------------
st.sidebar.markdown("---")
spot_labels = ["Whole selection"] + [
    f"#{i+1} · {s['zone']} · {s['score']:.0f}" for i, s in enumerate(all_spots)]
fly_idx = st.sidebar.selectbox("Zoom to top spot", range(len(spot_labels)),
                               format_func=lambda i: spot_labels[i], index=0)

# fly target token: selectbox OR a map marker click (last action wins)
ss = st.session_state
ss.setdefault("nav", ("sel", 0))
ss.setdefault("last_sel", 0)
if fly_idx != ss.last_sel:                 # selectbox changed
    ss.nav, ss.last_sel = ("sel", fly_idx), fly_idx

kind, val = ss.nav
if kind == "spot":
    s0 = all_spots[val]
    center, zoom, fly_spot = [s0["lat"], s0["lon"]], 16, s0
elif kind == "sel" and val > 0:
    s0 = all_spots[val - 1]
    center, zoom, fly_spot = [s0["lat"], s0["lon"]], 16, s0
else:
    centers = np.array([engine.ZONES[k].center for k in selected])
    center, zoom, fly_spot = [centers[:, 1].mean(), centers[:, 0].mean()], 13, None

m = folium.Map(location=center, zoom_start=zoom, tiles=None, control_scale=True)
folium.TileLayer(tiles=ESRI_SAT, attr="Esri World Imagery",
                 name="Satellite", overlay=False).add_to(m)

score_group = folium.FeatureGroup(name="Halibut score (model)", show=True)
contour_group = folium.FeatureGroup(name="Depth contours", show=True)

for zone, ov, contours in overlays:
    w, s, e, n = zone.bbox
    folium.Rectangle([[s, w], [n, e]], color="#ffd166", weight=1,
                     fill=False, opacity=0.4,
                     tooltip=f"{zone.name} — {zone.note}").add_to(m)
    if ov is not None:
        ov.add_to(score_group)
    for level_ft, latlon in contours:
        folium.PolyLine(latlon, color=depth_color(level_ft), weight=1.2,
                        opacity=0.8, tooltip=f"{level_ft:.0f} ft").add_to(contour_group)

contour_group.add_to(m)
score_group.add_to(m)

# numbered, clickable markers for the top spots (popup = WHY it scored)
spot_group = folium.FeatureGroup(name="Top spots", show=True)
for i, sp in enumerate(all_spots[:10]):
    popup_html = (
        f"<b>#{i+1} — {sp['zone']}</b><br>"
        f"<b>Score {sp['score']:.0f}/100</b><hr style='margin:3px 0'>"
        f"Depth: {ft(sp['depth_m']):.0f} ft &nbsp; Slope: {sp['slope_pct']}%<br>"
        f"Dist. to shore: {ft(sp['dist_shore_m']):.0f} ft<hr style='margin:3px 0'>"
        f"<i>{explain(sp)}</i><br>"
        f"<small>depth-fit {sp['f_depth']} · sand {sp['f_substrate']} · "
        f"edge {sp['f_edge']} · ambush {sp['f_ambush']} · temp ×{sp['f_temp']}</small><br>"
        f"<small>{sp['lat']:.5f}, {sp['lon']:.5f}</small>"
    )
    folium.Marker(
        [sp["lat"], sp["lon"]],
        popup=folium.Popup(popup_html, max_width=260),
        tooltip=f"#{i+1} · score {sp['score']:.0f}",
        icon=folium.DivIcon(html=(
            f"<div style='background:#111;color:#fff;border:2px solid #fff;"
            f"border-radius:50%;width:22px;height:22px;line-height:19px;"
            f"text-align:center;font-weight:bold;font-size:12px'>{i+1}</div>")),
    ).add_to(spot_group)
spot_group.add_to(m)

legend = bcm.LinearColormap(
    [matplotlib.colors.to_hex(SCORE_CMAP(i / 8)) for i in range(9)],
    vmin=0, vmax=100, caption="Halibut probability score (model output)",
)
legend.add_to(m)
folium.LayerControl(collapsed=True).add_to(m)

col_map, col_side = st.columns([3, 1])
with col_map:
    # center/zoom params pan the map when the nav target changes (reliable
    # programmatic move); stable key keeps click events flowing.
    map_data = st_folium(m, center=center, zoom=zoom, height=640,
                         use_container_width=True,
                         returned_objects=["last_object_clicked"], key="mainmap")

# clicking a marker -> jump to that spot (match click to nearest spot)
clicked = (map_data or {}).get("last_object_clicked")
if clicked and all_spots:
    cl, cn = clicked.get("lat"), clicked.get("lng")
    if cl is not None:
        j = min(range(len(all_spots)),
                key=lambda i: (all_spots[i]["lat"] - cl) ** 2
                              + (all_spots[i]["lon"] - cn) ** 2)
        d2 = (all_spots[j]["lat"] - cl) ** 2 + (all_spots[j]["lon"] - cn) ** 2
        if d2 < (50 / 111_320.0) ** 2 and ss.nav != ("spot", j):
            ss.nav = ("spot", j)
            st.rerun()

with col_side:
    st.subheader("Top spots — why")
    if fly_spot is not None:
        st.markdown(f"**Focused: {fly_spot['zone']} — score "
                    f"{fly_spot['score']:.0f}/100**")
        st.write(explain(fly_spot))
        st.caption(f"{ft(fly_spot['depth_m']):.0f} ft deep · "
                   f"{ft(fly_spot['dist_shore_m']):.0f} ft from shore · "
                   f"{fly_spot['lat']:.5f}, {fly_spot['lon']:.5f}")
        st.markdown("---")
    if all_spots:
        st.caption("Click a numbered marker to fly there, or use **Zoom to top "
                   "spot** in the sidebar.")
        st.dataframe(
            {"#": list(range(1, len(all_spots) + 1)),
             "Zone": [s["zone"] for s in all_spots],
             "Score": [s["score"] for s in all_spots],
             "Depth ft": [round(ft(s["depth_m"])) for s in all_spots],
             "Edge": [s["f_edge"] for s in all_spots],
             "Ambush": [s["f_ambush"] for s in all_spots]},
            hide_index=True, use_container_width=True,
        )
    else:
        st.write("No fishable water scored for the current settings.")

# --------------------------------------------------------------------------
# Tomorrow's game plan — read the forecast tide and rank windows × spots
# --------------------------------------------------------------------------
st.markdown("---")
st.subheader("📅 Tomorrow's game plan")

# build the plan per WATER (each uses its own tide station + current direction)
rows = []
plan_day = None
for key in selected:
    zone, shore, bathy, proj, structures = load_zone_base(key)
    cur = df.fetch_current(zone.current_station)
    day, _, windows = df.fetch_tide_windows(zone.tide_station, 1)
    plan_day = plan_day or day
    for wdw in windows:
        res = engine.score_zone(
            zone, bathy, proj, tide_stage=wdw.stage, tide_height_m=wdw.height_m,
            weekend_pressure=weekend_pressure, water_temp_c=water.water_temp_c,
            cast_radius_m=float(cast_radius), shore_only=shore_only,
            water_geom=shore.water_geom, structure_geom=structures,
            flood_dir=cur.flood_dir, ebb_dir=cur.ebb_dir)
        best = res.spots[0] if res.spots else None
        swing_ft = ft(wdw.swing_m)
        strength = ("gentle" if swing_ft < 3 else
                    "moderate" if swing_ft < 5 else "strong")
        rows.append({
            "Time": ampm(wdw.time),
            "Water": zone.name,
            "Window": f"{'High' if wdw.kind=='H' else 'Low'} slack",
            "Exchange": f"{swing_ft:.1f} ft ({strength})",
            "Spot score": round(best["score"], 0) if best else 0,
            "Depth ft": round(ft(best["depth_m"])) if best else 0,
            "Lat,Lon": f"{best['lat']:.4f}, {best['lon']:.4f}" if best else "—",
            "_t": wdw.time,
        })

if not rows:
    st.info("Tomorrow's tide forecast is unavailable right now.")
else:
    st.caption(f"**{plan_day:%A %b %d}** — each water scored on its own NOAA "
               "tide station and surveyed flood/ebb current direction.")
    rows.sort(key=lambda r: (r["Spot score"], -float(r["Exchange"].split()[0])),
              reverse=True)
    show = [{k: v for k, v in r.items() if k != "_t"} for r in rows]
    st.dataframe(show, hide_index=True, use_container_width=True)
    top = rows[0]
    st.success(
        f"**Best window:** {top['Time']} — {top['Water']} {top['Window']} "
        f"({top['Exchange']} exchange) near {top['Lat,Lon']} "
        f"(~{top['Depth ft']} ft, score {top['Spot score']:.0f}). "
        "Halibut feed best around the slack turns; gentle/moderate exchanges "
        "(≈3–5 ft, current under ~1.5 kt) beat a strong rip.")
    st.caption("Each slack is scored with that water's real current direction "
               "for that turn, so high-slack and low-slack favour different spots.")

with st.expander("Data sources & model notes"):
    for note in source_notes:
        st.markdown(f"- {note}")
    st.markdown(
        "- Depth **contours** are authoritative **NOAA ENC** survey lines "
        "(S-57 DEPCNT, harbour band) — the same data on nautical charts — shown "
        "in feet. DEM-derived contours are only a fallback.\n"
        "- Bathymetry surface (for slope/aspect/scoring): full **NOAA NGDC DEM** "
        "raster per zone (one `exportImage` call, ~15 m/pixel), georeferenced to "
        "NOAA's returned extent. Whole water area is scored.\n"
        "- Shorelines from **OpenStreetMap** (osmnx, Overpass fallback).\n"
        "- All depths/distances shown in **feet**, temperature in **°F**.\n"
        "- **Per-water** NOAA stations: each zone uses its own CO-OPS tide "
        "station (Mission Bay 9410196, National City 9410152, SD Bay 9410170/"
        "9410166) **and** its own current-prediction station, so flood/ebb "
        "**direction** is surveyed per basin (drives the ambush term), not "
        "guessed. Water temp from **NDBC** buoys.\n"
        "- The **score field** colour is the model output directly (turbo 0→100), "
        "not a point-density heatmap.\n"
        "- **Model terms (CA-halibut literature):** depth band (prime 3–12 m) · "
        "buryable-sand substrate (penalises steep rock) · drop-off/edge proximity "
        "· tide-vector vs. seabed-aspect ambush · water-temp fit (~16–18 °C) · "
        "tide-stage energy · weekend pressure on easy-access shallows."
    )
