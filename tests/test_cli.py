from delay_check.cli import (
    aggregate_delay, calculate_confidence, get_lowest, segments_times
)


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


class TestCalculateConfidenceWithExplicitAnchor:
    def test_scores_all_delays_against_explicit_anchor(self):
        # anchor not equal to any element; all 4 real delays scored (not
        # N-1), all within the 'excellent' tier (<=25ms) of anchor=1008.
        delays = [1000, 1005, 1010, 1015]
        score, out_of_sync = calculate_confidence(delays, anchor=1008)
        # max_per_segment = (100 - base_confidence(20)) / len(delays) = 80/4 = 20.0
        expected = 20.0 + 20.0 * 4 * 1.0
        assert score == round(expected, 2)
        assert out_of_sync == []

    def test_dynamic_max_per_segment_scales_with_delay_count(self):
        delays = [0, 0, 0, 0, 0, 0]
        score, out_of_sync = calculate_confidence(delays, anchor=0)
        assert score == 100.0
        assert out_of_sync == []

    def test_segment_index_matches_real_window_number(self):
        # With an explicit anchor, delays[0] is window #1 (not skipped as
        # the anchor itself), so a flagged delays[1] must report
        # segment_index=2, matching the window numbering from segments_times.
        delays = [1000, 1500]
        score, out_of_sync = calculate_confidence(delays, anchor=1000)
        assert out_of_sync == [
            {"segment_index": 2, "delay_found": 1500, "drift_amount": 500}
        ]

    def test_default_anchor_behavior_is_unchanged(self):
        # Regression: calling without `anchor` must reproduce the original
        # delays[0]-as-anchor, N-1-scored-segments behavior exactly.
        delays = [1000, 1010, 1040, 1060, 1090, 1200]
        with_default = calculate_confidence(delays)
        without_anchor_kwarg = calculate_confidence(delays, anchor=None)
        assert with_default == without_anchor_kwarg


class TestAggregateDelay:
    def test_median_of_all_confident_windows(self):
        segment_results = [
            {"Delay": 1000, "Score": 90.0},
            {"Delay": 1010, "Score": 85.0},
            {"Delay": 1020, "Score": 95.0},
        ]
        median_delay, confident = aggregate_delay(segment_results)
        assert median_delay == 1010
        assert confident == [1000, 1010, 1020]

    def test_median_with_even_count_averages_middle_two(self):
        segment_results = [
            {"Delay": 1000, "Score": 90.0},
            {"Delay": 1010, "Score": 90.0},
            {"Delay": 1020, "Score": 90.0},
            {"Delay": 1030, "Score": 90.0},
        ]
        median_delay, confident = aggregate_delay(segment_results)
        assert median_delay == 1015
        assert confident == [1000, 1010, 1020, 1030]

    def test_filters_out_low_confidence_windows(self):
        # confidence_threshold is 20 in the shipped config.json.
        segment_results = [
            {"Delay": 1000, "Score": 90.0},
            {"Delay": 5000, "Score": 10.0},
            {"Delay": 1010, "Score": 85.0},
        ]
        median_delay, confident = aggregate_delay(segment_results)
        assert median_delay == 1005
        assert confident == [1000, 1010]

    def test_no_confident_windows_returns_none(self):
        # No score clears the threshold, and the delays don't cluster
        # tightly enough for the consensus fallback either.
        segment_results = [
            {"Delay": 1000, "Score": 5.0},
            {"Delay": 5000, "Score": 8.0},
            {"Delay": 9000, "Score": 12.0},
        ]
        assert aggregate_delay(segment_results) == (None, [])

    def test_empty_input_returns_none(self):
        assert aggregate_delay([]) == (None, [])

    def test_consensus_fallback_when_no_window_clears_threshold(self):
        # Mirrors a real case: real dubbed content where each window's
        # score stays below confidence_threshold (only part of the audio,
        # e.g. shared music/effects and not the re-recorded dialogue,
        # actually correlates), but a majority of windows still agree
        # tightly on the same delay -- strong evidence despite low scores.
        segment_results = [
            {"Delay": 9510, "Score": 15.0},
            {"Delay": 9511, "Score": 18.0},
            {"Delay": 9509, "Score": 12.0},
            {"Delay": 9510, "Score": 10.0},
            {"Delay": 500, "Score": 5.0},  # outlier, not part of the consensus
        ]
        median_delay, cluster = aggregate_delay(segment_results)
        assert median_delay == 9510
        assert sorted(cluster) == [9509, 9510, 9510, 9511]

    def test_consensus_requires_strict_majority(self):
        # Largest cluster is exactly half (2 of 4) -- not a strict
        # majority, so this must NOT trigger the consensus fallback.
        segment_results = [
            {"Delay": 1000, "Score": 5.0},
            {"Delay": 1005, "Score": 5.0},
            {"Delay": 5000, "Score": 5.0},
            {"Delay": 9000, "Score": 5.0},
        ]
        assert aggregate_delay(segment_results) == (None, [])


class TestSegmentsTimes:
    def test_yields_n_evenly_spaced_segments(self):
        # fractions i/(n+1) for i=1..4, clamped to max(100-20,0)=80
        result = list(segments_times(100, 20, n_segments=4))
        assert result == [(1, 20.0), (2, 40.0), (3, 60.0), (4, 80.0)]

    def test_segment_count_matches_n_segments(self):
        result = list(segments_times(480, 60, n_segments=8))
        assert len(result) == 8
        assert [idx for idx, _ in result] == list(range(1, 9))

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
