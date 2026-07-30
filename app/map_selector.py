"""
Interactive 3D globe selector for choosing timelapse areas.

Uses CesiumJS to display a high-resolution 3D globe in the user's
default web browser (Chrome, Edge, Firefox — all support WebGL).
A lightweight local server serves the HTML page, and coordinates
are returned via a POST endpoint.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any


@dataclass
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


# ─── Shared state for the HTTP handler ──────────────────────────

_selection_result: Bbox | None = None
_selection_event = threading.Event()
_html_dir: str | None = None


class _GlobeHandler(BaseHTTPRequestHandler):
    """Minimal HTTP server: serves static files + POST /api/submit."""

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "":
            self.path = "/globe_selector.html"
        file_path = Path(_html_dir or ".") / self.path.lstrip("/")
        if file_path.exists() and file_path.suffix in (".html", ".js", ".css", ".png", ".jpg"):
            content_type = {
                ".html": "text/html",
                ".js": "application/javascript",
                ".css": "text/css",
                ".png": "image/png",
                ".jpg": "image/jpeg",
            }.get(file_path.suffix, "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with open(file_path, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

    def do_POST(self) -> None:
        global _selection_result
        if self.path == "/api/submit":
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len)
            try:
                data = json.loads(body)
                result = Bbox(
                    lon_min=data["top_left_lon"],
                    lat_max=data["top_left_lat"],
                    lon_max=data["bottom_right_lon"],
                    lat_min=data["bottom_right_lat"],
                )
                _selection_result = result
                _selection_event.set()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok"}).encode())
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress HTTP log noise
        pass


def _find_free_port() -> int:
    """Find a free TCP port on localhost."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def launch_map_selector() -> Bbox:
    """
    Launch a 3D globe in the user's default web browser using CesiumJS.

    Returns the selected Bbox after the user clicks "Confirm".
    """
    global _html_dir, _selection_result, _selection_event

    _selection_result = None
    _selection_event.clear()

    # Locate the HTML file
    html_path = Path(__file__).parent / "globe_selector.html"
    if not html_path.exists():
        raise FileNotFoundError(
            f"Globe selector HTML not found at {html_path.resolve()}. "
            "Ensure app/globe_selector.html exists."
        )

    _html_dir = str(html_path.parent.resolve())

    # Start the HTTP server on a free port
    port = _find_free_port()
    server = HTTPServer(("127.0.0.1", port), _GlobeHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # Open the page in the user's default browser
    url = f"http://127.0.0.1:{port}/globe_selector.html"
    webbrowser.open(url, new=2)

    print(f"🌍 3D globe opened in your browser.")
    print(f"   If it doesn't appear, manually open: {url}")
    print("   Select an area on the globe, then click Confirm.")
    print("   Close the browser tab when done, or press Ctrl+C here to cancel.")

    # Wait for the result
    try:
        _selection_event.wait(timeout=None)
    except KeyboardInterrupt:
        print("\nSelection cancelled.")
        return Bbox(
            lon_min=-2.0,
            lat_max=52.0,
            lon_max=2.0,
            lat_min=50.0,
        )
    finally:
        server.shutdown()

    result = _selection_result
    if result is None:
        return Bbox(
            lon_min=-2.0,
            lat_max=52.0,
            lon_max=2.0,
            lat_min=50.0,
        )
    return result