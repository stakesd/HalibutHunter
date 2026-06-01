"""
engine.py — Halibut Predictor scoring engine + spatial primitives.

This module is pure logic: no network calls, no Streamlit. It owns
  * the hardcoded geofences for the six target fisheries,
  * metric (meter-accurate) distance helpers,
  * water masking against OSM polygons,
  * slope / aspect derivation from a bathymetric grid,
  * the heuristic Halibut scoring function.

IMPORTANT — honest framing:
The scoring model below is a *heuristic* informed by how California halibut
(Paralichthys californicus) actually behave — they are sand-bottom ambush
predators that lie at the base of structure / drop-offs and orient into moving
water. The weights (depth bands, the tide-vs-aspect "ambush" term, the 35 m
shore-cast constraint) are physically reasonable but have NOT been calibrated
against catch data. Treat the output as a prioritisation map, not ground truth.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
from shapely.geometry import Point, box
from shapely.ops import transform as shp_transform
from shapely.prepared import prep

# --------------------------------------------------------------------------
# 1. GEOFENCES
# bbox = (west, south, east, north) in WGS84 degrees.
# flood_bearing = compass heading (deg, 0=N, 90=E) that the FLOOD current
#                 pushes water toward (i.e. into the basin). Ebb is +180.
#                 These follow each basin's main channel axis.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Zone:
    key: str
    name: str
    bbox: tuple[float, float, float, float]
    flood_bearing: float          # FALLBACK heading flood flows toward (deg)
    note: str = ""
    tide_station: str = "9410170"      # per-water NOAA CO-OPS tide station
    current_station: str | None = None  # NOAA current-prediction station (flood/ebb dir)

    @property
    def center(self) -> tuple[float, float]:
        w, s, e, n = self.bbox
        return ((w + e) / 2.0, (s + n) / 2.0)  # (lon, lat)


# Each water gets its OWN NOAA tide station and (where one exists) its own
# current-prediction station, so flood/ebb DIRECTION is the surveyed value for
# that basin rather than a guess. `flood_bearing` is only a fallback.
ZONES: dict[str, Zone] = {
    "mission_bay": Zone(
        "mission_bay", "Mission Bay",
        (-117.258, 32.758, -117.214, 32.796), 70.0,
        "Quiet-water flats; halibut stage on channel edges off Quivira/Mariners.",
        tide_station="9410196",            # Mission Bay, Campland
        current_station=None,              # no NOAA current station -> heuristic
    ),
    "shelter_island": Zone(
        "shelter_island", "Shelter Island",
        (-117.238, 32.702, -117.210, 32.722), 35.0,
        "Pier + launch ramp; classic SD bay halibut drift along the 15-25 ft line.",
        tide_station="9410166",            # San Diego, Quarantine Station
        current_station="PCT0051",         # Quarantine Station, La Playa
    ),
    "harbor_island": Zone(
        "harbor_island", "Harbor Island",
        (-117.220, 32.717, -117.188, 32.732), 95.0,
        "Steep riprap edges drop fast into the main channel — strong ledge ambush.",
        tide_station="9410170", current_station="PCT0061",  # Harbor Island E end
    ),
    "embarcadero": Zone(
        "embarcadero", "Embarcadero",
        (-117.184, 32.700, -117.158, 32.726), 150.0,
        "Downtown waterfront; deep dredged channel, current rips past the piers.",
        tide_station="9410170", current_station="PCT0066",  # SD, 0.5 mi west
    ),
    "coronado_ferry": Zone(
        "coronado_ferry", "Coronado Ferry Landing",
        (-117.182, 32.690, -117.162, 32.706), 320.0,
        "Ferry landing rock + sand transition opposite the main channel bend.",
        tide_station="9410170", current_station="PCT0091",  # Coronado, NE end
    ),
    "south_bay": Zone(
        "south_bay", "South Bay (Pepper Park / J St)",
        (-117.124, 32.636, -117.094, 32.670), 200.0,
        "Shallow back-bay flats; warm, productive on the incoming tide.",
        tide_station="9410152",            # National City, San Diego Bay
        current_station="PCT0111",         # National City, WSW of Pier 12
    ),
}


# --------------------------------------------------------------------------
# 2. METRIC PROJECTION
# Local equirectangular projection about a centroid. Accurate to well under a
# metre over a few-km basin, and needs no pyproj/PROJ install (keeps the app
# truly plug-and-play). Used for the 35 m cast radius and slope spacing.
# --------------------------------------------------------------------------


class LocalMeters:
    """Convert lon/lat <-> local metres about (lon0, lat0)."""

    def __init__(self, lon0: float, lat0: float):
        self.lon0 = lon0
        self.lat0 = lat0
        self.m_per_deg_lat = 111_320.0
        self.m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))

    def fwd(self, lon, lat):
        x = (np.asarray(lon) - self.lon0) * self.m_per_deg_lon
        y = (np.asarray(lat) - self.lat0) * self.m_per_deg_lat
        return x, y

    def fwd_xy(self, lon, lat):  # scalar tuple form for shapely.transform
        return (
            (lon - self.lon0) * self.m_per_deg_lon,
            (lat - self.lat0) * self.m_per_deg_lat,
        )

    def inv(self, x, y):  # metres -> lon, lat
        return (self.lon0 + x / self.m_per_deg_lon,
                self.lat0 + y / self.m_per_deg_lat)

    def to_metric_geom(self, geom):
        return shp_transform(lambda x, y, z=None: self.fwd_xy(x, y), geom)


# --------------------------------------------------------------------------
# 3. GRID + WATER MASK
# --------------------------------------------------------------------------


def build_grid(bbox, spacing_m: float):
    """Return (lon2d, lat2d) meshgrid covering bbox at ~spacing_m resolution."""
    w, s, e, n = bbox
    lat0 = (s + n) / 2.0
    dlat = spacing_m / 111_320.0
    dlon = spacing_m / (111_320.0 * math.cos(math.radians(lat0)))
    lons = np.arange(w, e, dlon)
    lats = np.arange(s, n, dlat)
    return np.meshgrid(lons, lats)  # lon2d, lat2d  (shape: nlat x nlon)


def water_mask(lon2d, lat2d, water_geom):
    """Boolean mask: True where the grid node lies inside an OSM water polygon."""
    if water_geom is None or water_geom.is_empty:
        # No polygon available -> treat everything as water (mask disabled).
        return np.ones_like(lon2d, dtype=bool)
    pg = prep(water_geom)
    flat = np.column_stack([lon2d.ravel(), lat2d.ravel()])
    mask = np.fromiter(
        (pg.contains(Point(x, y)) for x, y in flat),
        dtype=bool, count=flat.shape[0],
    )
    return mask.reshape(lon2d.shape)


# --------------------------------------------------------------------------
# 4. SLOPE / ASPECT from bathymetry
# --------------------------------------------------------------------------


def slope_aspect(depth2d, spacing_m: float):
    """
    Given seabed DEPTH (metres, positive down) on a regular grid, return:
      slope_pct  : rise/run as a fraction (steepness of the drop-off)
      aspect_deg : compass bearing (0=N) that the seabed slopes DOWN toward
                   (the direction deepening water faces — the "open" side of a ledge)
    """
    # np.gradient returns d/d(row)=d/dy(north+) and d/d(col)=d/dx(east+).
    dz_dy, dz_dx = np.gradient(depth2d, spacing_m, spacing_m)
    # Downslope = direction of increasing depth = +gradient of depth.
    slope = np.hypot(dz_dx, dz_dy)
    # bearing of the downslope vector (east=dx, north=dy)
    aspect = (np.degrees(np.arctan2(dz_dx, dz_dy))) % 360.0
    return slope, aspect


# --------------------------------------------------------------------------
# 5. SCORING ENGINE
# --------------------------------------------------------------------------

# Tide stage -> current-energy multiplier. Moving water feeds the ambush; slack
# water shuts it off. Mid-flood / mid-ebb move the most water.
# Tide-stage preference. California halibut are weak-swimming ambush feeders:
# anglers/guides report the two PRIME windows are HIGH SLACK and LOW SLACK, with
# optimum current under ~1.5 kt. A light-to-moderate ("building") current still
# fishes well, but a strong max current is too fast for them to hold and feed —
# so the curve peaks at the slack turns and is LOWEST at max current.
# (Refs: halibuttackle.net tides page; castandspear; norcalkayakanglers.)
TIDE_STAGE_MULT = {
    "Slack (low tide)": 1.00,
    "Incoming – building": 0.85,
    "Incoming – max current": 0.55,
    "Slack (high tide)": 1.00,
    "Outgoing – building": 0.85,
    "Outgoing – max current": 0.55,
}
TIDE_STAGES = list(TIDE_STAGE_MULT.keys())
# Incoming = flood (current pushes toward zone.flood_bearing); the low-tide turn
# is about to flood, so group it with incoming for current direction.
FLOOD_STAGES = {"Incoming – building", "Incoming – max current", "Slack (low tide)"}


# --------------------------------------------------------------------------
# Habitat preference curves. Tuned from California halibut literature/sources:
#  * bay depths: prime ~10-40 ft (3-12 m), productive to ~60 ft (18 m)
#  * substrate: MUST be sand/mud to bury & ambush -> reward flat-ish bottom,
#    penalise steep rock
#  * structure: they sit on sand ADJACENT to drop-offs / channel edges / sand-
#    to-rock transitions where bait funnels (modelled as edge-proximity, below)
#  * temperature: optimum ~16-18 C (61-65 F), drops off sharply outside 13-22 C
#  * current: feed on moving water, esp. around tide changes
# These are heuristic weights, not catch-calibrated coefficients.
# --------------------------------------------------------------------------


def depth_score(depth_m: np.ndarray) -> np.ndarray:
    """Depth preference for bay halibut (m). Prime 3-12 m, productive to ~18 m."""
    d = np.asarray(depth_m, dtype=float)
    s = np.interp(d, [0, 1.5, 3.5, 12.0, 18.0, 30.0, 45.0],
                     [0.0, 0.45, 1.0, 1.0, 0.6, 0.2, 0.05])
    return np.clip(s, 0.0, 1.0)


def substrate_score(slope_pct: np.ndarray) -> np.ndarray:
    """
    Buryable-sand preference. Halibut need soft bottom to bury; flat-to-gentle
    grades hold sand, steep grades are rock/rubble. Peaks on near-flat bottom.
    """
    sp = np.asarray(slope_pct, dtype=float) * 100.0  # fraction -> percent
    return np.clip(np.interp(sp, [0, 2, 6, 12, 25], [1.0, 1.0, 0.7, 0.35, 0.1]),
                   0.0, 1.0)


def temperature_mult(temp_c: float | None) -> float:
    """Water-temp multiplier. Peak 16-18 C; sharp fall-off past ~13 C / ~22 C."""
    if temp_c is None:
        return 1.0
    return float(np.clip(np.interp(temp_c, [10, 13, 16, 18, 21, 24, 27],
                                           [0.30, 0.70, 1.0, 1.0, 0.80, 0.45, 0.20]),
                         0.1, 1.0))


def ambush_alignment(aspect_deg: np.ndarray, current_bearing: float) -> np.ndarray:
    """
    Ambush potential is highest when the current washes DOWN the ledge (current
    bearing aligned with the downslope aspect): bait tumbles off the lip to a
    halibut lying at its base. alignment in [0,1].
    """
    delta = np.radians(np.asarray(aspect_deg) - current_bearing)
    return (np.cos(delta) + 1.0) / 2.0


def current_bearing_for(zone: Zone, tide_stage: str,
                        flood_dir: float | None = None,
                        ebb_dir: float | None = None) -> float:
    """
    Direction the current flows at this tide stage. Uses the surveyed NOAA
    flood/ebb directions when available; otherwise falls back to the zone's
    `flood_bearing` (+180 for ebb).
    """
    fd = flood_dir if flood_dir is not None else zone.flood_bearing
    ed = ebb_dir if ebb_dir is not None else (zone.flood_bearing + 180.0) % 360.0
    return fd if tide_stage in FLOOD_STAGES else ed


@dataclass
class ScoreResult:
    points: list[tuple[float, float, float]] = field(default_factory=list)  # (lat,lon,weight)
    scores: np.ndarray = field(default_factory=lambda: np.array([]))         # candidate scores
    max_score: float = 0.0
    n_candidates: int = 0
    raster: np.ndarray | None = None    # 2D score field (NaN where not fishable)
    extent: tuple | None = None         # (west, south, east, north) of the raster
    spots: list[dict] = field(default_factory=list)  # explainable top spots


def score_zone(
    zone: Zone,
    bathy,                       # data_fetcher.Bathy (full raster, south-up)
    proj: LocalMeters,
    tide_stage: str,
    tide_height_m: float = 0.0,
    weekend_pressure: bool = False,
    water_temp_c: float | None = None,
    cast_radius_m: float = 35.0,
    shore_only: bool = False,
    water_geom=None,             # OSM water polygon (WGS84) to refine the mask
    structure_geom=None,         # OSM marina polygons (WGS84) to penalise docks
    flood_dir: float | None = None,  # surveyed NOAA flood/ebb current directions
    ebb_dir: float | None = None,
) -> ScoreResult:
    """
    Score the ENTIRE water area of the zone on a raster.

    Pipeline (all vectorised over the DEM raster):
      1. water mask = DEM depth>0.3 AND inside the OSM water polygon (so colour
         never bleeds onto land / mixed shoreline pixels),
      2. slope + downslope aspect from the depth gradient,
      3. edge-proximity (smoothed slope) — proximity to a contour drop-off,
      4. distance-to-shore via an exact Euclidean distance transform,
      5. combine habitat curves + live tide/temperature into a 0-100 raster.

    Returns an explainable per-spot factor breakdown in `.spots`.
    `shore_only=True` hard-restricts to within the cast radius.
    """
    import shapely
    from scipy.ndimage import (gaussian_filter, distance_transform_edt,
                               binary_erosion)

    depth = bathy.depth2d + tide_height_m
    depth_filled = np.nan_to_num(depth, nan=-50.0)   # land/nodata -> "dry"
    water = np.isfinite(bathy.depth2d) & (depth > 0.3)

    # Optionally refine with the OSM water polygon — but ONLY if it actually
    # covers the bay. Open water is often tagged as a `coastline` line (not a
    # `water` polygon), so OSM water can be near-empty; don't let that erase
    # the real DEM water. Apply only when it keeps most of the DEM water.
    if water_geom is not None and not water_geom.is_empty and water.any():
        try:
            inside = shapely.contains_xy(water_geom, bathy.lon2d, bathy.lat2d)
            if (water & inside).sum() >= 0.5 * water.sum():
                water &= inside
        except Exception:
            pass

    # Erode one pixel to drop mixed shoreline pixels (stops colour bleeding onto
    # land at the ~15 m DEM resolution).
    if water.sum() > 50:
        water = binary_erosion(water, iterations=1)
    if water.sum() < 5:
        return ScoreResult()

    res_m = max(getattr(bathy, "res_m", 15.0), 1.0)

    # slope (fraction) and downslope aspect (bearing, deg) from the depth
    # gradient. Smooth the depth first (~30 m) so slope/aspect reflect real
    # drop-offs, not 15 m pixel noise — this also de-speckles the score field.
    depth_s = gaussian_filter(depth_filled, sigma=1.2)
    dz_dy, dz_dx = np.gradient(depth_s, res_m, res_m)
    slope = np.hypot(dz_dx, dz_dy)
    aspect = (np.degrees(np.arctan2(dz_dx, dz_dy))) % 360.0

    # edge proximity: smooth the slope field so sand flats NEAR a drop-off
    # (i.e. close to a tight contour) inherit "edge" credit — halibut sit on
    # sand at the base of the ledge, not on the ledge itself.
    edge = gaussian_filter(np.clip(slope, 0, None), sigma=2.0)
    e_hi = np.percentile(edge[water], 92) if water.any() else 1.0
    edge_norm = np.clip(edge / (e_hi + 1e-9), 0.0, 1.0)

    # distance from shore (m): EDT counts pixels to nearest non-water cell
    dist = distance_transform_edt(water) * res_m

    d_sc = depth_score(np.clip(depth, 0.0, None))
    sub_sc = substrate_score(slope)
    cur_bearing = current_bearing_for(zone, tide_stage, flood_dir, ebb_dir)
    a_sc = ambush_alignment(aspect, cur_bearing)
    tide_mult = TIDE_STAGE_MULT.get(tide_stage, 0.7)
    temp_mult = temperature_mult(water_temp_c)

    if shore_only:
        access = np.clip(1.0 - (dist - cast_radius_m) / (0.4 * cast_radius_m), 0.0, 1.0)
        access[dist <= cast_radius_m] = 1.0
    else:
        access = np.ones_like(depth)  # boat / full-bay: reachability not limiting

    if weekend_pressure:
        pressure = np.where(dist <= 60.0, 0.6, 0.85)
    else:
        pressure = np.ones_like(depth)

    # marina / dock-basin penalty: dredged slips are structure+rubble with poor
    # flow and no clean sand — halibut don't hold there, so knock them right down
    # (also stops the model lighting up marina bulkheads as "drop-off edges").
    in_structure = np.zeros(depth.shape, dtype=bool)
    if structure_geom is not None and not structure_geom.is_empty:
        try:
            sg = structure_geom.buffer(8.0 / 111_320.0)  # ~8 m pad
            in_structure = shapely.contains_xy(sg, bathy.lon2d, bathy.lat2d)
        except Exception:
            in_structure = np.zeros(depth.shape, dtype=bool)
    structure_mult = np.where(in_structure, 0.2, 1.0)

    sub_term = 0.35 + 0.65 * sub_sc
    edge_term = 0.55 + 0.45 * edge_norm
    ambush_term = 0.40 + 0.60 * a_sc
    score = (100.0 * d_sc * sub_term * edge_term * ambush_term
             * tide_mult * temp_mult * pressure * access * structure_mult)

    raster = np.where(water, score, np.nan)

    # explainable top spots: highest cells, spatially de-duplicated (~150 m)
    pts: list[tuple[float, float, float]] = []
    spots: list[dict] = []
    flat = np.where(water.ravel())[0]
    if flat.size:
        order = flat[np.argsort(-score.ravel()[flat])]
        chosen = []
        min_sep_deg = 150.0 / 111_320.0
        nc = raster.shape[1]
        for idx in order[:8000]:
            r, c = divmod(int(idx), nc)
            lat = float(bathy.lat2d[r, c]); lon = float(bathy.lon2d[r, c])
            if any(abs(lat - a) < min_sep_deg and abs(lon - b) < min_sep_deg
                   for a, b in chosen):
                continue
            chosen.append((lat, lon))
            sc = float(score[r, c])
            pts.append((lat, lon, sc))
            spots.append({
                "zone": zone.name, "lat": lat, "lon": lon, "score": round(sc, 1),
                "depth_m": round(float(depth[r, c]), 1),
                "slope_pct": round(float(slope[r, c]) * 100, 1),
                "dist_shore_m": round(float(dist[r, c]), 0),
                "f_depth": round(float(d_sc[r, c]), 2),
                "f_substrate": round(float(sub_sc[r, c]), 2),
                "f_edge": round(float(edge_norm[r, c]), 2),
                "f_ambush": round(float(a_sc[r, c]), 2),
                "f_tide": round(float(tide_mult), 2),
                "f_temp": round(float(temp_mult), 2),
                "current_bearing": round(cur_bearing, 0),
                "in_marina": bool(in_structure[r, c]),
            })
            if len(spots) >= 12:
                break

    valid = score[water]
    valid = valid[np.isfinite(valid)]
    w = float(bathy.lon2d.min()); e = float(bathy.lon2d.max())
    s = float(bathy.lat2d.min()); n = float(bathy.lat2d.max())
    return ScoreResult(pts, valid,
                       float(valid.max()) if valid.size else 0.0,
                       int(water.sum()),
                       raster=raster, extent=(w, s, e, n), spots=spots)
