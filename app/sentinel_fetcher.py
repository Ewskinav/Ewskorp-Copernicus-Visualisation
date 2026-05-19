from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterator, Literal

import numpy as np
import rasterio
from PIL import Image
from pystac_client import Client
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.windows import from_bounds


@dataclass(frozen=True)
class FetchParams:
    top_left_lat: float
    top_left_lon: float
    bottom_right_lat: float
    bottom_right_lon: float
    start_date: date
    end_date: date
    num_frames: int
    max_cloud_coverage: float
    width: int | None = None
    height: int | None = None
    stac_url: str = ""
    collection: Literal["sentinel-2-l2a"] = "sentinel-2-l2a"


def iter_frame_anchor_datetimes(start_date: date, end_date: date, num_frames: int) -> list[datetime]:
    """N evenly spaced target times from start of --start through end of --end (UTC, inclusive)."""
    if num_frames < 1:
        raise ValueError("num_frames must be >= 1")
    tz = timezone.utc
    range_start = datetime.combine(start_date, time.min, tzinfo=tz)
    range_end = datetime.combine(end_date, time.max, tzinfo=tz)
    span_s = (range_end - range_start).total_seconds()
    if span_s < 0:
        raise ValueError("end_date before start_date")
    if num_frames == 1:
        return [range_start + timedelta(seconds=span_s / 2.0)]
    return [range_start + timedelta(seconds=span_s * i / (num_frames - 1)) for i in range(num_frames)]


def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def iter_search_windows(anchor: datetime) -> Iterator[tuple[datetime, datetime]]:
    """
    Try small windows first, then expand; then search forward in time (next hours / days).
    """
    a = anchor if anchor.tzinfo else anchor.replace(tzinfo=timezone.utc)
    a = a.astimezone(timezone.utc)

    for h in (6, 12, 24, 48):
        d = timedelta(hours=h)
        lo, hi = a - d, a + d
        if lo < hi:
            yield lo, hi
    for days in (1, 2, 3, 5, 7, 10, 14, 21, 30, 45, 60):
        d = timedelta(days=days)
        lo, hi = a - d, a + d
        if lo < hi:
            yield lo, hi
    for h in range(1, 73):
        lo, hi = a, a + timedelta(hours=h)
        if lo < hi:
            yield lo, hi
    for d in range(1, 61):
        lo, hi = a, a + timedelta(days=d)
        if lo < hi:
            yield lo, hi


def _wgs84_polygon(params: FetchParams) -> dict:
    minx = params.top_left_lon
    maxx = params.bottom_right_lon
    miny = params.bottom_right_lat
    maxy = params.top_left_lat
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [minx, miny],
                [minx, maxy],
                [maxx, maxy],
                [maxx, miny],
                [minx, miny],
            ]
        ],
    }


def _item_datetime(item: object) -> datetime | None:
    props = getattr(item, "properties", None) or {}
    raw = props.get("datetime")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str):
        try:
            t = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _item_sort_key(it: object, anchor: datetime, *, prefer_clear: bool = False) -> tuple[float, float]:
    acq = _item_datetime(it)
    props = getattr(it, "properties", {}) or {}
    cc = float(props.get("eo:cloud_cover", 100.0))
    if acq is None:
        return (cc, 1e18) if prefer_clear else (1e18, cc)
    dt = abs((acq - anchor).total_seconds())
    # When prefer_clear, sort by cloud cover first, time second
    if prefer_clear:
        return (cc, dt)
    # Otherwise sort by time first, cloud second
    return (dt, cc)


def _read_band_window(asset_href: str, bounds_wgs84: tuple[float, float, float, float], out_w: int, out_h: int) -> np.ndarray:
    with rasterio.Env(AWS_NO_SIGN_REQUEST="YES"):
        with rasterio.open(asset_href) as src:
            transformer = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
            # Clip AOI bounds to the scene's actual extent to avoid out-of-bounds reads
            left, bottom = transformer.transform(bounds_wgs84[0], bounds_wgs84[1])
            right, top = transformer.transform(bounds_wgs84[2], bounds_wgs84[3])
            minx = max(left, src.bounds.left)
            miny = max(bottom, src.bounds.bottom)
            maxx = min(right, src.bounds.right)
            maxy = min(top, src.bounds.top)
            if minx >= maxx or miny >= maxy:
                raise ValueError("AOI fully outside scene extent")
            win = from_bounds(minx, miny, maxx, maxy, transform=src.transform)
            # Compute output shape preserving aspect ratio of clipped region
            win_w = int(round(win.width))
            win_h = int(round(win.height))
            if win_w < 1 or win_h < 1:
                raise ValueError("Window too small")
            out_shape = (out_h, out_w)
            arr = src.read(1, window=win, out_shape=out_shape, resampling=Resampling.bilinear)
            return arr


def _search_items(
    client: Client,
    *,
    collection: str,
    intersects: dict,
    time_lo: datetime,
    time_hi: datetime,
    max_cloud_fraction: float | None,
) -> list:
    q = {}
    if max_cloud_fraction is not None:
        q["eo:cloud_cover"] = {"lte": max_cloud_fraction * 100.0}
    datetime_range = f"{_iso_z(time_lo)}/{_iso_z(time_hi)}"
    try:
        search = client.search(
            collections=[collection],
            intersects=intersects,
            datetime=datetime_range,
            query=q or None,
            max_items=100,
        )
        return list(search.items())
    except Exception:
        return []


def _item_covers_aoi(item: object, bounds_wgs84: tuple[float, float, float, float]) -> bool:
    """Check if the scene's geometry fully contains the AOI."""
    geom = getattr(item, "geometry", None) or {}
    coords = geom.get("coordinates", None)
    if not coords or not coords[0]:
        return False
    # Get scene's footprint bounding box
    ring = coords[0]
    lons = [c[0] for c in ring]
    lats = [c[1] for c in ring]
    scene_minx, scene_maxx = min(lons), max(lons)
    scene_miny, scene_maxy = min(lats), max(lats)
    
    aoi_left, aoi_bottom, aoi_right, aoi_top = bounds_wgs84
    # Check if AOI fits inside scene footprint (with small tolerance)
    tol = 0.01  # ~1km tolerance for sliver overlaps
    return (
        scene_minx - tol <= aoi_left and
        scene_maxx + tol >= aoi_right and
        scene_miny - tol <= aoi_bottom and
        scene_maxy + tol >= aoi_top
    )


def _load_true_color_from_item(item: object, bounds: tuple[float, float, float, float], out_w: int, out_h: int) -> Image.Image | None:
    assets = getattr(item, "assets", {}) or {}
    if not all(k in assets for k in ("red", "green", "blue")):
        return None
    try:
        blue = _read_band_window(assets["blue"].href, bounds, out_w, out_h)
        green = _read_band_window(assets["green"].href, bounds, out_w, out_h)
        red = _read_band_window(assets["red"].href, bounds, out_w, out_h)
    except Exception:
        return None

    rgb = np.stack([red, green, blue], axis=-1).astype(np.float32)
    rgb = np.clip(rgb / 3000.0, 0.0, 1.0)
    rgb8 = (rgb * 255.0).round().astype(np.uint8)
    alpha = np.full((out_h, out_w, 1), 255, dtype=np.uint8)
    rgba = np.concatenate([rgb8, alpha], axis=-1)
    return Image.fromarray(rgba, mode="RGBA")


def fetch_true_color_frames(params: FetchParams) -> list[tuple[datetime, Image.Image]]:
    """
    For each of num_frames targets evenly spaced in [start_date, end_date], find the
    closest-in-time Sentinel-2 L2A scene (expanding time windows; forward bias after symmetric).
    """
    if params.num_frames < 1:
        raise ValueError("num_frames must be >= 1")

    if params.width and params.height:
        out_w, out_h = params.width, params.height
    else:
        out_w, out_h = 512, 512

    out_w = max(64, min(2048, int(out_w)))
    out_h = max(64, min(2048, int(out_h)))

    anchors = iter_frame_anchor_datetimes(params.start_date, params.end_date, params.num_frames)
    stac_url = params.stac_url or os.getenv("STAC_URL") or "https://earth-search.aws.element84.com/v1"
    client = Client.open(stac_url)
    intersects = _wgs84_polygon(params)
    bounds = (params.top_left_lon, params.bottom_right_lat, params.bottom_right_lon, params.top_left_lat)

    frames: list[tuple[datetime, Image.Image]] = []

    for anchor in anchors:
        found = False
        # Gradual cloud relaxation: try user limit, then step up, then no filter
        user = params.max_cloud_coverage
        cloud_steps = [user, max(user, 0.5), 0.6, 0.7, 0.8, 0.9, None]
        cloud_steps = list(dict.fromkeys(cloud_steps))  # dedup preserving order
        for cloud_cap in cloud_steps:
            for lo, hi in iter_search_windows(anchor):
                items = _search_items(
                    client,
                    collection=params.collection,
                    intersects=intersects,
                    time_lo=lo,
                    time_hi=hi,
                    max_cloud_fraction=cloud_cap,
                )
                if not items:
                    continue
                # When no cloud filter (or relaxed), prefer clear images over time proximity
                prefer_clear = (cloud_cap is None or cloud_cap > params.max_cloud_coverage)
                for it in sorted(items, key=lambda x: _item_sort_key(x, anchor, prefer_clear=prefer_clear)):
                    if not _item_covers_aoi(it, bounds):
                        continue
                    img = _load_true_color_from_item(it, bounds, out_w, out_h)
                    if img is None:
                        continue
                    acq = _item_datetime(it) or anchor
                    frames.append((acq, img))
                    found = True
                    break
                if found:
                    break
            if found:
                break

    return frames


def make_bbox(
    top_left_lat: float,
    top_left_lon: float,
    bottom_right_lat: float,
    bottom_right_lon: float,
) -> dict[str, float]:
    return {
        "top_left_lat": top_left_lat,
        "top_left_lon": top_left_lon,
        "bottom_right_lat": bottom_right_lat,
        "bottom_right_lon": bottom_right_lon,
    }
