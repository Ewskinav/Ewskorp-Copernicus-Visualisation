from __future__ import annotations
from PIL import Image
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import imageio.v2 as imageio
import numpy as np

from .image_processing import process_frames
from .sentinel_fetcher import FetchParams, fetch_true_color_frames


@dataclass(frozen=True)
class TimelapseParams:
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


class NoImageryError(RuntimeError):
    pass


def generate_timelapse_gif(
    params: TimelapseParams,
    output_path: Path,
    fps: float = 0.2,
    boomerang: bool = True,
    on_progress: object | None = None,
) -> Path:
    fetch_params = FetchParams(
        top_left_lat=params.top_left_lat,
        top_left_lon=params.top_left_lon,
        bottom_right_lat=params.bottom_right_lat,
        bottom_right_lon=params.bottom_right_lon,
        start_date=params.start_date,
        end_date=params.end_date,
        num_frames=params.num_frames,
        max_cloud_coverage=params.max_cloud_coverage,
        width=params.width,
        height=params.height,
    )

    frames = fetch_true_color_frames(fetch_params, on_progress=on_progress)
    if not frames:
        raise NoImageryError("No imagery available for given parameters.")

    processed = process_frames(frames)
    if not processed:
        raise NoImageryError("No frames produced after processing.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Pillow's GIF writer expects duration in milliseconds per frame
    frame_duration_ms = max(20, int(round(1000.0 / max(0.001, float(fps)))))

    images = [np.asarray(img.convert("RGBA")) for img in processed]

    # Forward + reverse (omit duplicate junction frame) so the loop doesn't jump cut
    if boomerang and len(images) > 1:
        images = images + images[-2::-1]

    imageio.mimsave(
        output_path.as_posix(),
        images,
        format="GIF",
        duration=frame_duration_ms,
        loop=0,
    )
    return output_path