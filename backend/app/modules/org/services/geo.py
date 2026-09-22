"""Geography helpers.

Kept in one place because the longitude/latitude ordering is the single easiest
thing to get backwards in this codebase, and getting it backwards puts every
site in the wrong hemisphere without raising anything.

**PostGIS and GeoJSON are (longitude, latitude). Humans say "lat, long".**
Every conversion in the system goes through these two functions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry import Polygon as ShapelyPolygon

SRID = 4326


@dataclass(frozen=True, slots=True)
class LatLng:
    latitude: float
    longitude: float


def to_db_point(latitude: float, longitude: float) -> Any:
    """A geography point for storage. Note the argument order flips here."""
    return from_shape(ShapelyPoint(longitude, latitude), srid=SRID)


def from_db_point(value: Any) -> LatLng | None:
    """Read a stored point back as latitude/longitude."""
    if value is None:
        return None
    shape = to_shape(value)
    return LatLng(latitude=shape.y, longitude=shape.x)


def to_db_polygon(points: list[tuple[float, float]]) -> Any:
    """Build a polygon from (latitude, longitude) pairs.

    The ring is closed automatically — a caller who forgets to repeat the first
    point should get a valid boundary, not a PostGIS error.
    """
    if len(points) < 3:
        raise ValueError("A polygon needs at least three points")

    ring = [(longitude, latitude) for latitude, longitude in points]
    if ring[0] != ring[-1]:
        ring.append(ring[0])

    polygon = ShapelyPolygon(ring)
    if not polygon.is_valid:
        raise ValueError(
            "The boundary is not a valid polygon — its edges cross. "
            "Trace the site perimeter in one direction without doubling back."
        )
    return from_shape(polygon, srid=SRID)


def from_db_polygon(value: Any) -> list[LatLng] | None:
    if value is None:
        return None
    shape = to_shape(value)
    return [LatLng(latitude=y, longitude=x) for x, y in shape.exterior.coords]


def coerce_point_input(value: Any) -> Any:
    """Accept either a stored geography value or an already-shaped dict.

    Used as a `mode="before"` validator so `Model.model_validate(orm_row)`
    works directly: without it, Pydantic reads the raw WKB element and reports
    "latitude field required", which is a confusing way to say "this is a
    PostGIS blob, not a point".
    """
    if value is None or isinstance(value, dict):
        return value
    if hasattr(value, "latitude") and hasattr(value, "longitude"):
        return value
    coords = from_db_point(value)
    return {"latitude": coords.latitude, "longitude": coords.longitude} if coords else None
