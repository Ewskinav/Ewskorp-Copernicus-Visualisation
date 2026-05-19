# Ewskorp-Copernicus-Visualisation

Command-line tool that **downloads Sentinel-2 satellite imagery** (public COGs via STAC) and builds an **animated GIF**: one frame per time step, played in order.

- **No HTTP server** — you run one Python file.
- **No paid API keys** — data comes from open Copernicus/Sentinel-2 archives (catalog + files over HTTPS).
- **Internet required** — the script queries a STAC API and reads cloud-optimized GeoTIFF bands.

## Layout

- **`make_timelapse.py`** — what you run (`python make_timelapse.py ...`).
- **`app/`** — fetch (`sentinel_fetcher.py`), resize/overlay (`image_processing.py`), assemble GIF (`timelapse.py`).

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Optional: copy `.env.example` to `.env` and set `STAC_URL` if you need a different catalog (default: Element84 Earth Search).

## Run

```bash
python make_timelapse.py -o out.gif --tl-lat 51.55 --tl-lon -0.55 --br-lat 51.35 --br-lon -0.25 --start 2024-04-01 --end 2024-06-01 --frames 9
```

- **`--frames N`** — **exactly N frames**. The span from the start of `--start` (00:00 UTC) through the end of `--end` (end of that day, UTC) is split into **N equal time positions**. For each position we search the catalog for the **closest-in-time** Sentinel-2 scene. If nothing matches yet, the search **expands** (symmetric windows, then forward in hours/days) and may **relax the cloud filter** so you still get an image. The label on each frame is the scene’s **actual acquisition time** (UTC).
- **Fewer than N frames** — only if a target still has **no** usable scene after all search steps (rare). Increase `--max-cloud` or widen `--end` if that happens.
- **`--max-cloud`** — preferred max scene cloud cover `0..1` (default `0.4`); relaxed automatically per target if needed.
- **`--width` / `--height`** — output frame size in pixels (default 512×512 if omitted).
- **`--fps`** — frames per second (default **0.2** ≈ **one frame every 5 seconds**). Internally this is converted to GIF delay in **milliseconds** (Pillow expects ms, not seconds).
- **Boomerang (default on)** — the GIF plays forward then backward (`… → last → … → first`) so the loop does not jump from last frame back to first. Pass **`--no-boomerang`** for a plain forward-only sequence.

Full options: `python make_timelapse.py --help`

Frames are ordered by **target time** (the N evenly spaced instants), each showing the **best available** nearby scene.
