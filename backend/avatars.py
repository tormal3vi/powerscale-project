"""Profile pictures: whatever a user uploads is decoded and re-encoded here
into one small square WebP. Only images this module produced are ever
stored or served - never the uploaded bytes - so a file that merely claims
to be an image (SVG with script, HTML, a polyglot) can't come back out,
and camera metadata (EXIF GPS etc.) is dropped.

Animated GIFs and WebPs stay animated: smaller, with at most MAX_FRAMES
frames, and shrunk further until they fit in MAX_ANIMATED_BYTES (they're
shown in lists next to many others)."""

from io import BytesIO
from typing import List

from PIL import Image, ImageOps

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # stills are downscaled by the page first; GIFs arrive as they are
MAX_PIXELS = 40_000_000              # refuse decompression bombs before decoding
MAX_FRAME_PIXELS = 400_000_000       # width x height x frames, for animations
SIZE = 256                           # stills: SIZE x SIZE; shown at 24-96px
MAX_FRAMES = 120
MAX_ANIMATED_BYTES = 600 * 1024
# (size, quality, keep every nth frame) - tried in order until one fits
ANIMATED_STEPS = ((160, 75, 1), (128, 65, 1), (128, 60, 2), (96, 55, 2), (96, 50, 3))
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}


class BadImage(ValueError):
    """The upload isn't an image we accept; the message is shown to the user."""


def _square(frame: Image.Image, size: int) -> Image.Image:
    return ImageOps.fit(frame.convert("RGBA"), (size, size), Image.Resampling.LANCZOS)


def _animated(img: Image.Image) -> bytes:
    frames = img.n_frames
    if img.width * img.height * frames > MAX_FRAME_PIXELS:
        raise BadImage("That animation is too large - try a shorter or smaller one")
    # Every frame in full (GIF frames can be partial updates of the last),
    # with its duration; long ones are thinned out evenly.
    stride = max(1, -(-frames // MAX_FRAMES))
    picked: List[Image.Image] = []
    durations: List[int] = []
    for i in range(frames):
        img.seek(i)
        duration = int(img.info.get("duration") or 100)
        if i % stride == 0:
            picked.append(img.convert("RGBA"))
            durations.append(duration)
        else:
            durations[-1] += duration
    for size, quality, every in ANIMATED_STEPS:
        frames_out, times = [], []
        for i in range(0, len(picked), every):
            frames_out.append(_square(picked[i], size))
            times.append(max(20, sum(durations[i:i + every])))
        out = BytesIO()
        frames_out[0].save(out, "WEBP", save_all=True, append_images=frames_out[1:], duration=times,
                           loop=0, quality=quality, method=4)
        if out.tell() <= MAX_ANIMATED_BYTES:
            return out.getvalue()
    raise BadImage("That animation is too long to use - try a shorter one")


def process(data: bytes) -> bytes:
    try:
        with Image.open(BytesIO(data)) as img:
            if img.format not in ALLOWED_FORMATS:
                raise BadImage("Use a JPG, PNG, WebP or GIF image")
            width, height = img.size  # read from the header, before any decoding
            if width * height > MAX_PIXELS:
                raise BadImage("That image is too large - try a smaller one")
            if getattr(img, "n_frames", 1) > 1:
                return _animated(img)
            if len(data) > 5 * 1024 * 1024:
                raise BadImage("That image is over 5 MB")
            img.seek(0)
            upright = ImageOps.exif_transpose(img)  # phone photos store rotation in EXIF
            out = BytesIO()
            _square(upright, SIZE).save(out, "WEBP", quality=85, method=6)
            return out.getvalue()
    except BadImage:
        raise
    except Exception:  # noqa: BLE001 - any decoder failure means "not an image we can read"
        raise BadImage("That file isn't an image we can read")
