"""
Satellite timelapse launcher.

Step 1: Enter coordinates directly (or look up a place name to auto-fill them).
Step 2: Fetches and generates the timelapse GIF.
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import date
from pathlib import Path
from tkinter import Tk, Label, Entry, Button, messagebox, StringVar, scrolledtext, Frame

from dotenv import load_dotenv

load_dotenv()


def _format_eta(current: int, total: int, now: float, start_time: float) -> str:
    """Estimated time remaining based on average time per completed frame."""
    elapsed = now - start_time
    if current <= 0 or current >= total:
        return ""
    avg_per_frame = elapsed / current
    remaining = avg_per_frame * (total - current)
    if remaining < 60:
        return f"~{remaining:.0f}s left"
    elif remaining < 3600:
        return f"~{remaining/60:.1f}min left"
    else:
        return f"~{remaining/3600:.1f}h left"


def launch_gui(initial_coords: dict | None = None) -> None:
    """Launch the parameter-entry GUI with optional pre-filled coordinates."""
    root = Tk()
    root.title("Satellite Timelapse Generator")
    root.configure(bg="#1a1a2e")

    # Coordinate frame
    coord_frame = Frame(root, bg="#1a1a2e")
    coord_frame.grid(row=0, column=0, columnspan=3, padx=5, pady=8, sticky="ew")

    coord_label_text = StringVar()
    coord_label = Label(
        coord_frame, textvariable=coord_label_text,
        font=("Segoe UI", 10), fg="#aaaaaa", bg="#1a1a2e",
        wraplength=400, justify="left",
    )
    coord_label.pack(side="left", padx=(5, 10), pady=5)

    # Hidden coordinate holders
    tl_lat_var = StringVar(value="51.55")
    tl_lon_var = StringVar(value="-0.55")
    br_lat_var = StringVar(value="51.35")
    br_lon_var = StringVar(value="-0.25")

    if initial_coords:
        tl_lat_var.set(f"{initial_coords['top_left_lat']:.4f}")
        tl_lon_var.set(f"{initial_coords['top_left_lon']:.4f}")
        br_lat_var.set(f"{initial_coords['bottom_right_lat']:.4f}")
        br_lon_var.set(f"{initial_coords['bottom_right_lon']:.4f}")
        coord_label_text.set(
            f"Area: ({initial_coords['top_left_lat']:.4f}°, {initial_coords['top_left_lon']:.4f}°) → "
            f"({initial_coords['bottom_right_lat']:.4f}°, {initial_coords['bottom_right_lon']:.4f}°)"
        )
        coord_label.configure(fg="#4ade80")
    else:
        coord_label_text.set("Enter coordinates below, or look up a place name")

    def do_geocode() -> None:
        """Look up a place name and fill coordinates."""
        place = geocode_var.get().strip()
        if not place:
            messagebox.showinfo("Geocode", "Enter a place name first.")
            return

        from app.map_preview import geocode_place
        print(f"Looking up '{place}'...")
        result = geocode_place(place)
        if result is None:
            messagebox.showerror("Geocode", f"Could not find '{place}'.")
            return

        # Fill coordinate fields with the bounding box
        lon_min, lat_min, lon_max, lat_max = result.bounding_box
        tl_lat_var.set(f"{lat_max:.4f}")
        tl_lon_var.set(f"{lon_min:.4f}")
        br_lat_var.set(f"{lat_min:.4f}")
        br_lon_var.set(f"{lon_max:.4f}")

        coord_label_text.set(
            f"📍 {result.display_name[:60]} — "
            f"({lat_max:.4f}°, {lon_min:.4f}°) → ({lat_min:.4f}°, {lon_max:.4f}°)"
        )
        coord_label.configure(fg="#4ade80")
        print(f"  → Filled bbox: lon[{lon_min:.4f}, {lon_max:.4f}]  lat[{lat_min:.4f}, {lat_max:.4f}]")

    # Buttons row
    btn_frame = Frame(root, bg="#1a1a2e")
    btn_frame.grid(row=1, column=0, columnspan=3, padx=5, pady=2, sticky="ew")

    geocode_var = StringVar()
    geocode_entry = Entry(
        btn_frame, textvariable=geocode_var, width=22,
        font=("Segoe UI", 10),
    )
    geocode_entry.pack(side="left", padx=3)
    geocode_entry.insert(0, "e.g. Lake Windermere")

    def clear_geocode(event=None):
        if geocode_var.get() == "e.g. Lake Windermere":
            geocode_var.set("")

    geocode_entry.bind("<FocusIn>", clear_geocode)

    Button(
        btn_frame, text="🔍 Look up", command=do_geocode,
        font=("Segoe UI", 10), bg="#6b21a8", fg="white",
        padx=10, pady=3, relief="flat", cursor="hand2",
    ).pack(side="left", padx=3)

    # Remaining text fields
    fields = [
        ("Start date (YYYY-MM-DD)", "2024-06-01"),
        ("End date (YYYY-MM-DD)", "2024-08-01"),
        ("Number of frames", "6"),
        ("Max cloud cover (0-1)", "0.4"),
        ("Frames per second", "1.0"),
        ("Frame width (px)", "512"),
        ("Frame height (px)", "512"),
    ]

    # Coordinate entry fields
    coord_fields = [
        ("Top-left latitude", tl_lat_var),
        ("Top-left longitude", tl_lon_var),
        ("Bottom-right latitude", br_lat_var),
        ("Bottom-right longitude", br_lon_var),
    ]

    vars = {}
    input_widgets = []
    row_offset = 2

    for i, (label, var) in enumerate(coord_fields):
        lbl = Label(root, text=label, bg="#1a1a2e", fg="#e0e0e0", font=("Segoe UI", 10))
        lbl.grid(row=row_offset + i, column=0, sticky="w", padx=10, pady=1)
        v = var
        entry = Entry(root, textvariable=v, width=25, font=("Segoe UI", 10))
        entry.grid(row=row_offset + i, column=1, columnspan=2, padx=10, pady=1, sticky="ew")
        input_widgets.extend([lbl, entry])

    for i, (label, default) in enumerate(fields):
        row = row_offset + len(coord_fields) + i
        lbl = Label(root, text=label, bg="#1a1a2e", fg="#e0e0e0", font=("Segoe UI", 10))
        lbl.grid(row=row, column=0, sticky="w", padx=10, pady=2)
        var = StringVar(value=default)
        entry = Entry(root, textvariable=var, width=25, font=("Segoe UI", 10))
        entry.grid(row=row, column=1, columnspan=2, padx=10, pady=2, sticky="ew")
        vars[label] = var
        input_widgets.extend([lbl, entry])

    btn = Button(
        root, text="🚀 Generate GIF", width=20,
        font=("Segoe UI", 11, "bold"), bg="#2d6a4f", fg="white",
        relief="flat", cursor="hand2",
    )
    btn.grid(row=row_offset + len(coord_fields) + len(fields) + 1, column=0, columnspan=3, pady=12)

    progress = scrolledtext.ScrolledText(
        root, height=15, width=80, state="disabled",
        bg="#0d1117", fg="#c9d1d9", font=("Consolas", 9),
        insertbackground="white",
    )

    def log(msg: str) -> None:
        """Must only be called from the main tkinter thread."""
        progress.configure(state="normal")
        progress.insert("end", msg + "\n")
        progress.see("end")
        progress.configure(state="disabled")

    def thread_safe_log(msg: str) -> None:
        """Schedule a log message on the main thread. Safe to call from any thread."""
        root.after(0, lambda: log(msg))
        print(msg)

    def _make_output_path(opts: dict) -> Path:
        """Build a descriptive output path in the output/ folder: output/{area}_{start_year}-{end_year}.gif"""
        mid_lat = (opts["tl_lat"] + opts["br_lat"]) / 2.0
        mid_lon = (opts["tl_lon"] + opts["br_lon"]) / 2.0
        lat_dir = "N" if mid_lat >= 0 else "S"
        lon_dir = "E" if mid_lon >= 0 else "W"
        area_str = f"{abs(mid_lat):.1f}{lat_dir}_{abs(mid_lon):.1f}{lon_dir}"

        start_year = opts["start"][:4]
        end_year = opts["end"][:4]
        if start_year == end_year:
            year_str = start_year
        else:
            year_str = f"{start_year}-{end_year}"

        output_dir = Path.cwd() / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        return (output_dir / f"{area_str}_{year_str}.gif").resolve()

    def start_generation(opts: dict) -> None:
        """Run GIF generation in a background thread so the GUI stays responsive."""
        from app.timelapse import NoImageryError, TimelapseParams, generate_timelapse_gif

        _est_start = time.time()

        def _on_progress(current: int, total: int, status: str) -> None:
            """Called from the background thread for each frame attempt."""
            now = time.time()

            if status == "downloading":
                thread_safe_log(f"  Frame {current}/{total}: downloading bands...")
            elif status == "done":
                est = _format_eta(current, total, now, _est_start)
                thread_safe_log(f"  Frame {current}/{total}: ✓ found  {est}")
            elif status == "no_match":
                est = _format_eta(current, total, now, _est_start)
                thread_safe_log(f"  Frame {current}/{total}: ✗ no matching scene  {est}")
            elif status == "low_coverage":
                est = _format_eta(current, total, now, _est_start)
                thread_safe_log(f"  Frame {current}/{total}: ✗ too much black area  {est}")
            elif status.startswith("error"):
                thread_safe_log(f"  Frame {current}/{total}: ✗ {status}")

        def _run() -> None:
            thread_safe_log("Starting GIF generation...")

            params = TimelapseParams(
                top_left_lat=opts["tl_lat"], top_left_lon=opts["tl_lon"],
                bottom_right_lat=opts["br_lat"], bottom_right_lon=opts["br_lon"],
                start_date=date.fromisoformat(opts["start"]),
                end_date=date.fromisoformat(opts["end"]),
                num_frames=opts["frames"], max_cloud_coverage=opts["max_cloud"],
                width=opts["width"], height=opts["height"],
            )

            try:
                output_path = _make_output_path(opts)
                out = generate_timelapse_gif(params, output_path, fps=opts["fps"], boomerang=False, on_progress=_on_progress)
                thread_safe_log(f"Done! GIF saved to: {out.as_posix()}")
            except NoImageryError as e:
                thread_safe_log(f"ERROR: No imagery available - {e}")
            except Exception as e:
                thread_safe_log(f"ERROR: {e}")
            finally:
                root.after(0, lambda: add_close_button())

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def add_close_button() -> None:
        Button(root, text="Close", command=lambda: (root.quit(), root.destroy()),
               font=("Segoe UI", 10), bg="#555555", fg="white",
        relief="flat", cursor="hand2").grid(row=row_offset + len(coord_fields) + len(fields) + 2, column=0, columnspan=3, pady=10)

    def on_submit() -> None:
        try:
            tl_lat = float(tl_lat_var.get())
            tl_lon = float(tl_lon_var.get())
            br_lat = float(br_lat_var.get())
            br_lon = float(br_lon_var.get())
            start = vars["Start date (YYYY-MM-DD)"].get()
            end = vars["End date (YYYY-MM-DD)"].get()
            frames = int(vars["Number of frames"].get())
            max_cloud = float(vars["Max cloud cover (0-1)"].get())
            fps = float(vars["Frames per second"].get())
            width = int(vars["Frame width (px)"].get())
            height = int(vars["Frame height (px)"].get())

            if tl_lat <= br_lat:
                messagebox.showerror("Error", "Top-left lat must be > bottom-right lat")
                return
            if tl_lon >= br_lon:
                messagebox.showerror("Error", "Top-left lon must be < bottom-right lon")
                return
            if start > end:
                messagebox.showerror("Error", "Start must be <= end")
                return
            if frames < 1:
                messagebox.showerror("Error", "Frames must be >= 1")
                return

            # Disable the button to prevent double-clicks
            btn.configure(state="disabled", text="⏳ Generating...")

            # Switch to progress view
            coord_frame.grid_remove()
            btn_frame.grid_remove()
            for w in input_widgets:
                w.grid_remove()
            progress.grid(row=row_offset + len(coord_fields) + len(fields) + 1, column=0, columnspan=3, padx=10, pady=10, sticky="nsew")
            root.grid_rowconfigure(row_offset + len(coord_fields) + len(fields) + 1, weight=1)
            root.grid_columnconfigure(0, weight=1)
            root.update()

            opts = dict(
                tl_lat=tl_lat, tl_lon=tl_lon,
                br_lat=br_lat, br_lon=br_lon,
                start=start, end=end,
                frames=frames, max_cloud=max_cloud,
                fps=fps, width=width, height=height,
            )

            # Start generation in a background thread
            root.after(100, lambda: start_generation(opts))

        except ValueError as e:
            messagebox.showerror("Input Error", f"Invalid input: {e}")

    btn.configure(command=on_submit)
    root.mainloop()


def _make_output_path_cli(tl_lat: float, tl_lon: float, br_lat: float, br_lon: float, start: date, end: date) -> Path:
    """Build a descriptive output path in the output/ folder."""
    mid_lat = (tl_lat + br_lat) / 2.0
    mid_lon = (tl_lon + br_lon) / 2.0
    lat_dir = "N" if mid_lat >= 0 else "S"
    lon_dir = "E" if mid_lon >= 0 else "W"
    area_str = f"{abs(mid_lat):.1f}{lat_dir}_{abs(mid_lon):.1f}{lon_dir}"

    start_year = str(start.year)
    end_year = str(end.year)
    if start_year == end_year:
        year_str = start_year
    else:
        year_str = f"{start_year}-{end_year}"

    output_dir = Path.cwd() / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    return (output_dir / f"{area_str}_{year_str}.gif").resolve()


def main() -> int:
    if len(sys.argv) > 1:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("-o", "--output", type=Path, default=None,
                            help="Output path (default: output/{area}_{years}.gif)")
        parser.add_argument("--tl-lat", type=float, required=True)
        parser.add_argument("--tl-lon", type=float, required=True)
        parser.add_argument("--br-lat", type=float, required=True)
        parser.add_argument("--br-lon", type=float, required=True)
        parser.add_argument("--start", type=date.fromisoformat, required=True)
        parser.add_argument("--end", type=date.fromisoformat, required=True)
        parser.add_argument("--frames", type=int, required=True)
        parser.add_argument("--max-cloud", type=float, default=0.4)
        parser.add_argument("--width", type=int, default=None)
        parser.add_argument("--height", type=int, default=None)
        parser.add_argument("--fps", type=float, default=0.2)
        parser.add_argument("--no-boomerang", action="store_true")
        args = parser.parse_args()

        from app.timelapse import NoImageryError, TimelapseParams, generate_timelapse_gif
        params = TimelapseParams(
            top_left_lat=args.tl_lat, top_left_lon=args.tl_lon,
            bottom_right_lat=args.br_lat, bottom_right_lon=args.br_lon,
            start_date=args.start, end_date=args.end,
            num_frames=args.frames, max_cloud_coverage=args.max_cloud,
            width=args.width, height=args.height,
        )

        if args.output is not None:
            output_path = args.output.resolve()
        else:
            output_path = _make_output_path_cli(args.tl_lat, args.tl_lon, args.br_lat, args.br_lon, args.start, args.end)

        out = generate_timelapse_gif(params, output_path, fps=args.fps, boomerang=not args.no_boomerang)
        print(out.as_posix())
        return 0

    # Interactive mode: GUI with coordinate entry + geocode lookup
    launch_gui(initial_coords=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())