"""The still frame shown while the stream is paused."""

from PIL import Image, ImageDraw, ImageFilter

_BLUR_SCALE = 4
_DIM = 0.2
_BADGE_OPACITY = 0.6
# The pause transition, shared by the preview and the virtual camera.
PAUSE_IN_SECONDS = 0.16
PAUSE_OUT_SECONDS = 0.11
_BADGE_START_SCALE = 0.6


def paused_frame(image: Image.Image) -> Image.Image:
    """Blur and dim ``image`` and put a pause badge in its center.

    This is the picture the virtual camera shows, so everyone watching sees
    that the stream is paused. The preview draws the same badge itself, over
    :func:`paused_background`, to animate it.
    """
    return _with_badge(paused_background(image))


def ease_out(progress: float) -> float:
    """Fast start, soft landing: the curve of the pause transition."""
    progress = min(1.0, max(0.0, progress))
    return 1.0 - (1.0 - progress) ** 3


def transition_frame(image: Image.Image, background: Image.Image, amount: float) -> Image.Image:
    """One step of the pause animation for the virtual camera.

    ``amount`` runs from 0 (the plain picture) to 1 (the paused frame): the
    blur fades in while the badge grows and becomes opaque.
    """
    amount = min(1.0, max(0.0, amount))
    source = image.convert("RGB")
    if source.size != background.size:
        source = source.resize(background.size, Image.Resampling.BILINEAR)
    frame = Image.blend(source, background, amount)
    if amount <= 0.0:
        return frame
    return _with_badge(
        frame,
        scale=_BADGE_START_SCALE + (1.0 - _BADGE_START_SCALE) * amount,
        opacity=amount,
    )


def paused_background(image: Image.Image) -> Image.Image:
    """The blurred, dimmed still frame without the badge."""
    source = image.convert("RGB")
    width, height = source.size

    # Blurring a reduced copy is much cheaper and looks the same once enlarged.
    small = source.resize(
        (max(1, width // _BLUR_SCALE), max(1, height // _BLUR_SCALE)),
        Image.Resampling.BILINEAR,
    )
    small = small.filter(ImageFilter.GaussianBlur(radius=max(1.0, height / 160)))
    frame = small.resize((width, height), Image.Resampling.BICUBIC)
    return Image.blend(frame, Image.new("RGB", (width, height)), _DIM)


def _with_badge(frame: Image.Image, scale: float = 1.0, opacity: float = 1.0) -> Image.Image:
    width, height = frame.size
    # Badge geometry follows the mockup: an 88 px circle on a 720 px frame
    # holding two bars from a 30 px icon. Drawn at 4x for smooth edges.
    oversample = 4
    diameter = max(8, round(height * 88 / 720 * scale))
    size = diameter * oversample
    badge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(badge)
    draw.ellipse((0, 0, size - 1, size - 1), fill=(0, 0, 0, round(255 * _BADGE_OPACITY)))
    unit = size * (30 / 88) / 30
    left = (size - 30 * unit) / 2
    for bar_start in (6.0, 17.5):
        draw.rounded_rectangle(
            (
                left + bar_start * unit,
                left + 4 * unit,
                left + (bar_start + 6.5) * unit,
                left + 26 * unit,
            ),
            radius=1.5 * unit,
            fill=(255, 255, 255, 255),
        )
    badge = badge.resize((diameter, diameter), Image.Resampling.LANCZOS)
    if opacity < 1.0:
        badge.putalpha(badge.getchannel("A").point(lambda alpha: round(alpha * opacity)))
    frame.paste(badge, ((width - diameter) // 2, (height - diameter) // 2), badge)
    return frame
