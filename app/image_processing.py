from __future__ import annotations

from datetime import date, datetime, timezone

from PIL import Image, ImageDraw, ImageFont, ImageOps


def normalize_frames(frames: list[tuple[datetime, Image.Image]]) -> list[tuple[datetime, Image.Image]]:
    """
    Ensures all frames are the same size (largest width/height among frames) using letterboxing.
    """
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
    """Adds acquisition time (UTC) label bottom-left."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    out = img.copy()
    draw = ImageDraw.Draw(out)
    label = format_acquisition_label(t)

    font = ImageFont.load_default()

    padding = 8
    text_bbox = draw.textbbox((0, 0), label, font=font)
    text_w = text_bbox[2] - text_bbox[0]
    text_h = text_bbox[3] - text_bbox[1]

    x = padding
    y = out.size[1] - text_h - padding

    bg_margin = 6
    bg = (x - bg_margin, y - bg_margin, x + text_w + bg_margin, y + text_h + bg_margin)
    draw.rectangle(bg, fill=(0, 0, 0, 140))
    draw.text((x, y), label, fill=(255, 255, 255, 255), font=font)
    return out


def process_frames(frames: list[tuple[datetime, Image.Image]]) -> list[Image.Image]:
    normalized = normalize_frames(frames)
    return [overlay_timestamp(img, t) for t, img in normalized]
