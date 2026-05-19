"""
Interactive world map selector for choosing timelapse areas visually.

Renders a equirectangular-projection world map with simplified continent
outlines. The user draws a 4:3 aspect-ratio rectangle by clicking and
dragging on the map. The bounding coordinates are extracted automatically,
eliminating the need to type in lat/lon values.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from typing import Callable

from PIL import Image, ImageDraw, ImageTk

from .world_map import CONTINENTS

# Map geometry (equirectangular projection)
MAP_W = 900
MAP_H = 540  # 900 × 540 = 5:3 canvas (4:3 inside with max fit)
LAND_COLOR = (60, 130, 60)        # green land
SEA_COLOR = (18, 40, 70)          # dark navy
SELECTION_COLOR = (255, 200, 50)  # gold rectangle
SELECTION_FILL = (255, 200, 50, 40)  # semi-transparent fill
LAT_LON_LINE_COLOR = (50, 80, 110)
CENTER_CROSS_COLOR = (255, 255, 255, 120)


@dataclass
class Bbox:
    """Normalised bounding box (top-left lon/lat, bottom-right lon/lat)."""
    lon_min: float = -180.0
    lat_max: float = 90.0
    lon_max: float = 180.0
    lat_min: float = -90.0

    @property
    def top_left_lon(self) -> float: return self.lon_min
    @property
    def top_left_lat(self) -> float: return self.lat_max
    @property
    def bottom_right_lon(self) -> float: return self.lon_max
    @property
    def bottom_right_lat(self) -> float: return self.lat_min


def _lon_to_x(lon: float, width: int) -> int:
    """Equirectangular projection: longitude to pixel x."""
    return int(round((lon + 180.0) / 360.0 * width))


def _lat_to_y(lat: float, height: int) -> int:
    """Equirectangular projection: latitude to pixel y."""
    return int(round((90.0 - lat) / 180.0 * height))


def _x_to_lon(x: int, width: int) -> float:
    return (x / width) * 360.0 - 180.0


def _y_to_lat(y: int, height: int) -> float:
    return 90.0 - (y / height) * 180.0


def _render_map(width: int, height: int) -> Image.Image:
    """Render a static equirectangular world map with continent outlines."""
    img = Image.new("RGBA", (width, height), (*SEA_COLOR, 255))
    draw = ImageDraw.Draw(img)

    # Graticule (grid lines) every 30°
    for lon in range(-180, 181, 30):
        x = _lon_to_x(lon, width)
        draw.line([(x, 0), (x, height)], fill=LAT_LON_LINE_COLOR, width=1)
    for lat in range(-90, 91, 30):
        y = _lat_to_y(lat, height)
        draw.line([(0, y), (width, y)], fill=LAT_LON_LINE_COLOR, width=1)

    # Continents
    for polygon in CONTINENTS:
        if len(polygon) < 3:
            continue
        pixel_points = [(_lon_to_x(lon, width), _lat_to_y(lat, height)) for lon, lat in polygon]
        draw.polygon(pixel_points, fill=(*LAND_COLOR, 255), outline=(40, 100, 40))

    # Equator and prime meridian in lighter colour
    eq_y = _lat_to_y(0, height)
    draw.line([(0, eq_y), (width, eq_y)], fill=(100, 140, 180), width=1)
    pm_x = _lon_to_x(0, width)
    draw.line([(pm_x, 0), (pm_x, height)], fill=(100, 140, 180), width=1)

    return img.convert("RGBA")


def _compute_4_3_rect(
    cx: float, cy: float,
    width: int, height: int,
) -> tuple[float, float, float, float]:
    """
    Given a centre (cx, cy) in map pixel coords, return the tightest
    4:3 rectangle that fits within the map. Returns (x0, y0, x1, y1).
    """
    # Candidate half-dimensions
    hw = min(cx, width - cx, height * (4.0 / 3.0) * 0.5)
    hh = hw * (3.0 / 4.0)

    # Clamp to map bounds
    hw = min(hw, width - cx, cx)
    hh = min(hh, height - cy, cy)

    x0 = cx - hw
    y0 = cy - hh
    x1 = cx + hw
    y1 = cy + hh

    # If too small, just centre a minimal rect
    if x1 - x0 < 4 or y1 - y0 < 4:
        x0 = max(0, cx - 60)
        y0 = max(0, cy - 45)
        x1 = min(width, cx + 60)
        y1 = min(height, cy + 45)

    return (x0, y0, x1, y1)


class MapSelector(tk.Frame):
    """
    A Tkinter widget that displays an interactive world map.
    Click and drag to reposition the 4:3 selection rectangle.
    The current bounding box is available via .bbox.
    """

    def __init__(
        self,
        master: tk.Misc,
        map_width: int = MAP_W,
        map_height: int = MAP_H,
        initial_bbox: Bbox | None = None,
        on_change: Callable[[Bbox], None] | None = None,
        **kwargs,
    ):
        super().__init__(master, **kwargs)
        self.map_width = map_width
        self.map_height = map_height
        self.on_change = on_change

        # State
        self._drag_start: tuple[int, int] | None = None
        self._bbox = initial_bbox or Bbox(
            lon_min=-2.0,
            lat_max=52.0,
            lon_max=2.0,
            lat_min=50.0,
        )

        # Render base map
        self._base_img = _render_map(map_width, map_height)
        self._overlay = Image.new("RGBA", (map_width, map_height), (0, 0, 0, 0))
        self._tk_img: ImageTk.PhotoImage | None = None

        # Tkinter canvas
        self.canvas = tk.Canvas(
            self,
            width=map_width,
            height=map_height,
            highlightthickness=0,
            cursor="crosshair",
        )
        self.canvas.pack()

        # Bind events
        self.canvas.bind("<Button-1>", self._on_mouse_down)
        self.canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.canvas.bind("<Configure>", self._on_configure)

        self._redraw()
        self._report_bbox()

    # ── Public API ────────────────────────────────────────────────

    @property
    def bbox(self) -> Bbox:
        return self._bbox

    def set_bbox(self, bbox: Bbox) -> None:
        self._bbox = bbox
        self._redraw()
        self._report_bbox()

    def get_coords(self) -> dict:
        """Return the bounding coordinates as a dict for TimelapseParams."""
        return {
            "top_left_lon": self._bbox.lon_min,
            "top_left_lat": self._bbox.lat_max,
            "bottom_right_lon": self._bbox.lon_max,
            "bottom_right_lat": self._bbox.lat_min,
        }

    # ── Internal ──────────────────────────────────────────────────

    def _redraw(self) -> None:
        """Composite the base map + selection overlay and display."""
        self._overlay = Image.new("RGBA", (self.map_width, self.map_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(self._overlay)

        # Project bbox to pixels
        x0 = _lon_to_x(self._bbox.lon_min, self.map_width)
        y0 = _lat_to_y(self._bbox.lat_max, self.map_height)
        x1 = _lon_to_x(self._bbox.lon_max, self.map_width)
        y1 = _lat_to_y(self._bbox.lat_min, self.map_height)

        # Ensure ordering
        px0, px1 = min(x0, x1), max(x0, x1)
        py0, py1 = min(y0, y1), max(y0, y1)

        # Selection rectangle fill
        draw.rectangle([px0, py0, px1, py1], fill=SELECTION_FILL, outline=SELECTION_COLOR, width=3)

        # Corner markers
        for cx, cy in [(px0, py0), (px1, py0), (px0, py1), (px1, py1)]:
            draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(255, 255, 255))

        # Centre cross
        cx = (px0 + px1) // 2
        cy = (py0 + py1) // 2
        cs = 8
        draw.line([(cx - cs, cy), (cx + cs, cy)], fill=CENTER_CROSS_COLOR, width=2)
        draw.line([(cx, cy - cs), (cx, cy + cs)], fill=CENTER_CROSS_COLOR, width=2)

        # Composite
        composite = Image.alpha_composite(self._base_img, self._overlay)
        self._tk_img = ImageTk.PhotoImage(composite)
        self.canvas.create_image(0, 0, image=self._tk_img, anchor="nw")

    def _report_bbox(self) -> None:
        if self.on_change:
            self.on_change(self._bbox)

    def _move_rect_to(self, cx_px: float, cy_px: float) -> None:
        """Reposition the 4:3 rect so its centre is at (cx_px, cy_px)."""
        x0, y0, x1, y1 = _compute_4_3_rect(cx_px, cy_px, self.map_width, self.map_height)
        self._bbox = Bbox(
            lon_min=_x_to_lon(int(x0), self.map_width),
            lat_max=_y_to_lat(int(y0), self.map_height),
            lon_max=_x_to_lon(int(x1), self.map_width),
            lat_min=_y_to_lat(int(y1), self.map_height),
        )
        self._redraw()
        self._report_bbox()

    def _on_mouse_down(self, e: tk.Event) -> None:
        self._drag_start = (e.x, e.y)

    def _on_mouse_drag(self, e: tk.Event) -> None:
        if self._drag_start is None:
            return
        # Smooth movement
        self._move_rect_to(float(e.x), float(e.y))

    def _on_mouse_up(self, e: tk.Event) -> None:
        self._drag_start = None
        self._move_rect_to(float(e.x), float(e.y))

    def _on_configure(self, e: tk.Event | None = None) -> None:
        pass  # We keep fixed size for stability


# ── Standalone test / preview ─────────────────────────────────────

def launch_map_selector() -> Bbox:
    """
    Launch a standalone window with the map selector.
    Returns the selected Bbox after the user clicks "Confirm".
    """
    root = tk.Toplevel()
    root.title("Select Area on Map")
    root.resizable(False, False)

    result: list[Bbox] = []
    coords_var = tk.StringVar(value="Click and drag on the map to select an area")

    def on_change(bbox: Bbox) -> None:
        coords_var.set(
            f"Top-left: ({bbox.lat_max:.3f}°, {bbox.lon_min:.3f}°)  |  "
            f"Bottom-right: ({bbox.lat_min:.3f}°, {bbox.lon_max:.3f}°)"
        )

    def on_confirm() -> None:
        nonlocal result
        selector = map_frame  # type: ignore
        result.append(selector.bbox)
        root.destroy()

    map_frame = MapSelector(root, on_change=on_change)
    map_frame.pack(padx=10, pady=(10, 5))

    info_label = tk.Label(root, textvariable=coords_var, font=("Segoe UI", 10), fg="#cccccc", bg="#1a1a2e")
    info_label.pack(fill="x", padx=10, pady=(0, 5))

    btn_frame = tk.Frame(root, bg="#1a1a2e")
    btn_frame.pack(pady=(0, 10))

    tk.Button(
        btn_frame, text="Confirm Selection", command=on_confirm,
        font=("Segoe UI", 11), bg="#2d6a4f", fg="white",
        padx=20, pady=4, relief="flat", cursor="hand2",
    ).pack(side="left", padx=5)

    tk.Button(
        btn_frame, text="Cancel", command=root.destroy,
        font=("Segoe UI", 11), bg="#555555", fg="white",
        padx=20, pady=4, relief="flat", cursor="hand2",
    ).pack(side="left", padx=5)

    map_frame.wait_window()

    if not result:
        return Bbox()
    return result[0]