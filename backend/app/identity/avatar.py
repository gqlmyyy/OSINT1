"""Perceptual hashing for avatars: "the same picture", never "the same person".

Two accounts using the same profile picture is a real correlation signal — people reuse
avatars across platforms constantly. But an exact byte hash misses almost every real
case, because each platform re-encodes, resizes and re-compresses on upload, so the same
photograph arrives with a different sha256 everywhere.

This closes that gap with a **difference hash (dHash)**: downscale to a fixed grid,
compare each pixel to its right-hand neighbour, and emit one bit per comparison. The
result survives rescaling, re-compression and mild colour shifts, while two genuinely
different pictures diverge quickly.

Three deliberate limits:

* **It identifies an image, not a person.** A shared stock photo, a meme, a brand logo, a
  default avatar — all produce a match. The signal this feeds is therefore named
  "same image candidate", is not treated as direct evidence, and cannot gate the
  ``CONFIRMED`` band. See :mod:`app.correlation.signals`.
* **No facial recognition, ever.** This compares pixel-luminance structure. It does not
  detect, encode or match faces, and the project excludes that category outright — see
  the README's "Legal and ethical use".
* **Untrusted input.** Avatars are arbitrary bytes from the internet, so decoding reuses
  the same guards as EXIF parsing: structural verification, a hard pixel ceiling
  independent of Pillow's own bomb guard, and every failure normalised to one exception.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, UnidentifiedImageError

#: Grid width; dHash compares each pixel with its right neighbour, so a 9x8 grid yields
#: 8x8 = 64 comparisons = a 64-bit fingerprint.
HASH_SIZE = 8
BITS = HASH_SIZE * HASH_SIZE

#: Independent of Pillow's MAX_IMAGE_PIXELS, so a crafted header claiming a huge but
#: not-quite-bomb size still cannot force a large decode.
MAX_PIXELS = 40_000_000

#: Hamming distance at or below which two images are considered the same picture.
#: Chosen conservatively: re-encoding and rescaling typically move a dHash by 0-6 bits,
#: while unrelated images sit near 32 (random). Raising this trades false negatives for
#: false positives, which is the wrong direction for this project.
SAME_IMAGE_THRESHOLD = 8

#: Above this, treat the images as unrelated and emit no signal at all. The gap between
#: the two thresholds is deliberately left silent rather than reported as a weak match:
#: a "somewhat similar" avatar is not evidence of anything.
UNRELATED_THRESHOLD = 16


class UnsafeImage(ValueError):
    """The image could not be decoded safely. Callers need no Pillow-specific handling."""


def perceptual_hash(data: bytes) -> str:
    """Return a 16-character hex dHash of ``data``.

    Raises :class:`UnsafeImage` for anything that is not a decodable, sanely sized image.
    """
    if not data:
        raise UnsafeImage("empty image payload")
    try:
        # verify() consumes the file object, so structural checking and decoding need
        # separate streams.
        Image.open(BytesIO(data)).verify()
        image = Image.open(BytesIO(data))
        width, height = image.size
        if width * height > MAX_PIXELS:
            raise UnsafeImage(f"image is too large to process ({width}x{height})")
        grid = image.convert("L").resize(
            (HASH_SIZE + 1, HASH_SIZE), Image.Resampling.LANCZOS
        )
    except UnsafeImage:
        raise
    except (UnidentifiedImageError, OSError, ValueError, MemoryError) as exc:
        raise UnsafeImage(f"image could not be decoded: {type(exc).__name__}") from exc

    # `load()` rather than `getdata()`: the latter is deprecated in Pillow 12 and this
    # project builds with warnings as errors, so it would break on upgrade.
    pixels = grid.load()
    if pixels is None:  # pragma: no cover - Pillow returns None only for unloadable data
        raise UnsafeImage("image could not be loaded into memory")

    # The grid was converted to mode "L", so each pixel is a single luminance int.
    # Pillow's stubs type the accessor as possibly returning a tuple (true for RGB),
    # hence the explicit int() rather than a blanket type-ignore.
    bits = 0
    for row in range(HASH_SIZE):
        for column in range(HASH_SIZE):
            bits <<= 1
            if int(pixels[column, row]) > int(pixels[column + 1, row]):  # type: ignore[arg-type]
                bits |= 1
    return f"{bits:0{BITS // 4}x}"


def hamming_distance(left: str, right: str) -> int:
    """Bits that differ between two hex hashes.

    Raises :class:`ValueError` on malformed or mismatched input rather than returning a
    misleading distance — a silent 0 here would read as "identical images".
    """
    if len(left) != len(right):
        raise ValueError("cannot compare hashes of different lengths")
    try:
        return bin(int(left, 16) ^ int(right, 16)).count("1")
    except ValueError as exc:
        raise ValueError("hashes must be hexadecimal") from exc


def is_same_image_candidate(left: str, right: str) -> bool:
    """Whether two hashes are close enough to be the same picture."""
    try:
        return hamming_distance(left, right) <= SAME_IMAGE_THRESHOLD
    except ValueError:
        return False


def similarity(left: str, right: str) -> float:
    """Fraction of matching bits, for display. Not a probability of anything."""
    try:
        return round(1.0 - (hamming_distance(left, right) / BITS), 4)
    except ValueError:
        return 0.0


__all__ = [
    "BITS",
    "SAME_IMAGE_THRESHOLD",
    "UNRELATED_THRESHOLD",
    "UnsafeImage",
    "hamming_distance",
    "is_same_image_candidate",
    "perceptual_hash",
    "similarity",
]
