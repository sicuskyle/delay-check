import pytest

from delay_check.drift import (
    analyze_timebase_drift, linear_fit, detect_progressive_delay,
    print_progressive_delay,
)


def _segments_with_delays(delays, start_sec=100.0, step_sec=281.75, window_sec=60.0):
    results = []
    for i, delay in enumerate(delays):
        start = start_sec + i * step_sec
        results.append({
            "Start": f"{start}",
            "End": f"{start + window_sec}",
            "StartSec": start,
            "EndSec": start + window_sec,
            "Delay": delay,
            "Score": 50.0,
        })
    return results


class TestLinearFit:
    def test_exact_positive_slope(self):
        xs = [0.0, 1.0, 2.0, 3.0]
        ys = [10.0, 13.0, 16.0, 19.0]
        slope, intercept, r2 = linear_fit(xs, ys)
        assert slope == pytest.approx(3.0)
        assert intercept == pytest.approx(10.0)
        assert r2 == pytest.approx(1.0)

    def test_constant_series_has_zero_slope(self):
        xs = [0.0, 1.0, 2.0]
        ys = [5.0, 5.0, 5.0]
        slope, intercept, r2 = linear_fit(xs, ys)
        assert slope == 0.0
        assert intercept == 5.0
        assert r2 == 1.0


class TestAnalyzeTimebaseDrift:
    def test_detects_dub_faster_linear_drift(self):
        # +430 ms every 281.75 s -> rate = 430 / 281750 ms/s ~ 0.001526
        # ~0.153% (1530 ppm) faster
        delays = [464, 894, 1324, 1754, 2184, 2614, 3044, 3474]
        drift = analyze_timebase_drift(_segments_with_delays(delays))
        assert drift is not None
        assert drift["direction"] == "FASTER"
        expected_rate = 430 / 281.75 / 1000.0
        assert drift["percent"] == pytest.approx(expected_rate * 100, rel=0.01)
        assert drift["ppm"] == pytest.approx(expected_rate * 1_000_000, rel=0.01)
        assert drift["r_squared"] > 0.99
        assert drift["atempo"] < 1.0

    def test_detects_dub_slower_linear_drift(self):
        delays = [3474, 3044, 2614, 2184, 1754, 1324, 894, 464]
        drift = analyze_timebase_drift(_segments_with_delays(delays))
        assert drift is not None
        assert drift["direction"] == "SLOWER"
        assert drift["percent"] > 0
        assert drift["atempo"] > 1.0

    def test_constant_delay_is_not_drift(self):
        delays = [1970, 1975, 1965, 1972, 1968, 1971, 1969, 1974]
        assert analyze_timebase_drift(_segments_with_delays(delays)) is None

    def test_random_scatter_is_not_drift(self):
        delays = [-48169, -50085, 50791, 47219, -16635, -46711, -17228, 4649]
        assert analyze_timebase_drift(_segments_with_delays(delays)) is None

    def test_too_few_segments_returns_none(self):
        delays = [100, 500, 900]
        assert analyze_timebase_drift(_segments_with_delays(delays)[:2]) is None

    def test_missing_timestamps_returns_none(self):
        results = [{"Delay": 100, "Score": 50.0}] * 4
        assert analyze_timebase_drift(results) is None

    def test_atempo_formula_for_faster_dub(self):
        # dub faster by rate r -> slow it down: atempo = 1 / (1 + r)
        delays = [0, 1526, 3052, 4578, 6104, 7630, 9156, 10682]
        drift = analyze_timebase_drift(_segments_with_delays(delays))
        assert drift is not None
        rate = drift["percent"] / 100.0
        assert drift["atempo"] == pytest.approx(1.0 / (1.0 + rate), rel=1e-4)

    def test_progressive_step_pattern_is_not_drift(self):
        # Real case (101.DUB vs 101.REF): a constant initial delay plus 5
        # localized ~2000ms jumps, not a continuous rate change. Even though
        # a whole-file linear fit lands on a reasonably straight line, the
        # mix of flat and moving intervals must rule this out as drift --
        # see TestDetectProgressiveDelay for the positive detection side.
        starts = [0, 379.945, 759.890, 1139.835, 1519.780, 1899.725, 2279.670, 2659.615]
        delays = [1666, 1666, 3668, 5670, 5668, 7673, 9672, 11692]
        segment_results = [
            {"StartSec": s, "EndSec": s + 60, "Delay": d, "Score": 50.0}
            for s, d in zip(starts, delays)
        ]
        assert analyze_timebase_drift(segment_results) is None


class TestDetectProgressiveDelay:
    def test_real_case_finds_all_five_steps(self):
        # Same dataset as
        # TestAnalyzeTimebaseDrift.test_progressive_step_pattern_is_not_drift.
        starts = [0, 379.945, 759.890, 1139.835, 1519.780, 1899.725, 2279.670, 2659.615]
        delays = [1666, 1666, 3668, 5670, 5668, 7673, 9672, 11692]
        segment_results = [
            {"StartSec": s, "EndSec": s + 60, "Delay": d, "Score": 50.0}
            for s, d in zip(starts, delays)
        ]

        progressive = detect_progressive_delay(segment_results)

        assert progressive is not None
        assert len(progressive["steps"]) == 5
        jumps = [round(s["jump_ms"]) for s in progressive["steps"]]
        assert jumps == [2002, 2002, 2005, 1999, 2020]
        assert progressive["total_jump_ms"] == pytest.approx(sum(jumps), abs=1)

    def test_constant_delay_has_no_steps(self):
        delays = [1970, 1975, 1965, 1972, 1968, 1971, 1969, 1974]
        assert detect_progressive_delay(_segments_with_delays(delays)) is None

    def test_continuous_drift_has_no_steps(self):
        # Every interval moves at the same rate -- that's drift, not
        # localized steps, so this must not also fire as progressive.
        delays = [464, 894, 1324, 1754, 2184, 2614, 3044, 3474]
        assert detect_progressive_delay(_segments_with_delays(delays)) is None

    def test_negative_jump_recommends_removal_not_insertion(self):
        starts = [0.0, 100.0, 200.0, 300.0]
        delays = [5000, 5000, 2000, 2000]
        segment_results = [
            {"StartSec": s, "EndSec": s + 60, "Delay": d, "Score": 50.0}
            for s, d in zip(starts, delays)
        ]

        progressive = detect_progressive_delay(segment_results)

        assert progressive is not None
        assert len(progressive["steps"]) == 1
        assert progressive["steps"][0]["jump_ms"] == pytest.approx(-3000.0)

    def test_too_few_segments_returns_none(self):
        delays = [100, 500]
        assert detect_progressive_delay(_segments_with_delays(delays)) is None


class TestPrintProgressiveDelay:
    def test_reports_the_jump_without_overclaiming_an_exact_edit_point(self, capsys):
        # The report must not phrase this as a precise instruction (e.g.
        # "insert X ms here") -- the bracket is only as precise as the two
        # nearest sample windows (minutes wide), not an exact timestamp.
        progressive = {
            "total_jump_ms": 2000.0,
            "steps": [{
                "before_time_sec": 60.0, "after_time_sec": 120.0,
                "before_delay_ms": 1000.0, "after_delay_ms": 3000.0,
                "jump_ms": 2000.0,
            }],
        }
        print_progressive_delay(progressive)
        out = capsys.readouterr().out
        assert "1000 -> 3000 ms" in out
        assert "+2000 ms" in out
        assert "of silence into the dub here" not in out
        assert "manual confirmation" in out

    def test_general_note_explains_both_jump_directions(self, capsys):
        progressive = {
            "total_jump_ms": -1000.0,
            "steps": [{
                "before_time_sec": 100.0, "after_time_sec": 200.0,
                "before_delay_ms": 5000.0, "after_delay_ms": 2000.0,
                "jump_ms": -3000.0,
            }],
        }
        print_progressive_delay(progressive)
        out = capsys.readouterr().out
        assert "pause present in the reference is missing from the" in out
        assert "dub has extra content the reference doesn't" in out
        assert "-3000 ms" in out
