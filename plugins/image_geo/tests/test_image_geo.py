from __future__ import annotations

from io import BytesIO

import httpx
from PIL import Image
from PIL.ExifTags import IFD

from app.core.enums import Assertion, TargetType
from app.providers.types import Target


def _jpeg_with_gps(lat_dms=(40, 26, 46.302), lat_ref="N", lon_dms=(79, 58, 55.998), lon_ref="W") -> bytes:
    img = Image.new("RGB", (20, 20), "blue")
    exif = img.getexif()
    exif[0x010F] = "TestCo"
    exif[0x0110] = "Model X"
    exif[0x0132] = "2026:05:01 12:00:00"
    gps = exif.get_ifd(IFD.GPSInfo)
    gps[1], gps[2], gps[3], gps[4] = lat_ref, lat_dms, lon_ref, lon_dms
    buf = BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def _jpeg_without_exif() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (10, 10), "green").save(buf, format="JPEG")
    return buf.getvalue()


TARGET = Target(type=TargetType.URL, value="https://cdn.test/a.jpg", normalized="https://cdn.test/a.jpg")


async def test_reports_coordinates_when_gps_exif_present(load_provider, make_ctx) -> None:
    data = _jpeg_with_gps()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=data, headers={"content-type": "image/jpeg"})

    observations = await load_provider("image_geo").search(TARGET, make_ctx(handler))
    assert len(observations) == 1
    result = observations[0]
    assert result.data["has_gps"] is True
    assert round(result.data["latitude"], 3) == 40.446
    assert round(result.data["longitude"], 3) == -79.982
    assert result.data["capture_device"] == "TestCo Model X"
    assert result.assertion is Assertion.OBSERVED
    assert result.confidence and result.confidence > 0.9


async def test_explains_absence_rather_than_reporting_bare_failure(load_provider, make_ctx) -> None:
    data = _jpeg_without_exif()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=data, headers={"content-type": "image/jpeg"})

    observations = await load_provider("image_geo").search(TARGET, make_ctx(handler))
    result = observations[0]
    assert result.data["has_exif"] is False
    assert result.data["has_gps"] is False
    # The reason must be legible on its own — a downstream UI shows exactly this string.
    assert "platforms strip EXIF" in result.data["note"]


async def test_exif_present_but_no_gps_tags(load_provider, make_ctx) -> None:
    img = Image.new("RGB", (10, 10), "red")
    exif = img.getexif()
    exif[0x0110] = "Model Y"
    buf = BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    data = buf.getvalue()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=data, headers={"content-type": "image/jpeg"})

    observations = await load_provider("image_geo").search(TARGET, make_ctx(handler))
    result = observations[0]
    assert result.data["has_exif"] is True
    assert result.data["has_gps"] is False
    assert "no GPS tags" in result.data["note"]


async def test_corrupt_image_is_reported_unverified_not_raised(load_provider, make_ctx) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not actually a jpeg" * 10, headers={"content-type": "image/jpeg"})

    observations = await load_provider("image_geo").search(TARGET, make_ctx(handler))
    assert len(observations) == 1
    assert observations[0].assertion is Assertion.UNVERIFIED
    assert observations[0].data["has_gps"] is False


async def test_oversized_image_is_rejected_gracefully(load_provider, make_ctx) -> None:
    """The SSRF guard's own byte cap trips first; the provider must still explain, not crash."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"\xff\xd8" + b"0" * (13 * 1024 * 1024), headers={"content-type": "image/jpeg"}
        )

    observations = await load_provider("image_geo").search(TARGET, make_ctx(handler))
    assert len(observations) == 1
    assert observations[0].assertion is Assertion.UNVERIFIED
    assert "size limit" in observations[0].data["error"]


async def test_non_image_response_is_ignored(load_provider, make_ctx) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not an image</html>", headers={"content-type": "text/html"})

    assert await load_provider("image_geo").search(TARGET, make_ctx(handler)) == []


async def test_404_yields_nothing(load_provider, make_ctx) -> None:
    assert await load_provider("image_geo").search(TARGET, make_ctx(lambda r: httpx.Response(404))) == []


async def test_out_of_range_gps_values_are_not_reported_as_valid(load_provider, make_ctx) -> None:
    """Malformed tag data (e.g. a corrupt DMS tuple) must never surface nonsense coords."""
    from app.social.exif import extract_gps_exif

    data = _jpeg_with_gps(lat_dms=(999, 0, 0), lat_ref="N")
    result = extract_gps_exif(data)
    assert result.has_gps is False, "a latitude outside [-90, 90] must be rejected, not reported"
