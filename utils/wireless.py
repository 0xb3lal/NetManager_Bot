def rssi_to_quality_pct(rssi: int) -> int:
    """Convert RSSI dBm to 0-100% quality (same as /active)."""
    return min(max(2 * (rssi + 100), 0), 100)


def rssi_to_distance_m(rssi: int, rssi_at_1m: int, n: float) -> float | None:
    """Estimate distance via log-distance path loss model.

    Returns None for invalid inputs.
    """
    if rssi is None or rssi_at_1m is None or n is None:
        return None
    try:
        n = float(n)
        if n <= 0:
            return None
        return 10 ** ((rssi_at_1m - int(rssi)) / (10 * n))
    except (TypeError, ValueError, OverflowError):
        return None


def clamp_distance(
    distance: float | None, floor_m: float = 1.0
) -> tuple[float | None, bool]:
    """Clamp distance to floor; returns (clamped, was_clamped)."""
    if distance is None:
        return None, False
    try:
        if distance < floor_m:
            return floor_m, True
        return distance, False
    except TypeError:
        return None, False
