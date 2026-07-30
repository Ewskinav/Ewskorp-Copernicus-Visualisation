"""
Interactive 2D Mercator map selector for choosing timelapse areas.

Uses Cartopy + matplotlib to display a real map with Natural Earth
coastlines, country borders, rivers and lakes at 1:10m scale.
The map is fully zoomable (scroll wheel, rubber-band zoom, pan)
via the matplotlib toolbar — detail remains crisp because the data
is vector-based Shapefile data, not raster tiles.

The user clicks two opposite corners to draw a selection rectangle,
then closes the window to confirm. The rectangle follows the map
during zoom/pan orientation changes.

The returned Bbox has the same interface as map_selector.py, making
it a drop-in replacement.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np


@dataclasses.dataclass
class Bbox:
    """Normalised bounding box (top-left lon/lat, bottom-right lon/lat)."""
    lon_min: float = -180.0
    lat_max: float = 90.0
    lon_max: float = 180.0
    lat_min: float = -90.0

    @property
    def top_left_lon(self) -> float:
        return self.lon_min

    @property
    def top_left_lat(self) -> float:
        return self.lat_max

    @property
    def bottom_right_lon(self) -> float:
        return self.lon_max

    @property
    def bottom_right_lat(self) -> float:
        return self.lat_min


# ─── Rectangle selector ────────────────────────────────────────

class _RectSelector:
    """
    Rectangle selector for a Cartopy GeoAxes on a Mercator projection.

    Coordinates from mouse events are in the projection's native system
    (Mercator metres).  They are transformed back to PlateCarree
    (lon/lat degrees) before being exposed via the ``result`` property.

    Zooming / panning is handled by the matplotlib toolbar — the drawn
    rectangle follows the view because it's drawn in **data** coordinates
    (Mercator), but the returned Bbox is always in lon/lat.
    """

    def __init__(self, ax) -> None:
        self.ax = ax
        self._fig = ax.figure
        self._cids: list[Any] = []
        self._rect_patch: Rectangle | None = None
        self._p1_data: tuple[float, float] | None = None  # Mercator coords
        self._p2_data: tuple[float, float] | None = None  # Mercator coords
        self._completed = False

        self._cids.append(self._fig.canvas.mpl_connect("button_press_event", self._on_press))
        self._cids.append(self._fig.canvas.mpl_connect("motion_notify_event", self._on_motion))
        self._cids.append(self._fig.canvas.mpl_connect("key_press_event", self._on_key))
        self._cids.append(self._fig.canvas.mpl_connect("draw_event", self._on_draw))

    def _click_to_lonlat(self, x_data: float, y_data: float) -> tuple[float, float]:
        """Convert axes data coordinates (Mercator metres) → (lon, lat) degrees."""
        return ccrs.PlateCarree().transform_point(x_data, y_data, self.ax.projection)

    def _on_press(self, event) -> None:
        if event.inaxes != self.ax or event.button not in (1, 3):
            return
        if event.button == 3:  # Right-click → cancel
            self._cancel()
            return

        # Left click
        if self._p1_data is None:
            self._p1_data = (event.xdata, event.ydata)
            self._p2_data = None
            self._completed = False
            self._draw_rect(self._p1_data, self._p1_data)
        else:
            self._p2_data = (event.xdata, event.ydata)
            self._draw_rect(self._p1_data, self._p2_data)
            self._completed = True

    def _on_motion(self, event) -> None:
        if event.inaxes != self.ax:
            return
        if self._p1_data is not None and not self._completed:
            self._draw_rect(self._p1_data, (event.xdata, event.ydata))

    def _on_key(self, event) -> None:
        if event.key == "escape":
            self._cancel()

    def _on_draw(self, event) -> None:
        """Redraw the rectangle after zoom/pan so it stays visible."""
        if self._p1_data is not None and self._p2_data is not None:
            self._draw_rect(self._p1_data, self._p2_data)
        elif self._p1_data is not None:
            self._draw_rect(self._p1_data, self._p1_data)

    def _draw_rect(self, p1: tuple[float, float], p2: tuple[float, float]) -> None:
        if self._rect_patch is not None:
            self._rect_patch.remove()
            self._rect_patch = None

        x1, y1 = p1
        x2, y2 = p2
        x_min, x_max = min(x1, x2), max(x1, x2)
        y_min, y_max = min(y1, y2), max(y1, y2)

        self._rect_patch = Rectangle(
            (x_min, y_min), x_max - x_min, y_max - y_min,
            linewidth=2, edgecolor="#ffcc00", facecolor="yellow", alpha=0.2,
            transform=self.ax.projection,  # Data coords = Mercator projection
        )
        self.ax.add_patch(self._rect_patch)
        self._fig.canvas.draw_idle()

    def _cancel(self) -> None:
        self._p1_data = None
        self._p2_data = None
        self._completed = False
        if self._rect_patch is not None:
            self._rect_patch.remove()
            self._rect_patch = None
        self._fig.canvas.draw_idle()

    def disconnect(self) -> None:
        for cid in self._cids:
            self._fig.canvas.mpl_disconnect(cid)

    @property
    def result(self) -> Bbox | None:
        if not self._completed or self._p1_data is None or self._p2_data is None:
            return None
        # Convert from Mercator metres → PlateCarree degrees
        lon1, lat1 = self._click_to_lonlat(*self._p1_data)
        lon2, lat2 = self._click_to_lonlat(*self._p2_data)
        lon_min = min(lon1, lon2)
        lon_max = max(lon1, lon2)
        lat_min = min(lat1, lat2)
        lat_max = max(lat1, lat2)
        return Bbox(
            lon_min=float(max(-180.0, min(180.0, lon_min))),
            lat_max=float(min(90.0, max(-90.0, lat_max))),
            lon_max=float(max(-180.0, min(180.0, lon_max))),
            lat_min=float(min(90.0, max(-90.0, lat_min))),
        )


# ─── Map building helpers ──────────────────────────────────────

def _build_map(ax) -> None:
    """Add Natural Earth 10m features to the given Cartopy GeoAxes."""
    ax.add_feature(cfeature.LAND, facecolor="#1a3a2a", edgecolor="none", zorder=1)
    ax.add_feature(cfeature.OCEAN, facecolor="#0a1628", edgecolor="none", zorder=1)

    # Coastlines at highest resolution
    ax.add_feature(
        cfeature.NaturalEarthFeature(
            "physical", "coastline", "10m",
            facecolor="none", edgecolor="#40916c", linewidth=0.6, zorder=3,
        )
    )
    # Country borders
    ax.add_feature(
        cfeature.NaturalEarthFeature(
            "cultural", "admin_0_boundary_lines_land", "10m",
            facecolor="none", edgecolor="#5a7a5a", linewidth=0.4, zorder=3,
        )
    )
    # Rivers
    ax.add_feature(
        cfeature.NaturalEarthFeature(
            "physical", "rivers_lake_centerlines", "10m",
            facecolor="none", edgecolor="#1a3a5a", linewidth=0.3, alpha=0.6, zorder=2,
        )
    )
    # Lakes
    ax.add_feature(
        cfeature.NaturalEarthFeature(
            "physical", "lakes", "10m",
            facecolor="#0a2a4a", edgecolor="#2a5a7a", linewidth=0.3, zorder=2,
        )
    )


# ─── Main entry point ──────────────────────────────────────────

def launch_map_selector() -> Bbox:
    """
    Launch an interactive 2D Mercator map in a matplotlib window.

    The map shows real coastlines, country borders, rivers and lakes
    at 1:10 million scale (Natural Earth vector data).  Because the
    features are vector-based Shapefile data, they remain perfectly
    crisp at **any zoom level** — no raster tiles needed.

    Zoom / Pan controls (matplotlib toolbar):
      - Scroll wheel to zoom in/out around mouse position
      - Middle-click + drag to pan
      - Left-click the magnifier icon, then rubber-band a region to zoom
      - Right-click the magnifier icon to zoom out
      - House icon to reset the view
      - Left/right arrow icons for back/forward navigation

    Selection workflow:
      1. Zoom/pan to your area of interest.
      2. Left-click once to set the first corner.
      3. Move the mouse to preview the rectangle.
      4. Left-click again to set the opposite corner and lock selection.
      5. Right-click or press Escape to cancel the current selection.
      6. Close the window when done.

    Returns
    -------
    Bbox
        Selected bounding box (lon/lat degrees).
        Defaults to London area if no valid selection was made.
    """
    projection = ccrs.Mercator(min_latitude=-80, max_latitude=84)
    fig, ax = plt.subplots(figsize=(14, 9), subplot_kw={"projection": projection})
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")

    _build_map(ax)

    # ── Initial view (centred on Europe, zoomed in a bit) ───────
    ax.set_extent([-15, 30, 35, 60], crs=ccrs.PlateCarree())

    # ── Gridlines with labels ────────────────────────────────────
    gl = ax.gridlines(
        crs=ccrs.PlateCarree(),
        draw_labels=True,
        linewidth=0.3,
        color="#333355",
        alpha=0.5,
        linestyle="--",
    )
    gl.top_labels = False
    gl.right_labels = False
    gl.xlabel_style = {"color": "#888888", "size": 7}
    gl.ylabel_style = {"color": "#888888", "size": 7}

    # ── Title and instructions ────────────────────────────────────
    ax.set_title("Select Area on Map", color="#e0e0e0", fontsize=13, pad=15)
    instruction_text = (
        "Use toolbar to zoom/pan  •  "
        "Left-click two corners to select area  •  "
        "Right-click or Escape to cancel  •  "
        "Close window to confirm coords"
    )
    ax.text(
        0.5, 1.01, instruction_text,
        transform=ax.transAxes, fontsize=7.5, color="#aaaaaa",
        ha="center", va="bottom",
    )

    # ── Attach selector ───────────────────────────────────────────
    selector = _RectSelector(ax)
    plt.tight_layout()

    try:
        plt.show(block=True)
    except Exception:
        pass

    selector.disconnect()

    result = selector.result
    if result is not None:
        print(f"Selected: {result}")
        return result

    print("No selection made. Using default area (London).")
    return Bbox(
        lon_min=-2.0,
        lat_max=52.0,
        lon_max=2.0,
        lat_min=50.0,
    )