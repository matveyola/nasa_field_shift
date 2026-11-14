from math import atan2, cos, fsum, isclose, radians, sin

Point = tuple[float, float]
Vector = tuple[float, float, float]
EARTH_RADIUS_M = 6_371_008.8


def _on_segment(a: Point, b: Point, point: Point) -> bool:
    return min(a[0], b[0]) <= point[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= point[1] <= max(
        a[1], b[1]
    )


def _side(a: Point, b: Point, point: Point) -> int:
    cross = (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
    return 0 if isclose(cross, 0, abs_tol=1e-15) else (1 if cross > 0 else -1)


def _intersects(a: Point, b: Point, c: Point, d: Point) -> bool:
    abc, abd = _side(a, b, c), _side(a, b, d)
    cda, cdb = _side(c, d, a), _side(c, d, b)
    return (
        (abc * abd < 0 and cda * cdb < 0)
        or (abc == 0 and _on_segment(a, b, c))
        or (abd == 0 and _on_segment(a, b, d))
        or (cda == 0 and _on_segment(c, d, a))
        or (cdb == 0 and _on_segment(c, d, b))
    )


def validate_ring(ring: list[Point]) -> None:
    if not 4 <= len(ring) <= 128 or ring[0] != ring[-1]:
        raise ValueError("A polygon needs a closed ring with 3 to 127 distinct vertices")
    if len(set(ring[:-1])) != len(ring) - 1:
        raise ValueError("The polygon contains repeated vertices")
    if any(not (-180 <= lon <= 180 and -90 <= lat <= 90) for lon, lat in ring):
        raise ValueError("Polygon coordinates are outside longitude or latitude bounds")

    edges = list(zip(ring[:-1], ring[1:], strict=True))
    for i, vertex in enumerate(ring[:-1]):
        previous = ring[i - 1] if i else ring[-2]
        following = ring[i + 1]
        if _side(previous, vertex, following) == 0 and not _on_segment(previous, following, vertex):
            raise ValueError("The polygon edges overlap")
    for i, (a, b) in enumerate(edges):
        for j in range(i + 2, len(edges)):
            if i == 0 and j == len(edges) - 1:
                continue
            if _intersects(a, b, *edges[j]):
                raise ValueError("The polygon edges intersect")

    origin_x, origin_y = ring[0]
    area = sum(
        (a[0] - origin_x) * (b[1] - origin_y) - (b[0] - origin_x) * (a[1] - origin_y)
        for a, b in edges
    )
    if isclose(area, 0, abs_tol=1e-13):
        raise ValueError("The polygon encloses no area")


def contains_point(ring: list[Point], x: float, y: float) -> bool:
    inside = False
    for (ax, ay), (bx, by) in zip(ring[:-1], ring[1:], strict=True):
        if (ay > y) != (by > y) and x < ax + (bx - ax) * (y - ay) / (by - ay):
            inside = not inside
    return inside


def same_boundary(first: list[Point], second: list[Point]) -> bool:
    """Ignore the starting vertex and winding of a closed ring."""
    size = len(first) - 1
    if len(first) != len(second) or second[0] not in first[:-1]:
        return False
    start = first.index(second[0])
    return any(
        all(first[(start + i * direction) % size] == second[i] for i in range(size))
        for direction in (1, -1)
    )


def _longitude_frame(ring: list[Point], origin: float) -> list[Point]:
    """Place small polygons near the antimeridian in a common longitude frame."""
    return [
        (
            lon - 360 if lon - origin > 180 else lon + 360 if lon - origin < -180 else lon,
            lat,
        )
        for lon, lat in ring
    ]


def covers_ring(boundary: list[Point], footprint: list[Point]) -> bool:
    origin = boundary[0][0]
    boundary = _longitude_frame(boundary, origin)
    footprint = _longitude_frame(footprint, origin)
    boundary_edges = list(zip(boundary[:-1], boundary[1:], strict=True))

    def covers(point: Point) -> bool:
        return contains_point(boundary, *point) or any(
            _side(a, b, point) == 0 and _on_segment(a, b, point) for a, b in boundary_edges
        )

    if not all(covers(point) for point in footprint[:-1]):
        return False

    for a, b in zip(footprint[:-1], footprint[1:], strict=True):
        contacts = [a, b]
        for c, d in boundary_edges:
            ac, ad = _side(a, b, c), _side(a, b, d)
            if ac * ad < 0 and _side(c, d, a) * _side(c, d, b) < 0:
                return False
            if ac == 0 and _on_segment(a, b, c):
                contacts.append(c)
        # A segment can pass outside through boundary vertices without properly crossing an edge.
        points = sorted(set(contacts))
        for c, d in zip(points[:-1], points[1:], strict=True):
            if not covers(((c[0] + d[0]) / 2, (c[1] + d[1]) / 2)):
                return False
    return True


def _unit_vector(point: Point) -> Vector:
    lon, lat = map(radians, point)
    return cos(lat) * cos(lon), cos(lat) * sin(lon), sin(lat)


def _dot(a: Vector, b: Vector) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _triangle_excess(origin: Vector, a: Vector, b: Vector) -> float:
    # Van Oosterom–Strackee signed solid angle. Subtracting origin before the
    # cross product avoids cancellation for very small parcels.
    ax, ay, az = (x - y for x, y in zip(a, origin, strict=True))
    bx, by, bz = (x - y for x, y in zip(b, origin, strict=True))
    cross = (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)
    return 2 * atan2(_dot(origin, cross), 1 + _dot(origin, a) + _dot(a, b) + _dot(b, origin))


def area_hectares(ring: list[Point]) -> float:
    """Area on a sphere with great-circle edges."""
    origin, *vectors = map(_unit_vector, ring[:-1])
    excess = fsum(
        _triangle_excess(origin, a, b) for a, b in zip(vectors[:-1], vectors[1:], strict=True)
    )
    return abs(excess) * EARTH_RADIUS_M**2 / 10_000
