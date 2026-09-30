from pathlib import Path

import pytest

from delay_check import windowing
from delay_check.windowing import (
    get_lowest, segments_times, _predict_delay_ms, _shifted_starts,
    segment_delays,
)


class TestSegmentsTimes:
    def test_yields_n_evenly_spaced_segments(self):
        # span = max(100-20,0)=80, starts at i/(n-1) for i=0..3 -> 0, 80/3, 160/3, 80
        result = list(segments_times(100, 20, n_segments=4))
        assert result == [
            (1, 0.0),
            (2, 80 / 3),
            (3, 160 / 3),
            (4, 80.0),
        ]

    def test_first_window_always_starts_at_zero(self):
        for duration, window, n in [(480, 60, 8), (1800, 60, 8), (100, 20, 4), (75, 75, 3)]:
            first_start = next(iter(segments_times(duration, window, n)))[1]
            assert first_start == 0.0

    def test_last_window_ends_at_file_end(self):
        result = list(segments_times(480, 60, n_segments=8))
        assert result[-1][1] + 60 == 480

    def test_segment_count_matches_n_segments(self):
        result = list(segments_times(480, 60, n_segments=8))
        assert len(result) == 8
        assert [idx for idx, _ in result] == list(range(1, 9))

    def test_single_segment_starts_at_zero(self):
        assert list(segments_times(480, 60, n_segments=1)) == [(1, 0.0)]

    def test_times_are_strictly_increasing_before_clamping(self):
        result = list(segments_times(1000, 10, n_segments=5))
        times = [t for _, t in result]
        assert times == sorted(times)
        assert len(set(times)) == len(times)

    def test_all_segments_clamp_to_zero_when_max_sec_exceeds_duration(self):
        # max_sec bigger than max_duration_sec -> every window clamps to 0
        result = list(segments_times(10, 20, n_segments=4))
        assert all(time == 0 for _, time in result)


class TestGetLowest:
    def test_returns_smaller_value(self):
        assert get_lowest(3, 5) == 3
        assert get_lowest(5, 3) == 3

    def test_returns_a_when_equal(self):
        assert get_lowest(4, 4) == 4


class TestPredictDelayMs:
    def test_no_anchors_returns_zero(self):
        assert _predict_delay_ms([], 100.0) == 0.0

    def test_single_anchor_is_zero_order_hold(self):
        # With only one confirmed point, the prediction ignores t entirely
        # and just carries the last known delay forward.
        assert _predict_delay_ms([(10.0, 500.0)], 999.0) == 500.0

    def test_two_or_more_anchors_extrapolate_linearly(self):
        # Exact line: delay = -100 + 2*t -- anchors at t=0 and t=45.
        anchors = [(0.0, -100.0), (45.0, -10.0)]
        predicted = _predict_delay_ms(anchors, 90.0)
        assert predicted == pytest.approx(80.0)


class TestShiftedStarts:
    def test_zero_predicted_delay_keeps_both_at_nominal(self):
        assert _shifted_starts(100.0, 0.0) == (100.0, 100.0)

    def test_positive_predicted_delay_shifts_ref_forward(self):
        # ref leads -> ref reads further into itself, dub stays put.
        ref_start, dub_start = _shifted_starts(100.0, 2500.0)
        assert ref_start == pytest.approx(102.5)
        assert dub_start == 100.0

    def test_negative_predicted_delay_shifts_dub_forward(self):
        # dub leads -> dub reads further into itself, ref stays put.
        ref_start, dub_start = _shifted_starts(100.0, -2500.0)
        assert ref_start == 100.0
        assert dub_start == pytest.approx(102.5)


class _FakeConfig:
    segments_to_analyze = 3
    confidence_threshold = 20.0


class _ScriptedCorrelate:
    """Stand-in for _correlate_window: returns queued (delay_ms, score)
    responses in call order and records the (ref_start, dub_start) each
    call was made with, so tests can assert both the decision (retry or
    not) and the exact shifted read positions used.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, ref_sample, dub_sample, ref_start, dub_start, max_sec):
        self.calls.append((ref_start, dub_start))
        return self.responses.pop(0)


class TestSegmentDelaysTracking:
    """Exercises segment_delays' retry-on-divergence logic in isolation,
    with _correlate_window and get_duration mocked out -- no real audio
    files or correlation math involved.
    """

    @pytest.mark.asyncio
    async def test_first_window_is_never_retried(self, monkeypatch):
        # No anchors exist yet when window #1 is processed, so the
        # deviation check is skipped unconditionally regardless of the
        # naive result.
        monkeypatch.setattr(windowing, "config", _FakeConfig())
        monkeypatch.setattr(windowing, "get_duration", lambda path: 100_000)
        scripted = _ScriptedCorrelate([
            (99999, 5.0),  # window1 naive -- wildly off, but nothing to compare against
            (-140, 85.0),  # window2 naive
            (-150, 80.0),  # window3 naive
        ])
        monkeypatch.setattr(windowing, "_correlate_window", scripted)

        results = await segment_delays(Path("ref.wav"), Path("dub.wav"), max_sec=10)

        assert len(scripted.calls) == 3  # no retry call inserted
        assert results[0]["Delay"] == 99999

    @pytest.mark.asyncio
    async def test_naive_result_within_tolerance_is_not_retried(self, monkeypatch):
        monkeypatch.setattr(windowing, "config", _FakeConfig())
        monkeypatch.setattr(windowing, "get_duration", lambda path: 100_000)
        scripted = _ScriptedCorrelate([
            (-100, 90.0),  # window1 naive -> becomes an anchor
            (-140, 85.0),  # window2 naive -- close enough to the -100 trend
            (-150, 80.0),  # window3 naive
        ])
        monkeypatch.setattr(windowing, "_correlate_window", scripted)

        results = await segment_delays(Path("ref.wav"), Path("dub.wav"), max_sec=10)

        assert len(scripted.calls) == 3  # never retried
        assert [s["Delay"] for s in results] == [-100, -140, -150]

    @pytest.mark.asyncio
    async def test_naive_result_diverging_triggers_retry_with_shifted_read(self, monkeypatch):
        monkeypatch.setattr(windowing, "config", _FakeConfig())
        monkeypatch.setattr(windowing, "get_duration", lambda path: 100_000)
        # max_sec=10 -> deviation_cap_ms = 10*1000*0.2 = 2000
        scripted = _ScriptedCorrelate([
            (-100, 90.0),  # window1 naive -> anchor (0.0, -100.0)
            (3000, 10.0),  # window2 naive: |3000 - (-100)| = 3100 > 2000 -> retry
            (350, 40.0),   # window2 retry residual
            (-140, 85.0),  # window3 naive: predicted from 2 anchors is far enough
                           # away that this naive result stays within tolerance
        ])
        monkeypatch.setattr(windowing, "_correlate_window", scripted)

        results = await segment_delays(Path("ref.wav"), Path("dub.wav"), max_sec=10)

        assert len(scripted.calls) == 4  # one retry call was inserted
        # Retry call used the predicted delay (-100, zero-order hold from the
        # single prior anchor) to pre-shift the "ahead" side (dub, since the
        # prediction is negative) by 0.1s forward from the nominal t=45.0.
        assert scripted.calls[2] == (45.0, 45.1)
        # Reported delay = predicted (-100) + retried residual (350) = 250.
        assert results[1]["Delay"] == 250
        assert results[1]["Score"] == 40.0

    @pytest.mark.asyncio
    async def test_low_score_window_is_not_used_as_an_anchor(self, monkeypatch):
        # A window scoring below confidence_threshold must not corrupt the
        # trend used to sanity-check later windows, even if its own naive
        # delay happens to look plausible.
        monkeypatch.setattr(windowing, "config", _FakeConfig())
        monkeypatch.setattr(windowing, "get_duration", lambda path: 100_000)
        scripted = _ScriptedCorrelate([
            (-100, 90.0),  # window1 naive -> anchor
            (5000, 5.0),   # window2 naive: diverges -> retry
            (10, 5.0),     # window2 retry: still below threshold -> NOT an anchor
            (-105, 90.0),  # window3 naive: checked only against window1's anchor
                           # (window2 never qualified), well within tolerance
        ])
        monkeypatch.setattr(windowing, "_correlate_window", scripted)

        results = await segment_delays(Path("ref.wav"), Path("dub.wav"), max_sec=10)

        assert len(scripted.calls) == 4
        assert results[2]["Delay"] == -105
        assert results[2]["Score"] == 90.0
