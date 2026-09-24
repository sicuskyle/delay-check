from delay_check.commons import print_subt


DRIFT_MIN_R2 = 0.9
DRIFT_MIN_SLOPE_MS_PER_S = 0.5


def linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    if ss_xx == 0:
        return 0.0, mean_y, 0.0
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    r_squared = 1.0 if ss_tot == 0 else max(0.0, 1.0 - ss_res / ss_tot)
    return slope, intercept, r_squared


def analyze_timebase_drift(segment_results: list[dict]) -> dict | None:
    if len(segment_results) < 3:
        return None
    if not all("StartSec" in s and "EndSec" in s for s in segment_results):
        return None

    xs = [(s["StartSec"] + s["EndSec"]) / 2 for s in segment_results]
    ys = [float(s["Delay"]) for s in segment_results]

    slope, _, r_squared = linear_fit(xs, ys)

    if r_squared < DRIFT_MIN_R2 or abs(slope) < DRIFT_MIN_SLOPE_MS_PER_S:
        return None

    rate = slope / 1000.0
    percent = rate * 100.0
    ppm = int(round(rate * 1_000_000))
    atempo = 1.0 / (1.0 + rate)

    return {
        "slope_ms_per_s": slope,
        "r_squared": r_squared,
        "percent": abs(percent),
        "ppm": abs(ppm),
        "direction": "FASTER" if slope > 0 else "SLOWER",
        "atempo": atempo,
    }


def print_timebase_drift(drift: dict) -> None:
    print_subt("### Timebase drift Detected ###", 55, center=True)
    print(
        f" ### Dubbed is {drift['percent']:.3f}% ({drift['ppm']} ppm) "
        f"{drift['direction']} than reference"
    )
    print(f" ### Delay slope: {drift['slope_ms_per_s']:+.3f} ms per second")
    print(" ### Recommendation: apply tempo correction, not a fixed offset")
    print(
        f' ### Suggested: ffmpeg -i dubbed -af "atempo={drift["atempo"]:.5f}" output'
    )
