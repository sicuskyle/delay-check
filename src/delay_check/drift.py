from delay_check.commons import print_subt
from delay_check.utils import ms_to_timestamp


DRIFT_MIN_R2 = 0.9
DRIFT_MIN_SLOPE_MS_PER_S = 0.5

# A local rate of change (between two consecutive windows) below this is
# treated as "flat" -- no real speed difference in that interval. Same
# order of magnitude as DRIFT_MIN_SLOPE_MS_PER_S since both express "is
# there a meaningful rate of change here", just at different granularities
# (whole-file fit vs a single interval).
STEP_FLAT_RATE_MS_PER_S = 0.5


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


def _sorted_points(segment_results: list[dict]) -> tuple[list[float], list[float]] | None:
    if len(segment_results) < 3:
        return None
    if not all("StartSec" in s and "EndSec" in s for s in segment_results):
        return None

    ordered = sorted(segment_results, key=lambda s: s["StartSec"])
    xs = [(s["StartSec"] + s["EndSec"]) / 2 for s in ordered]
    ys = [float(s["Delay"]) for s in ordered]
    return xs, ys


def _local_rates(xs: list[float], ys: list[float]) -> list[float]:
    """Rate of change (ms/s) between each pair of consecutive windows --
    the local, interval-by-interval counterpart to linear_fit's single
    whole-file slope. A genuine constant-rate drift shows a similar rate in
    every interval; a handful of localized edits (inserted/removed pauses,
    added/cut scenes) instead show near-zero rates almost everywhere with a
    few large, isolated jumps -- a pattern the whole-file fit alone cannot
    tell apart from real drift once it happens to land on a reasonably
    straight line.
    """
    rates = []
    for i in range(len(xs) - 1):
        dt = xs[i + 1] - xs[i]
        if dt > 0:
            rates.append((ys[i + 1] - ys[i]) / dt)
    return rates


def analyze_timebase_drift(segment_results: list[dict]) -> dict | None:
    points = _sorted_points(segment_results)
    if points is None:
        return None
    xs, ys = points

    slope, _, r_squared = linear_fit(xs, ys)

    if r_squared < DRIFT_MIN_R2 or abs(slope) < DRIFT_MIN_SLOPE_MS_PER_S:
        return None

    # A real constant-rate drift should show up as a nonzero rate in every
    # interval, not just on average -- if any interval is flat while others
    # move, that heterogeneity means something localized is happening at
    # specific points rather than a continuous effect across the whole
    # file, even if the coarse whole-file fit still looks reasonably
    # linear. See detect_progressive_delay for that pattern instead.
    local_rates = _local_rates(xs, ys)
    if any(abs(r) < STEP_FLAT_RATE_MS_PER_S for r in local_rates):
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


def detect_progressive_delay(segment_results: list[dict]) -> dict | None:
    """Detects a "progressive" delay pattern: a handful of localized jumps
    (typically inserted/removed pauses or added/cut scenes) separated by
    otherwise-flat stretches, as opposed to analyze_timebase_drift's
    continuous, evenly-distributed rate of change.

    Requires at least one flat interval AND at least one moving interval --
    that mix is what distinguishes "a few discrete events" from either a
    genuinely constant delay (every interval flat) or genuine continuous
    drift (every interval moving at a similar rate, handled by
    analyze_timebase_drift). Coarse-only: this identifies which intervals
    contain a jump and by how much, but not the exact timestamp within
    that interval -- see PENDING.md for the planned bisection refinement.
    """
    points = _sorted_points(segment_results)
    if points is None:
        return None
    xs, ys = points

    local_rates = _local_rates(xs, ys)
    if len(local_rates) < 2:
        return None

    flat = [abs(r) < STEP_FLAT_RATE_MS_PER_S for r in local_rates]
    if not any(flat) or all(flat):
        return None

    steps = [
        {
            "before_time_sec": xs[i],
            "after_time_sec": xs[i + 1],
            "before_delay_ms": ys[i],
            "after_delay_ms": ys[i + 1],
            "jump_ms": ys[i + 1] - ys[i],
        }
        for i, is_flat in enumerate(flat)
        if not is_flat
    ]

    return {
        "steps": steps,
        "total_jump_ms": sum(s["jump_ms"] for s in steps),
    }


def print_progressive_delay(progressive: dict) -> None:
    steps = progressive["steps"]
    print_subt("### Progressive Delay Detected ###", 55, center=True)
    print(
        f" ### Found {len(steps)} localized jump(s) totaling "
        f"{progressive['total_jump_ms']:+.0f} ms -- not a constant-rate drift"
    )
    print(" ### Recommendation: fix each point individually")
    print(" ###   (a global tempo change will not correct this)")
    print(
        " ### Each range below only brackets the jump between the two\n"
        " ### nearest sample windows -- the exact edit point within that\n"
        " ### range needs manual confirmation. A positive jump usually\n"
        " ### means a pause present in the reference is missing from the\n"
        " ### dub (add silence there); a negative jump usually means the\n"
        " ### dub has extra content the reference doesn't (trim it there)."
    )
    for i, step in enumerate(steps, start=1):
        before_ts = ms_to_timestamp(int(step["before_time_sec"] * 1000))
        after_ts = ms_to_timestamp(int(step["after_time_sec"] * 1000))
        jump = step["jump_ms"]
        print(
            f"    #{i}: {before_ts} - {after_ts} | "
            f"{step['before_delay_ms']:.0f} -> {step['after_delay_ms']:.0f} ms "
            f"({jump:+.0f} ms)"
        )
