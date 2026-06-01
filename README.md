# 🎣 Halibut Predictor — San Diego & Mission Bay

A fully autonomous, plug-and-play predictive heatmap for shore-cast California
halibut across six San Diego fisheries: **Mission Bay, Shelter Island, Harbor
Island, Embarcadero, Coronado Ferry Landing, and South Bay (Pepper Park / J St)**.

No shapefiles, no manual downloads, no file paths. Every byte of geospatial and
environmental data is pulled live at runtime and cached for the session.

## Quick start

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open the URL Streamlit prints (default http://localhost:8501).

## What it does

| Layer | Source | How |
|-------|--------|-----|
| Shorelines / water polygons | OpenStreetMap | `osmnx` with a built-in Overpass-API fallback |
| Bathymetry (depth, slope, aspect) | NOAA NGDC DEM global mosaic | `getSamples` REST, sampled on the scoring grid |
| Depth contours | derived | `matplotlib` contour extraction over the grid |
| Tides | NOAA CO-OPS **station 8628400** | `noaa-coops` |
| Water temperature | NDBC buoys (46235 / 46232 / 46225) | `ndbc-api` |

Each source is wrapped in `st.cache_data` so it is fetched **once per session**
and reused across reruns. If any source is offline the app falls back to a
clearly-labelled default (e.g. a synthetic depth surface) so it always runs.

## The scoring engine (`engine.py`)

For every water node on a 60 m grid:

1. **Accessibility** — distance to castable shoreline (OSM shore + piers),
   projected to true metres. Full credit within the cast radius (default
   **35 m**, adjustable), fading to zero beyond a long cast.
2. **Depth band** — preference curve for sandy halibut depths (prime ≈ 3.5–9 m,
   tide-adjusted with the live water level).
3. **Slope** — rewards defined drop-offs / ledges over featureless flats.
4. **Ambush** — the live tide **current vector** (flood pushes toward each
   basin's channel axis; ebb is the reciprocal) compared against the seabed
   **downslope aspect**. Current washing *down* a ledge = high ambush.
5. **Tide stage** multiplier (moving water beats slack) and an optional
   **Weekend Pressure** penalty on easy-access shallows.

Change **Tide Stage** or **Weekend Pressure** and the heatmap re-scores and
redraws instantly — only the cheap scoring step reruns; the cached fetches do not.

## ⚠️ Honest caveats

- The scoring weights are a **physically-reasonable heuristic**, not validated
  fisheries science. Use the map to prioritise spots, not as a guarantee.
- NOAA does not expose on-demand vector depth contours for arbitrary areas, so
  contours/slope are derived from the sampled DEM grid (real measured data,
  gridded). Resolution is coastal-DEM scale, not survey-grade.
- Current *direction* is modelled from each basin's channel axis + tide phase,
  not from a per-zone ADCP current station.

## Files

- `app.py` — Streamlit UI + Folium satellite map, heatmap, contours, controls.
- `engine.py` — geofences, metric projection, water mask, slope/aspect, scoring.
- `data_fetcher.py` — cached autonomous fetchers (OSM / NOAA DEM / tides / buoys).
