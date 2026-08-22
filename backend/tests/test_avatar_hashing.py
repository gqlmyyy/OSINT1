"""Perceptual avatar hashing: survives re-encoding, separates different pictures, stays safe.

Images are generated here rather than fixtured, so the test states the visual
relationship it is asserting instead of hiding it in a binary blob.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from app.identity import avatar


def render(draw_fn, *, size: tuple[int, int] = (256, 256), fmt: str = "PNG", **save) -> bytes:
    image = Image.new("RGB", size, "white")
    draw_fn(ImageDraw.Draw(image), size)
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **save)
    return buffer.getvalue()


def gradient_with_box(draw, size):
    width, height = size
    for x in range(width):
        draw.line([(x, 0), (x, height)], fill=(x % 256, 80, 160))
    draw.rectangle([width // 4, height // 4, width // 2, height // 2], fill="black")


def circles(draw, size):
    width, height = size
    draw.ellipse([10, 10, width - 10, height - 10], fill=(20, 200, 90))
    draw.ellipse([width // 3, height // 3, width // 2, height // 2], fill="white")


AVATAR = render(gradient_with_box)
OTHER = render(circles)


# -- the core property --------------------------------------------------------


def test_the_same_image_hashes_identically() -> None:
    assert avatar.perceptual_hash(AVATAR) == avatar.perceptual_hash(AVATAR)


def test_hash_has_the_documented_width() -> None:
    digest = avatar.perceptual_hash(AVATAR)
    assert len(digest) == avatar.BITS // 4
    int(digest, 16)  # must be valid hex


def test_rescaling_preserves_the_hash_match() -> None:
    """The whole point: platforms resize on upload and the match must survive it."""
    smaller = render(gradient_with_box, size=(64, 64))
    assert avatar.is_same_image_candidate(
        avatar.perceptual_hash(AVATAR), avatar.perceptual_hash(smaller)
    )


def test_recompression_preserves_the_hash_match() -> None:
    """A lossy re-encode is exactly what breaks a byte hash."""
    jpeg = render(gradient_with_box, fmt="JPEG", quality=60)
    assert avatar.is_same_image_candidate(
        avatar.perceptual_hash(AVATAR), avatar.perceptual_hash(jpeg)
    )


def test_a_byte_hash_would_have_missed_those_matches() -> None:
    """Justifies the feature: sha256 fails on the cases dHash catches."""
    import hashlib

    jpeg = render(gradient_with_box, fmt="JPEG", quality=60)
    assert hashlib.sha256(AVATAR).hexdigest() != hashlib.sha256(jpeg).hexdigest()
    assert avatar.is_same_image_candidate(
        avatar.perceptual_hash(AVATAR), avatar.perceptual_hash(jpeg)
    )


def test_different_pictures_do_not_match() -> None:
    distance = avatar.hamming_distance(
        avatar.perceptual_hash(AVATAR), avatar.perceptual_hash(OTHER)
    )
    assert distance > avatar.SAME_IMAGE_THRESHOLD, f"distance {distance} is too close"


def test_similarity_is_reported_as_a_fraction_of_bits() -> None:
    assert avatar.similarity(avatar.perceptual_hash(AVATAR), avatar.perceptual_hash(AVATAR)) == 1.0
    assert 0.0 <= avatar.similarity("0" * 16, "f" * 16) <= 1.0


# -- comparison edge cases ----------------------------------------------------


def test_mismatched_hash_lengths_raise_rather_than_return_zero() -> None:
    """A silent 0 would read as 'identical images' — the worst possible failure."""
    with pytest.raises(ValueError):
        avatar.hamming_distance("abcd", "abcdef12")


def test_non_hex_hashes_raise() -> None:
    with pytest.raises(ValueError):
        avatar.hamming_distance("zzzzzzzzzzzzzzzz", "0000000000000000")


def test_malformed_input_is_not_a_candidate_match() -> None:
    """The convenience wrapper must fail closed."""
    assert avatar.is_same_image_candidate("nonsense", "0000000000000000") is False
    assert avatar.is_same_image_candidate("", "") is False


# -- untrusted input ----------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [b"", b"not an image", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, b"GIF89a" + b"\xff" * 16],
)
def test_undecodable_payloads_raise_unsafe_image(payload: bytes) -> None:
    with pytest.raises(avatar.UnsafeImage):
        avatar.perceptual_hash(payload)


def test_oversized_images_are_refused_before_full_decode() -> None:
    """Independent of Pillow's own bomb guard."""
    original = avatar.MAX_PIXELS
    try:
        avatar.MAX_PIXELS = 100  # smaller than the 256x256 fixture
        with pytest.raises(avatar.UnsafeImage, match="too large"):
            avatar.perceptual_hash(AVATAR)
    finally:
        avatar.MAX_PIXELS = original


def test_a_truncated_image_raises_rather_than_crashing_a_scan() -> None:
    with pytest.raises(avatar.UnsafeImage):
        avatar.perceptual_hash(AVATAR[: len(AVATAR) // 3])


def test_greyscale_and_palette_images_are_handled() -> None:
    """Avatars arrive in every mode; none may raise for being unusual."""
    for mode in ("L", "P", "RGBA"):
        buffer = io.BytesIO()
        Image.new(mode, (64, 64), 0 if mode in ("L", "P") else (10, 20, 30, 255)).save(
            buffer, format="PNG"
        )
        assert len(avatar.perceptual_hash(buffer.getvalue())) == avatar.BITS // 4


# -- the safety boundary ------------------------------------------------------


def test_thresholds_leave_an_explicit_silent_band() -> None:
    """Between 'same image' and 'unrelated' the engine says nothing, by design."""
    assert avatar.SAME_IMAGE_THRESHOLD < avatar.UNRELATED_THRESHOLD


def test_module_performs_no_face_detection() -> None:
    """Policy test: this project excludes facial recognition outright."""
    from pathlib import Path

    source = (Path(avatar.__file__)).read_text().lower()
    for forbidden in ("face", "facial", "haar", "landmark", "biometric", "embedding"):
        # The docstring explains the exclusion; executable code must not mention them.
        import ast

        tree = ast.parse((Path(avatar.__file__)).read_text())
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ) and ast.get_docstring(node) is not None:
                node.body = node.body[1:]
        assert forbidden not in ast.unparse(tree).lower(), forbidden
    assert "face" in source, "the docstring should still explain why faces are excluded"
