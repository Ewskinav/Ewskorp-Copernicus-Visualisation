from __future__ import annotations

import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Iterator, Literal

import numpy as np
import rasterio
from PIL import Image
from pystac_client import Client
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.windows import from_bounds
from rasterio.warp import reproject


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
    max_workers: int = 4
    quality_scale: int = 1


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
    Search windows: try most likely windows first, then expand.
    Reduced from ~147 windows to ~20 by removing redundant forward-only searches.
    """
    a = anchor if anchor.tzinfo else anchor.replace(tzinfo=timezone.utc)
    a = a.astimezone(timezone.utc)

    # Most common revisit windows first (Sentinel-2 revisits every 5-10 days)
    for h in (6, 12, 24, 48):
        d = timedelta(hours=h)
        lo, hi = a - d, a + d
        if lo < hi:
            yield lo, hi
    # Expand to days
    for days in (1, 2, 3, 5, 7, 10, 14, 21, 30, 45, 60):
        d = timedelta(days=days)
        lo, hi = a - d, a + d
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
    if prefer_clear:
        return (cc, dt)
    return (dt, cc)


# Scene-level cache — avoids re-downloading the same Sentinel-2 scene for multiple frames
_band_cache: dict[tuple[str, float, float, float, float, int, int], np.ndarray | None] = {}
_band_cache_hits = 0
_band_cache_misses = 0


def _clear_band_cache() -> None:
    """Clear the band cache."""
    global _band_cache, _band_cache_hits, _band_cache_misses
    _band_cache.clear()
    _band_cache_hits = 0
    _band_cache_misses = 0


def _get_band_cache_stats() -> dict:
    return {"hits": _band_cache_hits, "misses": _band_cache_misses}


def _compute_output_dims(params: FetchParams) -> tuple[int, int]:
    """
    Compute output pixel dimensions that preserve the real-world aspect ratio
    of the area of interest.

    WGS84 degrees are not square in real-world distance.
    1° latitude ≈ 111 km everywhere, but 1° longitude ≈ 111 × cos(lat) km.
    So rendering a degree-spanning box into a square pixel grid distorts the image.

    This function computes physically correct dimensions using:
    - Physical-width-proportional = lon_span × cos(mid_lat)
    - Physical-height-proportional = lat_span

    The target pixel count comes from user-supplied width/height if both given,
    otherwise defaults to ~262k pixels (~512×512 equivalent).
    """
    lon_span = params.bottom_right_lon - params.top_left_lon
    lat_span = params.top_left_lat - params.bottom_right_lat

    if lat_span <= 0 or lon_span <= 0:
        return (512, 512)

    # Mid-latitude in radians for cosine longitude-compression correction
    mid_lat_rad = math.radians((params.top_left_lat + params.bottom_right_lat) / 2.0)
    cos_lat = math.cos(mid_lat_rad)

    # Physical proportions (proportional to real-world km)
    phys_width = lon_span * cos_lat
    phys_height = lat_span

    # Determine target pixel count
    if params.width is not None and params.height is not None:
        target_pixels = params.width * params.height
    else:
        target_pixels = 262144  # default ~512×512

    # Compute dimensions that preserve physical aspect ratio
    phys_ratio = phys_width / phys_height

    if phys_ratio >= 1.0:
        out_w = int(round(math.sqrt(target_pixels * phys_ratio)))
        out_h = int(round(target_pixels / out_w))
    else:
        out_h = int(round(math.sqrt(target_pixels / phys_ratio)))
        out_w = int(round(target_pixels / out_h))

    out_w = max(64, min(2048, out_w))
    out_h = max(64, min(2048, out_h))
    return (out_w, out_h)


def _read_tile_into_wgs84(
    asset_href: str,
    bounds_wgs84: tuple[float, float, float, float],
    out_w: int,
    out_h: int,
    quality_scale: int = 1,
) -> np.ndarray | None:
    """
    Read a single band from the given asset, reprojecting to WGS84 lat/lon grid
    so multiple tiles produce pixel-aligned arrays.
    Returns a (out_h, out_w) uint16 array or None on failure.
    Results are cached by asset URL.
    """
    global _band_cache, _band_cache_hits, _band_cache_misses

    cache_key = (asset_href, bounds_wgs84[0], bounds_wgs84[1], bounds_wgs84[2], bounds_wgs84[3], out_w, out_h)

    if cache_key in _band_cache:
        _band_cache_hits += 1
        result = _band_cache[cache_key]
        if result is None:
            return None
        return result.copy()

    _band_cache_misses += 1

    try:
        with rasterio.Env(AWS_NO_SIGN_REQUEST="YES"):
            with rasterio.open(asset_href) as src:
                transformer = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
                left, bottom = transformer.transform(bounds_wgs84[0], bounds_wgs84[1])
                right, top = transformer.transform(bounds_wgs84[2], bounds_wgs84[3])
                minx = max(left, src.bounds.left)
                miny = max(bottom, src.bounds.bottom)
                maxx = min(right, src.bounds.right)
                maxy = min(top, src.bounds.top)
                if minx >= maxx or miny >= maxy:
                    raise ValueError("AOI fully outside scene extent")

                win = from_bounds(minx, miny, maxx, maxy, transform=src.transform)

                read_h = out_h * quality_scale
                read_w = out_w * quality_scale
                src_data = src.read(
                    1,
                    window=win,
                    out_shape=(read_h, read_w),
                    resampling=Resampling.bilinear,
                )

                dst_array = np.zeros((out_h, out_w), dtype=np.float32)
                dst_transform = rasterio.transform.from_bounds(
                    bounds_wgs84[0], bounds_wgs84[1],
                    bounds_wgs84[2], bounds_wgs84[3],
                    out_w, out_h,
                )
                dst_crs = "EPSG:4326"

                src_transform = rasterio.transform.from_bounds(
                    minx, miny, maxx, maxy,
                    src_data.shape[1], src_data.shape[0],
                )

                reproject(
                    source=src_data,
                    destination=dst_array,
                    src_transform=src_transform,
                    src_crs=src.crs,
                    dst_transform=dst_transform,
                    dst_crs=dst_crs,
                    resampling=Resampling.bilinear,
                )

                _band_cache[cache_key] = dst_array.copy()
                return dst_array
    except Exception:
        _band_cache[cache_key] = None
        return None


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


def _item_overlaps(item: object, bounds_wgs84: tuple[float, float, float, float]) -> bool:
    """Check if the scene's footprint overlaps any part of the AOI."""
    geom = getattr(item, "geometry", None) or {}
    coords = geom.get("coordinates", None)
    if not coords or not coords[0]:
        return False
    ring = coords[0]
    lons = [c[0] for c in ring]
    lats = [c[1] for c in ring]
    sx_min, sx_max = min(lons), max(lons)
    sy_min, sy_max = min(lats), max(lats)

    aoi_left, aoi_bottom, aoi_right, aoi_top = bounds_wgs84
    return (
        sx_min < aoi_right and
        sx_max > aoi_left and
        sy_min < aoi_top and
        sy_max > aoi_bottom
    )


def _get_tile_grid(item_id: str) -> str:
    parts = item_id.split("_")
    return parts[1] if len(parts) >= 2 else item_id


def _mosaic_reprojected(layers: list[np.ndarray]) -> np.ndarray:
    """
    Mosaic multiple (H, W, 3) uint8 arrays that are already in the same
    WGS84 grid. Empty pixels (all channels < 5) from one layer are filled
    by the next layer.
    """
    if not layers:
        return np.zeros((1, 1, 3), dtype=np.uint8)
    if len(layers) == 1:
        return layers[0]

    canvas = layers[0].astype(np.float32)
    for layer in layers[1:]:
        layer_f = layer.astype(np.float32)
        empty = np.all(canvas < 5, axis=2)
        for c in range(3):
            canvas[:, :, c] = np.where(empty, layer_f[:, :, c], canvas[:, :, c])

    return canvas.astype(np.uint8)


def _load_true_color_reprojected(
    item: object,
    bounds: tuple[float, float, float, float],
    out_w: int,
    out_h: int,
    quality_scale: int = 1,
) -> np.ndarray | None:
    """
    Load true-color bands for an item, reprojected to a common WGS84 grid.
    Returns uint8 (H, W, 3) or None.
    """
    assets = getattr(item, "assets", {}) or {}
    if not all(k in assets for k in ("red", "green", "blue")):
        return None

    blue = _read_tile_into_wgs84(assets["blue"].href, bounds, out_w, out_h, quality_scale)
    green = _read_tile_into_wgs84(assets["green"].href, bounds, out_w, out_h, quality_scale)
    red = _read_tile_into_wgs84(assets["red"].href, bounds, out_w, out_h, quality_scale)

    if any(b is None for b in (red, green, blue)):
        return None

    rgb = np.stack([red, green, blue], axis=-1)
    rgb = np.clip(rgb / 3000.0, 0.0, 1.0)
    rgb8 = (rgb * 255.0).round().astype(np.uint8)
    return rgb8


def _extract_closest_date_tile_items(
    client: Client,
    intersects: dict,
    anchor: datetime,
    max_cloud_fraction: float | None,
    bounds: tuple[float, float, float, float],
    user_cloud: float,
) -> list[tuple[datetime, str, list]]:
    """
    For a given anchor time, search expanding windows and return the earliest
    window where we find items. Items are grouped by tile grid.
    Returns a list of (acquisition_datetime, tile_id, [item1, item2, ...]) tuples,
    one per tile grid that covers the AOI.
    """
    cloud_steps = [max_cloud_fraction, max(max_cloud_fraction or 0, 0.5), 0.6, 0.7, 0.8, 0.9, None]
    cloud_steps = list(dict.fromkeys(cloud_steps))

    for cloud_cap in cloud_steps:
        for lo, hi in iter_search_windows(anchor):
            items = _search_items(
                client,
                collection="sentinel-2-l2a",
                intersects=intersects,
                time_lo=lo,
                time_hi=hi,
                max_cloud_fraction=cloud_cap,
            )
            if not items:
                continue

            overlapping = [it for it in items if _item_overlaps(it, bounds)]
            if not overlapping:
                continue

            by_tile: dict[str, list] = {}
            for it in overlapping:
                tile = _get_tile_grid(getattr(it, "id", ""))
                if tile not in by_tile:
                    by_tile[tile] = []
                by_tile[tile].append(it)

            result_items = []
            for tile, tile_items in by_tile.items():
                prefer_clear = (cloud_cap is None or cloud_cap > user_cloud)
                sorted_items = sorted(tile_items, key=lambda x: _item_sort_key(x, anchor, prefer_clear=prefer_clear))
                result_items.append((_item_datetime(sorted_items[0]) or anchor, tile, sorted_items))

            if result_items:
                return result_items

    return []


def _fetch_single_frame(
    params: FetchParams,
    client: Client,
    intersects: dict,
    bounds: tuple[float, float, float, float],
    anchor: datetime,
    out_w: int,
    out_h: int,
    idx: int,
    total: int,
    on_progress: Callable | None = None,
) -> tuple[datetime, Image.Image] | None:
    """Fetch a single frame."""
    if on_progress is not None:
        on_progress(idx + 1, total, "searching")

    tile_items = _extract_closest_date_tile_items(
        client, intersects, anchor,
        max_cloud_fraction=params.max_cloud_coverage,
        bounds=bounds,
        user_cloud=params.max_cloud_coverage,
    )

    if not tile_items:
        if on_progress is not None:
            on_progress(idx + 1, total, "no_match")
        return None

    if on_progress is not None:
        on_progress(idx + 1, total, "downloading")

    layers: list[np.ndarray] = []
    primary_acq = tile_items[0][0]

    for acq, tile, candidates in tile_items:
        for it in candidates:
            rgb8 = _load_true_color_reprojected(it, bounds, out_w, out_h, params.quality_scale)
            if rgb8 is None:
                continue
            layers.append(rgb8)
            break

    if not layers:
        if on_progress is not None:
            on_progress(idx + 1, total, "no_match")
        return None

    # Mosaic all reprojected layers (all on the same WGS84 grid)
    result_rgb = _mosaic_reprojected(layers)

    # Reject if the final mosaic still has too much empty (black) area
    valid_pixels = np.any(result_rgb > 10, axis=2).sum()
    total_pixels = result_rgb.shape[0] * result_rgb.shape[1]
    coverage = valid_pixels / total_pixels
    if coverage < 0.80:
        if on_progress is not None:
            on_progress(idx + 1, total, "low_coverage")
        return None

    alpha = np.full((out_h, out_w, 1), 255, dtype=np.uint8)
    rgba = np.concatenate([result_rgb, alpha], axis=-1)
    result_img = Image.fromarray(rgba, mode="RGBA")

    if on_progress is not None:
        on_progress(idx + 1, total, "done")

    return (primary_acq, result_img)


def fetch_true_color_frames(
    params: FetchParams,
    *,
    on_progress: object | None = None,
) -> list[tuple[datetime, Image.Image]]:
    """
    For each frame, find all Sentinel-2 tiles that overlap the AOI,
    reproject them to a common WGS84 grid, and mosaic them together.

    All tiles are reprojected to the same geographic grid, so they
    align regardless of their native UTM zone.

    Optimisations:
    - Parallel frame fetching via ThreadPoolExecutor
    - Scene-level band caching
    - Reduced search windows
    """
    if params.num_frames < 1:
        raise ValueError("num_frames must be >= 1")

    _clear_band_cache()

    # Compute physically correct output dimensions to avoid distortion
    out_w, out_h = _compute_output_dims(params)

    anchors = iter_frame_anchor_datetimes(params.start_date, params.end_date, params.num_frames)
    stac_url = params.stac_url or os.getenv("STAC_URL") or "https://earth-search.aws.element84.com/v1"
    client = Client.open(stac_url)
    intersects = _wgs84_polygon(params)
    bounds = (params.top_left_lon, params.bottom_right_lat, params.bottom_right_lon, params.top_left_lat)

    total = len(anchors)
    frames: list[tuple[datetime, Image.Image]] = []

    def _progress_wrapper(current: int, total: int, status: str) -> None:
        if on_progress is not None and callable(on_progress):
            on_progress(current, total, status)

    # Parallel frame fetching
    max_workers = min(params.max_workers, total)

    if max_workers <= 1 or total <= 1:
        # Sequential for small jobs
        for idx, anchor in enumerate(anchors):
            result = _fetch_single_frame(
                params, client, intersects, bounds, anchor,
                out_w, out_h, idx, total, on_progress=_progress_wrapper,
            )
            if result is not None:
                frames.append(result)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _fetch_single_frame,
                    params, client, intersects, bounds, anchor,
                    out_w, out_h, idx, total, on_progress=_progress_wrapper,
                ): idx
                for idx, anchor in enumerate(anchors)
            }

            results: dict[int, tuple[datetime, Image.Image] | None] = {}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    results[idx] = None
                    if on_progress is not None and callable(on_progress):
                        on_progress(idx + 1, total, f"error: {e}")

            for idx in range(total):
                result = results.get(idx)
                if result is not None:
                    frames.append(result)

    stats = _get_band_cache_stats()
    if stats["hits"] > 0:
        print(f"  [cache] Band cache: {stats['hits']} hits, {stats['misses']} misses "
              f"({stats['hits']/(stats['hits']+stats['misses'])*100:.0f}% hit rate)")

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