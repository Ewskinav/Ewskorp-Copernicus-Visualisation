from __future__ import annotations

from datetime import date, datetime, timezone

from PIL import Image, ImageDraw, ImageFont, ImageOps


def normalize_frames(frames: list[tuple[datetime, Image.Image]]) -> list[tuple[datetime, Image.Image]]:
    """Make all frames the same size (largest width/height) using letterboxing."""
    if not frames:
        return []

    max_w = max(img.size[0] for _, img in frames)
    max_h = max(img.size[1] for _, img in frames)

    normalized: list[tuple[datetime, Image.Image]] = []
    for t, img in frames:
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        if img.size != (max_w, max_h):
            img = ImageOps.pad(img, (max_w, max_h), color=(0, 0, 0, 255), centering=(0.5, 0.5))
        normalized.append((t, img))
    return normalized


def format_acquisition_label(t: datetime | date) -> str:
    if isinstance(t, datetime):
        tt = t.astimezone(timezone.utc) if t.tzinfo else t.replace(tzinfo=timezone.utc)
        return tt.strftime("%Y-%m-%d %H:%M UTC")
    return t.isoformat()


def overlay_timestamp(img: Image.Image, t: datetime | date) -> Image.Image:
    """Add acquisition time (UTC) label bottom-left."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    out = img.copy()
    draw = ImageDraw.Draw(out)
    label = format_acquisition_label(t)

    font = ImageFont.load_default()

    bar_height = 6
    gap = 4
    padding = 8
    text_bbox = draw.textbbox((0, 0), label, font=font)
    text_w = text_bbox[2] - text_bbox[0]
    text_h = text_bbox[3] - text_bbox[1]

    x = padding
    y = out.size[1] - bar_height - gap - text_h

    bg_margin = 6
    bg = (x - bg_margin, y - bg_margin, x + text_w + bg_margin, y + text_h + bg_margin)
    draw.rectangle(bg, fill=(0, 0, 0, 140))
    draw.text((x, y), label, fill=(255, 255, 255, 255), font=font)
    return out


def overlay_progress_bar(img: Image.Image, frame_index: int, total_frames: int) -> Image.Image:
    """Add a slim progress bar at the bottom of the image showing current position."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    out = img.copy()
    draw = ImageDraw.Draw(out)
    w, h = out.size

    bar_height = 6
    bar_y = h - bar_height

    # Background track
    draw.rectangle((0, bar_y, w, h), fill=(0, 0, 0, 180))

    # Filled progress
    if total_frames > 1:
        filled_w = int(w * (frame_index + 1) / total_frames)
    else:
        filled_w = w
    draw.rectangle((0, bar_y, filled_w, h), fill=(76, 175, 80, 220))

    return out


def process_frames(frames: list[tuple[datetime, Image.Image]]) -> list[Image.Image]:
    normalized = normalize_frames(frames)
    total = len(normalized)
    result: list[Image.Image] = []
    for i, (t, img) in enumerate(normalized):
        img = overlay_timestamp(img, t)
        img = overlay_progress_bar(img, i, total)
        result.append(img)
    return result