"""
data_fetcher.py — autonomous data acquisition for the Halibut Predictor.

Every public function is wrapped in st.cache_data so each external source is hit
at most once per session (and re-used across reruns / control changes). Nothing
here touches the local filesystem — all data is pulled live and held in memory.

Sources
  * OpenStreetMap (shorelines, water polygons, piers)  -> osmnx, Overpass fallback
  * NOAA NGDC DEM global mosaic ImageServer (bathymetry) -> getSamples REST
  * NOAA CO-OPS tides, station 8628400 (San Diego)        -> noaa-coops
  * NDBC buoy water temp                                  -> ndbc-api

Each fetcher degrades gracefully: if a source is unreachable the app keeps
running on a clearly-labelled fallback so it is always plug-and-play.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

import numpy as np
import requests

try:
    import streamlit as st
    cache = st.cache_data
except Exception:  # allow standalone import/testing without a Streamlit runtime
    def cache(*a, **k):
        def deco(fn):
            return fn
        return deco if (a and callable(a[0]) is False) else (a[0] if a else deco)

from shapely.geometry import LineString, MultiLineString, Polygon, MultiPolygon
from shapely.ops import unary_union

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOAA_DEM_IMAGESERVER = (
    "https://gis.ngdc.noaa.gov/arcgis/rest/services/"
    "DEM_mosaics/DEM_global_mosaic/ImageServer/getSamples"
)
# NOAA ENC Direct to GIS — authoritative surveyed depth contours (S-57 DEPCNT).
# Harbour band = best detail; approach band is the coastal fallback.
ENC_BANDS = (
    "https://encdirect.noaa.gov/arcgis/rest/services/encdirect/enc_harbour/MapServer",
    "https://encdirect.noaa.gov/arcgis/rest/services/encdirect/enc_approach/MapServer",
)
ENC_CONTOUR_LAYER = 104   # Harbor.Depth_Contour_line (attr VALDCO = depth, metres)
M_TO_FT = 3.280839895
# NOTE: the spec named CO-OPS station "8628400", but that ID is in the
# Chesapeake/Virginia (86xxxxx) range and the API rejects it as invalid. The
# real San Diego Bay tide station is 9410170 (94xxxxx = West Coast). Using the
# correct one so tide data — which the whole ambush engine depends on — works.
TIDE_STATION = "9410170"  # San Diego, San Diego Bay, CA
NDBC_BUOYS = ["46232", "46235", "46225", "46258"]  # Pt Loma S, SD approaches, etc.


# ==========================================================================
# 1. SHORELINE / WATER POLYGONS (OpenStreetMap)
# ==========================================================================


@dataclass
class ShoreData:
    water_geom: object          # (Multi)Polygon of navigable water, WGS84
    shoreline_geom: object      # (Multi)LineString castable edges (shore + piers)
    source: str


def _overpass_shore(bbox) -> ShoreData | None:
    """Fallback: query Overpass directly and assemble shapely geoms."""
    w, s, e, n = bbox
    q = f"""
    [out:json][timeout:30];
    (
      way["natural"="water"]({s},{w},{n},{e});
      relation["natural"="water"]({s},{w},{n},{e});
      way["natural"="coastline"]({s},{w},{n},{e});
      way["man_made"~"pier|breakwater|groyne"]({s},{w},{n},{e});
    );
    out geom;
    """
    r = requests.post(OVERPASS_URL, data={"data": q}, timeout=40)
    r.raise_for_status()
    elems = r.json().get("elements", [])

    polys, lines = [], []
    for el in elems:
        geom = el.get("geometry")
        if not geom or len(geom) < 2:
            continue
        coords = [(p["lon"], p["lat"]) for p in geom]
        tags = el.get("tags", {})
        if tags.get("natural") == "water" and coords[0] == coords[-1] and len(coords) >= 4:
            try:
                polys.append(Polygon(coords))
            except Exception:
                pass
        else:
            lines.append(LineString(coords))

    water = unary_union(polys) if polys else None
    # shoreline = exterior of water polys + coastline/pier lines
    edges = list(lines)
    if water is not None:
        geoms = water.geoms if isinstance(water, MultiPolygon) else [water]
        for g in geoms:
            edges.append(LineString(g.exterior.coords))
            for ring in g.interiors:
                edges.append(LineString(ring.coords))
    shoreline = unary_union(edges) if edges else None
    if water is None and shoreline is None:
        return None
    return ShoreData(water, shoreline, "OpenStreetMap (Overpass)")


def _osmnx_shore(bbox) -> ShoreData | None:
    """Preferred path: osmnx features. Handles both osmnx 1.x and 2.x APIs."""
    import osmnx as ox

    tags = {
        "natural": ["water", "coastline"],
        "man_made": ["pier", "breakwater", "groyne"],
    }
    w, s, e, n = bbox
    gdf = None
    # osmnx >= 2.0 : features_from_bbox(bbox=(left,bottom,right,top), tags)
    for attempt in (
        lambda: ox.features_from_bbox(bbox=(w, s, e, n), tags=tags),
        lambda: ox.features_from_bbox((w, s, e, n), tags),
        # osmnx 1.x : features_from_bbox(north, south, east, west, tags)
        lambda: ox.features_from_bbox(n, s, e, w, tags),
    ):
        try:
            gdf = attempt()
            break
        except TypeError:
            continue

    if gdf is None or gdf.empty:
        return None

    polys, lines = [], []
    for geom in gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        if isinstance(geom, (Polygon, MultiPolygon)):
            polys.append(geom)
        elif isinstance(geom, (LineString, MultiLineString)):
            lines.append(geom)

    water = unary_union(polys) if polys else None
    edges = list(lines)
    if water is not None:
        geoms = water.geoms if isinstance(water, MultiPolygon) else [water]
        for g in geoms:
            edges.append(LineString(g.exterior.coords))
    shoreline = unary_union(edges) if edges else None
    if water is None and shoreline is None:
        return None
    return ShoreData(water, shoreline, "OpenStreetMap (osmnx)")


@cache(show_spinner=False)
def fetch_structures(bbox):
    """
    Marina / dock-basin polygons (OSM `leisure=marina`). Halibut don't hold
    among docked boats — dredged basins are structure/rubble with poor flow and
    no clean sand — so the engine penalises water inside these. Returns a shapely
    (Multi)Polygon in WGS84, or None.
    """
    try:
        import osmnx as ox
        w, s, e, n = bbox
        tags = {"leisure": "marina"}
        gdf = None
        for attempt in (
            lambda: ox.features_from_bbox(bbox=(w, s, e, n), tags=tags),
            lambda: ox.features_from_bbox((w, s, e, n), tags),
            lambda: ox.features_from_bbox(n, s, e, w, tags),
        ):
            try:
                gdf = attempt()
                break
            except TypeError:
                continue
        if gdf is None or gdf.empty:
            return None
        polys = [g for g in gdf.geometry
                 if g is not None and g.geom_type in ("Polygon", "MultiPolygon")]
        if not polys:
            return None
        return unary_union(polys)
    except Exception:
        return None


@cache(show_spinner=False)
def fetch_shore(bbox) -> ShoreData:
    """Water polygon + castable shoreline for a bbox. osmnx -> Overpass -> empty."""
    for fn in (_osmnx_shore, _overpass_shore):
        try:
            sd = fn(bbox)
            if sd is not None:
                return sd
        except Exception:
            continue
    # Last resort: no mask (everything treated as water; documented in UI).
    return ShoreData(None, None, "unavailable (mask disabled)")


# ==========================================================================
# 2. BATHYMETRY (NOAA NGDC DEM global mosaic)
# ==========================================================================


def _grid_size(bbox, target_res_m, lo=64, hi=700):
    """Pixel dimensions (W, H) for a bbox at ~target_res_m, clamped to [lo, hi]."""
    w, s, e, n = bbox
    lat0 = (s + n) / 2.0
    width_m = (e - w) * 111_320.0 * math.cos(math.radians(lat0))
    height_m = (n - s) * 111_320.0
    W = int(min(max(width_m / target_res_m, lo), hi))
    H = int(min(max(height_m / target_res_m, lo), hi))
    return W, H


def _synthetic_bathy(lon2d, lat2d, zone_bearing):
    """Fallback depth surface: deepens toward the basin's open/channel axis."""
    cx, cy = lon2d.mean(), lat2d.mean()
    th = math.radians(zone_bearing)
    ux, uy = math.sin(th), math.cos(th)  # unit vector toward flood bearing
    proj = (lon2d - cx) * ux + (lat2d - cy) * uy
    proj = (proj - proj.min()) / (np.ptp(proj) + 1e-9)
    rng = np.random.default_rng(42)
    depth = 1.0 + 11.0 * proj + rng.normal(0, 0.4, lon2d.shape)
    return np.clip(depth, 0.0, None)


@dataclass
class Bathy:
    lon2d: np.ndarray
    lat2d: np.ndarray
    depth2d: np.ndarray   # metres, positive down (south-up: row 0 = south)
    source: str
    res_m: float = 15.0   # approx ground resolution (m/pixel)


@cache(show_spinner=False)
def fetch_bathymetry(bbox, target_res_m: float = 15.0, zone_bearing: float = 90.0) -> Bathy:
    """
    Fetch a full seabed elevation RASTER for the bbox via the NOAA DEM
    ImageServer `exportImage` op (float32 GeoTIFF), and convert to depth.

    IMPORTANT: the ImageServer forces square pixels and therefore returns a
    slightly DIFFERENT extent than requested (it pads one axis). We must
    georeference to the *returned* extent, not the request, or the whole raster
    is shifted onto land. So we call f=json (gives href + true extent), then
    download the image and grid it to that extent.

    Falls back to a synthetic surface if NOAA/Pillow are unavailable.
    """
    w, s, e, n = bbox
    W, H = _grid_size(bbox, target_res_m)
    export_url = NOAA_DEM_IMAGESERVER.replace("getSamples", "exportImage")
    try:
        import io
        from PIL import Image

        params = {
            "bbox": f"{w},{s},{e},{n}", "bboxSR": "4326", "imageSR": "4326",
            "size": f"{W},{H}", "format": "tiff", "pixelType": "F32",
            "interpolation": "RSP_BilinearInterpolation", "f": "json",
        }
        meta = requests.get(export_url, params=params, timeout=60).json()
        href, ext = meta["href"], meta["extent"]
        img = requests.get(href, timeout=60)
        img.raise_for_status()
        arr = np.array(Image.open(io.BytesIO(img.content)), dtype=float)  # row0=north
        hh, ww = arr.shape
        xmin, ymin = float(ext["xmin"]), float(ext["ymin"])
        xmax, ymax = float(ext["xmax"]), float(ext["ymax"])

        elev = np.flipud(arr)                       # -> south-up (row 0 = ymin)
        depth = -elev
        depth[np.abs(depth) > 1e6] = np.nan         # nodata sentinels (~ -3.4e38)
        if not np.isfinite(depth).any():
            raise RuntimeError("NOAA raster all-nodata")

        lons = np.linspace(xmin, xmax, ww)
        lats = np.linspace(ymin, ymax, hh)          # increasing north
        lon2d, lat2d = np.meshgrid(lons, lats)
        midlat = (ymin + ymax) / 2.0
        res_m = ((xmax - xmin) * 111_320.0 * math.cos(math.radians(midlat)) / ww
                 + (ymax - ymin) * 111_320.0 / hh) / 2.0
        return Bathy(lon2d, lat2d, depth, "NOAA NGDC DEM (exportImage raster)", res_m)
    except Exception:
        lons = np.linspace(w, e, W)
        lats = np.linspace(s, n, H)
        lon2d, lat2d = np.meshgrid(lons, lats)
        res_m = ((e - w) * 111_320.0 * math.cos(math.radians((s + n) / 2)) / W
                 + (n - s) * 111_320.0 / H) / 2.0
        return Bathy(lon2d, lat2d, _synthetic_bathy(lon2d, lat2d, zone_bearing),
                     "synthetic (NOAA unreachable)", res_m)


# ==========================================================================
# 2b. AUTHORITATIVE DEPTH CONTOURS (NOAA ENC, S-57 DEPCNT)
# ==========================================================================


@dataclass
class ContourSet:
    lines: list           # [(depth_m, [(lat, lon), ...]), ...]
    source: str


def _enc_query(base, bbox):
    """Page through ENC depth-contour features intersecting bbox -> lines."""
    w, s, e, n = bbox
    lines, offset = [], 0
    while True:
        p = {
            "where": "1=1",
            "geometry": f"{w},{s},{e},{n}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326", "outSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "VALDCO", "returnGeometry": "true", "f": "geojson",
            "resultOffset": offset, "resultRecordCount": 1000,
        }
        r = requests.get(f"{base}/{ENC_CONTOUR_LAYER}/query", params=p, timeout=50)
        r.raise_for_status()
        j = r.json()
        feats = j.get("features", [])
        for f in feats:
            g = f.get("geometry") or {}
            val = (f.get("properties") or {}).get("VALDCO")
            if val is None:
                continue
            if g.get("type") == "LineString":
                segs = [g["coordinates"]]
            elif g.get("type") == "MultiLineString":
                segs = g["coordinates"]
            else:
                continue
            for seg in segs:
                latlon = [(c[1], c[0]) for c in seg if len(c) >= 2]
                if len(latlon) >= 2:
                    lines.append((float(val), latlon))
        if len(feats) < 1000 or not j.get("exceededTransferLimit"):
            break
        offset += len(feats)
        if offset > 8000:
            break
    return lines


@cache(show_spinner=False)
def fetch_contours(bbox) -> ContourSet:
    """Surveyed depth contours from NOAA ENC (harbour band, approach fallback)."""
    for base in ENC_BANDS:
        try:
            lines = _enc_query(base, bbox)
            if lines:
                band = "harbour" if "harbour" in base else "approach"
                return ContourSet(lines, f"NOAA ENC ({band} band)")
        except Exception:
            continue
    return ContourSet([], "ENC unavailable")


# ==========================================================================
# 3. TIDES (NOAA CO-OPS station 9410170, San Diego Bay)
# ==========================================================================


@dataclass
class TideInfo:
    height_now_m: float
    stage: str                  # one of engine.TIDE_STAGES
    rate_m_per_hr: float = 0.0  # signed dh/dt now (+ incoming, - outgoing)
    strength: float = 0.0       # |current| normalised to the day's max (0..1)
    next_events: list = None    # [(datetime, 'H'/'L', height_m), ...]
    source: str = ""


def _classify_stage(rate, max_rate, height, hi, lo) -> str:
    """
    Tidal-current stage from the rate of water-level change.

    Current speed in a bay is ~proportional to dh/dt: near-zero at the high/low
    turns (slack) and fastest mid-tide. Sign of dh/dt gives direction:
    rising = incoming (flood), falling = outgoing (ebb).
    """
    strength = abs(rate) / max_rate if max_rate > 1e-6 else 0.0
    rising = rate >= 0
    if strength < 0.20:
        # at a turn — name it by which extreme we're nearest
        near_high = abs(height - hi) <= abs(height - lo)
        return "Slack (high tide)" if near_high else "Slack (low tide)"
    band = "Incoming" if rising else "Outgoing"
    qualifier = "max current" if strength >= 0.65 else "building"
    return f"{band} – {qualifier}"


def _stage_from_hilo(events, now):
    """
    Derive (height, signed-rate, strength, stage) from hi/lo events only — for
    subordinate stations that don't serve 6-min predictions. Models the leg as a
    half-cosine, so current strength ~ sin(pi*frac): 0 at the turns, max mid-leg.
    """
    prev = [e for e in events if e[0] <= now]
    nxt = [e for e in events if e[0] > now]
    if not prev or not nxt:
        return 0.8, 0.2, 0.5, "Incoming – building"
    (t0, ty0, h0), (t1, ty1, h1) = prev[-1], nxt[0]
    span = (t1 - t0).total_seconds() or 1.0
    frac = min(max((now - t0).total_seconds() / span, 0.0), 1.0)
    rising = h1 > h0
    height = h0 + (h1 - h0) * (0.5 - 0.5 * math.cos(math.pi * frac))  # cosine ramp
    strength = math.sin(math.pi * frac)                              # 0..1..0
    rate = (1 if rising else -1) * strength
    if strength < 0.20:
        near_high = (ty0 == "H") if frac < 0.5 else (ty1 == "H")
        stage = "Slack (high tide)" if near_high else "Slack (low tide)"
    else:
        band = "Incoming" if rising else "Outgoing"
        stage = f"{band} – {'max current' if strength >= 0.65 else 'building'}"
    return height, rate, strength, stage


@cache(ttl=1800, show_spinner=False)
def fetch_tides(station: str = TIDE_STATION) -> TideInfo:
    try:
        from noaa_coops import Station

        st_ = Station(id=station)
        now = dt.datetime.now()
        begin = (now - dt.timedelta(hours=14)).strftime("%Y%m%d %H:%M")
        end = (now + dt.timedelta(hours=24)).strftime("%Y%m%d %H:%M")
        hilo = st_.get_data(begin_date=begin, end_date=end, product="predictions",
                            datum="MLLW", interval="hilo", units="metric",
                            time_zone="lst_ldt")

        def _valcol(frame):
            return "v" if "v" in frame.columns else "predicted_wl"

        hc = _valcol(hilo)
        events = sorted([(idx.to_pydatetime(), row["type"], float(row[hc]))
                         for idx, row in hilo.iterrows()], key=lambda x: x[0])
        nxt = [e for e in events if e[0] > now][:4]

        # Prefer the precise 6-min series (harmonic/reference stations); fall
        # back to the hi/lo model for subordinate stations that lack it.
        try:
            wl = st_.get_data(
                begin_date=(now - dt.timedelta(hours=3)).strftime("%Y%m%d %H:%M"),
                end_date=(now + dt.timedelta(hours=9)).strftime("%Y%m%d %H:%M"),
                product="predictions", datum="MLLW", units="metric",
                time_zone="lst_ldt")
            vc = _valcol(wl)
            if wl.empty:
                raise RuntimeError("no 6-min data")
            times = np.array([t.to_pydatetime().timestamp() for t in wl.index])
            vals = wl[vc].to_numpy(dtype=float)
            rate = np.gradient(vals, times) * 3600.0
            i_now = int(np.argmin(np.abs(times - now.timestamp())))
            rate_now = float(rate[i_now])
            height_now = float(vals[i_now])
            max_rate = float(np.nanmax(np.abs(rate)))
            hi, lo = float(np.nanmax(vals)), float(np.nanmin(vals))
            strength = abs(rate_now) / max_rate if max_rate > 1e-6 else 0.0
            stage = _classify_stage(rate_now, max_rate, height_now, hi, lo)
        except Exception:
            height_now, rate_now, strength, stage = _stage_from_hilo(events, now)

        return TideInfo(height_now, stage, round(rate_now, 3), round(strength, 2),
                        nxt, f"NOAA CO-OPS {station}")
    except Exception:
        return TideInfo(0.8, "Incoming – building", 0.2, 0.5, [],
                        "unavailable (default stage)")


@dataclass
class TideWindow:
    time: dt.datetime
    stage: str            # "Slack (high tide)" / "Slack (low tide)"
    height_m: float
    kind: str             # 'H' or 'L'
    swing_m: float        # exchange to the adjacent extreme (current strength cue)


@cache(ttl=3600, show_spinner=False)
def fetch_tide_windows(station: str = TIDE_STATION, day_offset: int = 1):
    """
    Tomorrow's PRIME windows = the slack turns (each high/low), since halibut
    feed best around high slack and low slack (light/moderate current). Each
    window carries the surrounding tidal swing as a current-strength cue
    (smaller swing = gentler, generally better; a huge swing = strong current).

    Returns (day_date, hilo_events, windows).
    """
    try:
        from noaa_coops import Station

        st_ = Station(id=station)
        day = dt.date.today() + dt.timedelta(days=day_offset)
        begin = dt.datetime.combine(day, dt.time(0, 0)) - dt.timedelta(hours=6)
        end = dt.datetime.combine(day, dt.time(23, 59)) + dt.timedelta(hours=6)
        hilo = st_.get_data(begin_date=begin.strftime("%Y%m%d %H:%M"),
                            end_date=end.strftime("%Y%m%d %H:%M"),
                            product="predictions", datum="MLLW", interval="hilo",
                            units="metric", time_zone="lst_ldt")
        vc = "v" if "v" in hilo.columns else "predicted_wl"
        events = sorted([(idx.to_pydatetime(), row["type"], float(row[vc]))
                         for idx, row in hilo.iterrows()], key=lambda x: x[0])

        windows = []
        for i, (t, ty, h) in enumerate(events):
            if t.date() != day:
                continue
            neighbours = [events[j][2] for j in (i - 1, i + 1)
                          if 0 <= j < len(events)]
            swing = max(abs(h - hn) for hn in neighbours) if neighbours else 0.0
            stage = "Slack (high tide)" if ty == "H" else "Slack (low tide)"
            windows.append(TideWindow(t, stage, h, ty, swing))
        day_events = [e for e in events if e[0].date() == day]
        return day, day_events, windows
    except Exception:
        return (dt.date.today() + dt.timedelta(days=day_offset)), [], []


# ==========================================================================
# 3b. TIDAL CURRENT DIRECTION (NOAA current-prediction stations)
# ==========================================================================


@dataclass
class CurrentInfo:
    flood_dir: float | None     # degrees true the FLOOD (incoming) current sets toward
    ebb_dir: float | None       # degrees true the EBB (outgoing) current sets toward
    station: str
    source: str


@cache(ttl=86400, show_spinner=False)
def fetch_current(station: str | None) -> CurrentInfo:
    """
    Surveyed mean flood/ebb current directions for a NOAA current-prediction
    station (degrees true). These are station constants embedded in the
    currents_predictions MAX_SLACK response. Returns Nones if unavailable so the
    engine falls back to the zone's heuristic bearing.
    """
    if not station:
        return CurrentInfo(None, None, "", "no current station (heuristic)")
    try:
        now = dt.datetime.now()
        p = {
            "product": "currents_predictions", "application": "halibut-predictor",
            "begin_date": now.strftime("%Y%m%d"),
            "end_date": (now + dt.timedelta(days=2)).strftime("%Y%m%d"),
            "station": station, "time_zone": "lst_ldt", "interval": "MAX_SLACK",
            "units": "english", "format": "json",
        }
        r = requests.get("https://api.tidesandcurrents.noaa.gov/api/prod/datagetter",
                         params=p, timeout=40)
        recs = r.json().get("current_predictions", {}).get("cp", [])
        fd = next((rec["meanFloodDir"] for rec in recs
                   if rec.get("meanFloodDir") is not None), None)
        ed = next((rec["meanEbbDir"] for rec in recs
                   if rec.get("meanEbbDir") is not None), None)
        if fd is None and ed is None:
            return CurrentInfo(None, None, station, "current dir unavailable")
        return CurrentInfo(float(fd) if fd is not None else None,
                           float(ed) if ed is not None else None,
                           station, f"NOAA currents {station}")
    except Exception:
        return CurrentInfo(None, None, station, "current dir unavailable")


# ==========================================================================
# 4. WATER TEMPERATURE / CONDITIONS (NDBC buoys)
# ==========================================================================


@dataclass
class WaterConditions:
    water_temp_c: float | None
    buoy: str | None
    source: str


@cache(ttl=1800, show_spinner=False)
def fetch_water_conditions() -> WaterConditions:
    try:
        from ndbc_api import NdbcApi

        api = NdbcApi()
        end = dt.datetime.now()
        start = end - dt.timedelta(days=4)
        for buoy in NDBC_BUOYS:
            try:
                # ndbc-api wants datetime objects (relative strings raise).
                data = api.get_data(station_id=buoy, mode="stdmet",
                                    start_time=start, end_time=end, as_df=True)
                df = data
                if df is None or df.empty:
                    continue
                col = next((c for c in df.columns if c.upper() == "WTMP"), None)
                if col is None:
                    continue
                series = df[col].dropna()
                series = series[series < 90]  # 99/999 = missing sentinels
                if series.empty:
                    continue
                return WaterConditions(round(float(series.iloc[-1]), 1), buoy,
                                       f"NDBC {buoy}")
            except Exception:
                continue
        return WaterConditions(None, None, "unavailable")
    except Exception:
        return WaterConditions(None, None, "unavailable")
