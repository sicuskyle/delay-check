import pytest

from delay_check.drift import analyze_timebase_drift, linear_fit


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
