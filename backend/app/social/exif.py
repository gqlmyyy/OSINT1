"""GPS EXIF extraction from a downloaded image.

Kept separate from the provider so the parsing logic — the part that touches untrusted
bytes — is small, reviewable, and testable without any network involved.

Most large platforms (Instagram, X, LinkedIn, Gravatar's resized variants) strip EXIF on
upload; that is expected and the caller must say so plainly rather than presenting an
empty result as a failure. A website hosting an original file, or a raw upload nobody
has resized, is where this actually finds something.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Any

from PIL import ExifTags, Image, UnidentifiedImageError
from PIL.ExifTags import GPS, IFD

#: Hard ceiling independent of Image.MAX_IMAGE_PIXELS, so a crafted header claiming a
#: huge-but-not-quite-bomb size still can't force a large decode.
MAX_PIXELS = 40_000_000  # ~40 MP: comfortably above any real avatar or post image
MAX_EXIF_STRING = 200


@dataclass
class GpsCoordinates:
    latitude: float
    longitude: float
    altitude_m: float | None = None


@dataclass
class ExifResult:
    has_exif: bool
    has_gps: bool
    coordinates: GpsCoordinates | None = None
    capture_device: str | None = None
    capture_datetime: datetime | None = None
    software: str | None = None
    width: int | None = None
    height: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "has_exif": self.has_exif,
            "has_gps": self.has_gps,
            "latitude": self.coordinates.latitude if self.coordinates else None,
            "longitude": self.coordinates.longitude if self.coordinates else None,
            "altitude_m": self.coordinates.altitude_m if self.coordinates else None,
            "capture_device": self.capture_device,
            "capture_datetime": self.capture_datetime.isoformat()
            if self.capture_datetime
            else None,
            "software": self.software,
            "width": self.width,
            "height": self.height,
        }


class UnsafeImage(ValueError):
    """The image failed a safety check before any EXIF parsing was attempted."""


def _dms_to_degrees(dms: tuple[Any, Any, Any], ref: str) -> float:
    degrees, minutes, seconds = (float(v) for v in dms)
    value = degrees + minutes / 60.0 + seconds / 3600.0
    return -value if ref in ("S", "W") else value


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    text = str(value).strip().strip("\x00")
    return text[:MAX_EXIF_STRING] or None


def _parse_datetime(value: Any) -> datetime | None:
    text = _clean_str(value)
    if not text:
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def extract_gps_exif(data: bytes) -> ExifResult:
    """Parse EXIF from raw image bytes. Never raises on malformed or absent EXIF."""
    if len(data) == 0:
        raise UnsafeImage("empty image payload")

    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()  # structural check; catches truncated/corrupt files early
        # verify() leaves the file object unusable for further reads, so reopen.
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            if width * height > MAX_PIXELS:
                raise UnsafeImage(
                    f"image exceeds the {MAX_PIXELS} pixel safety ceiling ({width}x{height})"
                )
            exif = image.getexif()
    except UnsafeImage:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        # Not a decodable image, or Pillow's own decompression-bomb guard tripped.
        raise UnsafeImage(f"could not safely decode image: {exc}") from exc

    if not exif:
        return ExifResult(has_exif=False, has_gps=False, width=width, height=height)

    tags = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
    device_parts = [_clean_str(tags.get("Make")), _clean_str(tags.get("Model"))]
    device = " ".join(p for p in device_parts if p) or None

    result = ExifResult(
        has_exif=True,
        has_gps=False,
        capture_device=device,
        capture_datetime=_parse_datetime(tags.get("DateTimeOriginal") or tags.get("DateTime")),
        software=_clean_str(tags.get("Software")),
        width=width,
        height=height,
    )

    gps_ifd = exif.get_ifd(IFD.GPSInfo) if hasattr(exif, "get_ifd") else None
    if not gps_ifd:
        return result

    gps_names = {member.value: member.name for member in GPS}
    gps = {gps_names.get(k, k): v for k, v in gps_ifd.items()}
    lat, lat_ref = gps.get("GPSLatitude"), gps.get("GPSLatitudeRef")
    lon, lon_ref = gps.get("GPSLongitude"), gps.get("GPSLongitudeRef")
    if not (lat and lon and lat_ref and lon_ref):
        return result

    try:
        latitude = _dms_to_degrees(lat, str(lat_ref))
        longitude = _dms_to_degrees(lon, str(lon_ref))
    except (TypeError, ValueError, ZeroDivisionError):
        return result

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return result  # malformed tag data; do not report nonsense coordinates

    altitude = None
    raw_altitude = gps.get("GPSAltitude")
    if raw_altitude is not None:
        try:
            altitude = float(raw_altitude)
            if gps.get("GPSAltitudeRef") == 1:  # 1 = below sea level
                altitude = -altitude
        except (TypeError, ValueError):
            altitude = None

    result.has_gps = True
    result.coordinates = GpsCoordinates(latitude=latitude, longitude=longitude, altitude_m=altitude)
    return result
