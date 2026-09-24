from delay_check.windowing import get_lowest, segments_times


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
