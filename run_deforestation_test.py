"""
Script to test deforestation GIF generation with detailed logging.
Runs sequentially with more verbose output to diagnose what's happening.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from app.sentinel_fetcher import FetchParams, fetch_true_color_frames
from app.timelapse import generate_timelapse_gif, TimelapseParams
from PIL import Image
import numpy as np
import imageio.v2 as imageio

# === Test configurations for deforestation hotspots ===
# Each tuple: (name, tl_lat, tl_lon, br_lat, br_lon, start_date, end_date, frames, cloud_max)
tests = [
    # 1. Rondônia / Amazon deforestation arc (infamous "deforestation arch")
    #    Try during the dry season window (May-Sep) over multiple years
    ("Rondonia_DrySeason", -10.5, -63.0, -12.5, -61.0, date(2016, 6, 1), date(2025, 9, 1), 20, 0.05),
    
    # 2. Mato Grosso - agriculture/soy deforestation, pronounced dry season
    ("MatoGrosso", -12.0, -55.5, -14.0, -53.5, date(2016, 1, 1), date(2025, 1, 1), 20, 0.05),
    
    # 3. Chaco region, Paraguay/Bolivia - semi-arid, less cloud cover
    ("Paraguay_Chaco", -19.0, -61.0, -22.0, -58.5, date(2016, 1, 1), date(2025, 1, 1), 20, 0.05),
    
    # 4. Sumatra, Indonesia - palm oil deforestation
    ("Sumatra", -2.0, 101.0, -4.0, 103.0, date(2016, 1, 1), date(2025, 1, 1), 20, 0.05),
    
    # 5. Borneo (Kalimantan) - palm oil deforestation, fire-related clearing
    ("Borneo", -1.0, 112.0, -3.0, 114.0, date(2016, 1, 1), date(2025, 1, 1), 20, 0.05),
    
    # 6. Mato Grosso - tighter area, specific deforestation hotspot  
    ("MatoGrosso_Rio", -11.5, -54.0, -13.0, -52.5, date(2016, 1, 1), date(2025, 1, 1), 20, 0.05),

    # 7. Mato Grosso - even more focused on deforestation arc
    ("AmazonArc", -8.0, -51.0, -10.0, -49.0, date(2016, 1, 1), date(2025, 1, 1), 20, 0.05),
]

if __name__ == "__main__":
    output_dir = Path.cwd() / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Run tests in order, but stop after first success  
    for name, tl_lat, tl_lon, br_lat, br_lon, start, end, frames, cloud_max in tests:
        print(f"\n{'='*60}")
        print(f"TEST: {name}")
        print(f"  Area: ({tl_lat}, {tl_lon}) → ({br_lat}, {br_lon})")
        print(f"  Dates: {start} to {end}")
        print(f"  Frames: {frames}, Max Cloud: {cloud_max}")
        print(f"{'='*60}")
        
        # First do a quick count to see how many frames we might get
        fetch_params = FetchParams(
            top_left_lat=tl_lat, top_left_lon=tl_lon,
            bottom_right_lat=br_lat, bottom_right_lon=br_lon,
            start_date=start, end_date=end,
            num_frames=frames, max_cloud_coverage=cloud_max,
            width=600, height=600,
            max_workers=1,  # Sequential for easier debugging
        )
        
        # Use the CLI-style direct call
        output_path = output_dir / f"{name}_deforestation.gif"
        
        try:
            from app.timelapse import NoImageryError
            
            params = TimelapseParams(
                top_left_lat=tl_lat, top_left_lon=tl_lon,
                bottom_right_lat=br_lat, bottom_right_lon=br_lon,
                start_date=start, end_date=end,
                num_frames=frames, max_cloud_coverage=cloud_max,
                width=600, height=600,
            )
            
            out = generate_timelapse_gif(
                params, output_path, fps=3, boomerang=False, on_progress=None
            )
            
            # Check the result
            img = Image.open(out)
            n_frames = getattr(img, 'n_frames', 1)
            print(f"  ✓ SUCCESS: {n_frames} frames → {out}")
            print(f"    File size: {out.stat().st_size / 1024:.0f} KB")
            
            if n_frames >= 15:
                print(f"  ✓ GOOD candidate for website!")
                break
            else:
                print(f"  ✗ Only {n_frames} frames, trying next location...")
        except NoImageryError as e:
            print(f"  ✗ No imagery: {e}")
        except Exception as e:
            print(f"  ✗ Error: {e}")
    else:
        print("\n" + "="*60)
        print("All tests failed. Trying with relaxed cloud cover...")
        print("="*60)
        
        # Retry the best candidates with relaxed cloud
        for name, tl_lat, tl_lon, br_lat, br_lon, start, end, frames, cloud_max in tests[:3]:
            for relaxed_cloud in [0.10, 0.15, 0.20, 0.30]:
                print(f"\n  RETRY: {name} with cloud={relaxed_cloud}")
                output_path = output_dir / f"{name}_cloud{relaxed_cloud:.0f}_deforestation.gif"
                try:
                    params = TimelapseParams(
                        top_left_lat=tl_lat, top_left_lon=tl_lon,
                        bottom_right_lat=br_lat, bottom_right_lon=br_lon,
                        start_date=start, end_date=end,
                        num_frames=frames, max_cloud_coverage=relaxed_cloud,
                        width=600, height=600,
                    )
                    out = generate_timelapse_gif(
                        params, output_path, fps=3, boomerang=False, on_progress=None
                    )
                    img = Image.open(out)
                    n_frames = getattr(img, 'n_frames', 1)
                    print(f"    ✓ {n_frames} frames → {out}")
                    if n_frames >= 15:
                        print(f"    ✓ GOOD candidate!")
                        break
                except (NoImageryError, Exception) as e:
                    print(f"    ✗ {e}")