"""
Satellite timelapse launcher. Opens a GUI dialog for input.
Coordinate entry now uses an interactive world map where you click
and drag to select the area of interest (4:3 aspect ratio).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from tkinter import Tk, Label, Entry, Button, messagebox, StringVar, scrolledtext, Frame

from dotenv import load_dotenv

load_dotenv()


def launch_gui() -> None:
    root = Tk()
    root.title("Satellite Timelapse Generator")
    root.configure(bg="#1a1a2e")

    # ── Coordinate frame with map button ──────────────────────────
    coord_frame = Frame(root, bg="#1a1a2e")
    coord_frame.grid(row=0, column=0, columnspan=2, padx=5, pady=5, sticky="ew")

    coords_var = StringVar(value="Click 'Select on Map' to choose area")
    coord_label = Label(
        coord_frame, textvariable=coords_var,
        font=("Segoe UI", 10), fg="#aaaaaa", bg="#1a1a2e",
        wraplength=350, justify="left",
    )
    coord_label.pack(side="left", padx=(5, 10), pady=5)

    # Hidden coordinate holders
    tl_lat_var = StringVar(value="51.55")
    tl_lon_var = StringVar(value="-0.55")
    br_lat_var = StringVar(value="51.35")
    br_lon_var = StringVar(value="-0.25")

    def open_map_selector() -> None:
        from app.map_selector import Bbox, launch_map_selector
        bbox = launch_map_selector()
        if bbox.lat_min == -90.0 and bbox.lat_max == 90.0 and bbox.lon_min == -180.0 and bbox.lon_max == 180.0:
            return  # Cancelled
        tl_lat_var.set(f"{bbox.lat_max:.4f}")
        tl_lon_var.set(f"{bbox.lon_min:.4f}")
        br_lat_var.set(f"{bbox.lat_min:.4f}")
        br_lon_var.set(f"{bbox.lon_max:.4f}")
        coords_var.set(
            f"Area selected: ({bbox.lat_max:.3f}°, {bbox.lon_min:.3f}°) → ({bbox.lat_min:.3f}°, {bbox.lon_max:.3f}°) "
            f"[4:3 aspect]"
        )
        coord_label.configure(fg="#4ade80")

    tk.Button(
        coord_frame, text="🗺  Select on Map", command=open_map_selector,
        font=("Segoe UI", 11, "bold"), bg="#2d6a4f", fg="white",
        padx=15, pady=4, relief="flat", cursor="hand2",
    ).pack(side="right", padx=5)

    # ── Remaining text fields ─────────────────────────────────────
    fields = [
        ("Start date (YYYY-MM-DD)", "2023-04-01"),
        ("End date (YYYY-MM-DD)", "2026-04-01"),
        ("Number of frames", "20"),
        ("Max cloud cover (0-1)", "0.05"),
        ("Frames per second", "4.0"),
        ("Frame width (px)", "512"),
        ("Frame height (px)", "512"),
    ]

    vars = {}
    input_widgets = []
    for i, (label, default) in enumerate(fields):
        lbl = Label(root, text=label, bg="#1a1a2e", fg="#e0e0e0", font=("Segoe UI", 10))
        lbl.grid(row=i + 1, column=0, sticky="w", padx=10, pady=2)
        var = StringVar(value=default)
        entry = Entry(root, textvariable=var, width=25, font=("Segoe UI", 10))
        entry.grid(row=i + 1, column=1, padx=10, pady=2, sticky="ew")
        vars[label] = var
        input_widgets.extend([lbl, entry])

    btn = Button(
        root, text="🚀 Generate GIF", width=20,
        font=("Segoe UI", 11, "bold"), bg="#2563eb", fg="white",
        relief="flat", cursor="hand2",
    )
    btn.grid(row=len(fields) + 1, column=0, columnspan=2, pady=12)

    progress = scrolledtext.ScrolledText(
        root, height=15, width=70, state="disabled",
        bg="#0d1117", fg="#c9d1d9", font=("Consolas", 9),
        insertbackground="white",
    )

    def log(msg: str) -> None:
        progress.configure(state="normal")
        progress.insert("end", msg + "\n")
        progress.see("end")
        progress.configure(state="disabled")
        print(msg)

    def start_generation(opts: dict) -> None:
        """Called via after(0) to run outside button callback."""
        from app.timelapse import NoImageryError, TimelapseParams, generate_timelapse_gif

        log("Starting GIF generation...")

        params = TimelapseParams(
            top_left_lat=opts["tl_lat"], top_left_lon=opts["tl_lon"],
            bottom_right_lat=opts["br_lat"], bottom_right_lon=opts["br_lon"],
            start_date=date.fromisoformat(opts["start"]),
            end_date=date.fromisoformat(opts["end"]),
            num_frames=opts["frames"], max_cloud_coverage=opts["max_cloud"],
            width=opts["width"], height=opts["height"],
        )

        log("Fetching satellite imagery... (this may take a minute)")
        root.update()

        try:
            out = generate_timelapse_gif(params, Path("out.gif").resolve(), fps=opts["fps"], boomerang=False)
            log(f"Done! GIF saved to: {out.as_posix()}")
        except NoImageryError as e:
            log(f"ERROR: No imagery available - {e}")
        except Exception as e:
            log(f"ERROR: {e}")

        Button(root, text="Close", command=lambda: (root.quit(), root.destroy()),
               font=("Segoe UI", 10), bg="#555555", fg="white",
               relief="flat", cursor="hand2").pack(pady=10)
        root.update()

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

            # Switch to progress view
            btn.grid_remove()
            coord_frame.grid_remove()
            for w in input_widgets:
                w.grid_remove()
            progress.pack(padx=10, pady=10, fill="both", expand=True)
            root.update()

            opts = dict(
                tl_lat=tl_lat, tl_lon=tl_lon,
                br_lat=br_lat, br_lon=br_lon,
                start=start, end=end,
                frames=frames, max_cloud=max_cloud,
                fps=fps, width=width, height=height,
            )

            # Schedule generation after event loop returns
            root.after(100, lambda: start_generation(opts))

        except ValueError as e:
            messagebox.showerror("Input Error", f"Invalid input: {e}")

    btn.configure(command=on_submit)
    root.mainloop()


def main() -> int:
    if len(sys.argv) > 1:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("-o", "--output", type=Path, default=Path("timelapse.gif"))
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
        out = generate_timelapse_gif(params, args.output.resolve(), fps=args.fps, boomerang=not args.no_boomerang)
        print(out.as_posix())
        return 0

    launch_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())