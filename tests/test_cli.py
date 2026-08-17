from delay_check.cli import calculate_confidence, get_lowest, segments_times


class TestCalculateConfidence:
    def test_empty_delays_returns_zero(self):
        assert calculate_confidence([]) == (0.0, [])

    def test_all_equal_delays_is_full_confidence(self):
        score, out_of_sync = calculate_confidence([1000, 1000, 1000, 1000, 1000])
        assert score == 100.0
        assert out_of_sync == []

    def test_score_matches_weighted_penalty_tiers(self):
        # base=1000; diffs vs. base: 10 (excellent), 40 (good), 60 (acceptable),
        # 90 (poor), 200 (failed) -- against the shipped config.json tiers
        # (excellent<=25, good<=50, acceptable<=75, poor<=100) and penalty
        # factors (1.0, 0.95, 0.85, 0.70, 0.0), base_confidence=20.0,
        # max_per_segment=20.0.
        delays = [1000, 1010, 1040, 1060, 1090, 1200]
        score, out_of_sync = calculate_confidence(delays)

        expected = 20.0 + 20.0 * (1.0 + 0.95 + 0.85 + 0.70 + 0.0)
        assert score == round(expected, 2)

        assert out_of_sync == [
            {"segment_index": 6, "delay_found": 1200, "drift_amount": 200}
        ]

    def test_single_segment_beyond_poor_tolerance_is_flagged(self):
        delays = [0, 500]
        score, out_of_sync = calculate_confidence(delays)
        assert out_of_sync == [
            {"segment_index": 2, "delay_found": 500, "drift_amount": 500}
        ]
        assert score == 20.0


class TestSegmentsTimes:
    def test_yields_four_segments_starting_at_two(self):
        result = list(segments_times(100, 20))
        assert result == [(2, 25.0), (3, 50), (4, 75.0), (5, 80)]

    def test_last_segment_start_never_negative(self):
        # max_sec bigger than max_duration_sec -> clamp to 0
        result = list(segments_times(10, 20))
        assert result[-1] == (5, 0)


class TestGetLowest:
    def test_returns_smaller_value(self):
        assert get_lowest(3, 5) == 3
        assert get_lowest(5, 3) == 3

    def test_returns_a_when_equal(self):
        assert get_lowest(4, 4) == 4
