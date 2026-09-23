"""Profile pictures: whatever a user uploads is decoded and re-encoded here
into one small square WebP. Only images this module produced are ever
stored or served - never the uploaded bytes - so a file that merely claims
to be an image (SVG with script, HTML, a polyglot) can't come back out,
and camera metadata (EXIF GPS etc.) is dropped."""

from io import BytesIO

from PIL import Image, ImageOps

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # the page downscales first; this is the backstop
MAX_PIXELS = 40_000_000             # refuse decompression bombs before decoding
SIZE = 256                          # stored as SIZE x SIZE; shown at 24-96px
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}


class BadImage(ValueError):
    """The upload isn't an image we accept; the message is shown to the user."""


def process(data: bytes) -> bytes:
    try:
        with Image.open(BytesIO(data)) as img:
            if img.format not in ALLOWED_FORMATS:
                raise BadImage("Use a JPG, PNG, WebP or GIF image")
            width, height = img.size  # read from the header, before any decoding
            if width * height > MAX_PIXELS:
                raise BadImage("That image is too large - try a smaller one")
            img.seek(0)  # first frame of an animated GIF/WebP
            upright = ImageOps.exif_transpose(img)  # phone photos store rotation in EXIF
            square = ImageOps.fit(upright.convert("RGBA"), (SIZE, SIZE), Image.Resampling.LANCZOS)
            out = BytesIO()
            square.save(out, "WEBP", quality=85, method=6)
            return out.getvalue()
    except BadImage:
        raise
    except Exception:  # noqa: BLE001 - any decoder failure means "not an image we can read"
        raise BadImage("That file isn't an image we can read")
