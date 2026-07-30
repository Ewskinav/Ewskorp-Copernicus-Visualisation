"""
Free geocoding helper for looking up place names via Nominatim.
No heavy dependencies beyond ``requests``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

USER_AGENT = "Ewskorp-Copernicus-Visualisation/1.0"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
MIN_BBOX_DEG = 0.05  # minimum bbox size in degrees (expand tiny point results)


@dataclass
class GeocodeResult:
    display_name: str
    lat: float
    lon: float
    bounding_box: tuple[float, float, float, float]  # (lon_min, lat_min, lon_max, lat_max)


def geocode_place(query: str, *, timeout: float = 8.0) -> GeocodeResult | None:
    """
    Look up a place name via Nominatim (OpenStreetMap free geocoder).
    Ensures the returned bounding box is at least MIN_BBOX_DEG wide/tall.
    """
    time.sleep(1.1)  # Rate limit: 1 req/s

    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "addressdetails": 0,
    }
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        print(f"  [geocode] Request failed: {exc}")
        return None
    except ValueError:
        print("  [geocode] Invalid JSON response")
        return None

    if not data:
        return None

    first = data[0]
    bb = first.get("boundingbox")
    if bb and len(bb) == 4:
        lat_min = float(bb[0])
        lat_max = float(bb[1])
        lon_min = float(bb[2])
        lon_max = float(bb[3])
    else:
        lat_min = lat_max = float(first.get("lat", 0))
        lon_min = lon_max = float(first.get("lon", 0))

    # Expand tiny or point-level bounding boxes so they're usable for satellite imagery
    lon_span = lon_max - lon_min
    lat_span = lat_max - lat_min
    if lon_span < MIN_BBOX_DEG:
        mid = (lon_min + lon_max) / 2.0
        lon_min = mid - MIN_BBOX_DEG / 2.0
        lon_max = mid + MIN_BBOX_DEG / 2.0
    if lat_span < MIN_BBOX_DEG:
        mid = (lat_min + lat_max) / 2.0
        lat_min = mid - MIN_BBOX_DEG / 2.0
        lat_max = mid + MIN_BBOX_DEG / 2.0

    return GeocodeResult(
        display_name=first.get("display_name", query),
        lat=float(first.get("lat", 0)),
        lon=float(first.get("lon", 0)),
        bounding_box=(lon_min, lat_min, lon_max, lat_max),
    )