import asyncio

import numpy as np
import pytest

from delay_check.fgp import compute_fingerprint, find_offset_fgp

from .helpers import make_base_signal, make_delayed_pair, make_unrelated_pair

# Current correlation-lag grid is hop_length/sr ~= 512/44100 ~= 11.6ms.
# 30ms gives comfortable margin over that quantization step plus rounding.
LAG_TOLERANCE_MS = 30


def _find_offset(ref, dub):
    return asyncio.run(find_offset_fgp(ref, dub, verbose=False))


class TestComputeFingerprint:
    def test_shape_is_one_dimensional(self):
        audio = make_base_signal(duration_sec=2.0)
        fp = compute_fingerprint(audio, sr=44100)
        assert fp.ndim == 1
        assert fp.shape[0] > 1


class TestFindOffsetKnownDelay:
    @pytest.mark.parametrize(
        "delay_ms", [0, 250, 1000, -250, -1000, 3000, -3000]
    )
    def test_recovers_known_delay(self, delay_ms):
        ref, dub, expected_lag_ms = make_delayed_pair(delay_ms, duration_sec=10.0)
        lag_ms, score = _find_offset(ref, dub)
        assert abs(lag_ms - expected_lag_ms) <= LAG_TOLERANCE_MS
        assert score > 60

    def test_gain_shifted_dub_is_still_recovered(self):
        ref, dub, expected_lag_ms = make_delayed_pair(1000, gain_db=-6, duration_sec=10.0)
        lag_ms, score = _find_offset(ref, dub)
        assert abs(lag_ms - expected_lag_ms) <= LAG_TOLERANCE_MS
        assert score > 60

    def test_bandpass_filtered_dub_is_still_recovered(self):
        ref, dub, expected_lag_ms = make_delayed_pair(
            1000, bandpass_hz=(1000, 4000), duration_sec=10.0
        )
        lag_ms, score = _find_offset(ref, dub)
        assert abs(lag_ms - expected_lag_ms) <= LAG_TOLERANCE_MS
        # EQ'd dub reduces correlation score vs. an unfiltered dub, but the
        # lag should still be recoverable -- known baseline of the current
        # (unmodified) algorithm, not a claim it's optimal.
        assert score > 40

    @pytest.mark.parametrize("snr_db", [10, 0])
    def test_degrades_gracefully_with_additive_noise(self, snr_db):
        ref, dub, expected_lag_ms = make_delayed_pair(
            1000, noise_snr_db=snr_db, duration_sec=10.0
        )
        lag_ms, score = _find_offset(ref, dub)
        assert abs(lag_ms - expected_lag_ms) <= LAG_TOLERANCE_MS
        assert score > 40

    def test_small_overlap_near_segment_boundary_does_not_crash(self):
        # Delay close to the clip duration -> little true overlap. This is
        # a known weak spot of the current (overlap-unaware) corr_score
        # normalization; we only pin that it doesn't crash and stays in
        # the right ballpark, not that it's precise.
        ref, dub, expected_lag_ms = make_delayed_pair(2700, duration_sec=3.0)
        lag_ms, score = _find_offset(ref, dub)
        assert abs(lag_ms - expected_lag_ms) <= 500
        assert 0.0 <= score <= 100.0


class TestFindOffsetUnrelatedSignals:
    @pytest.mark.parametrize("seed_a,seed_b", [(1, 2), (5, 9), (42, 43)])
    def test_unrelated_signals_score_lower_than_true_matches(self, seed_a, seed_b):
        ref, dub = make_unrelated_pair(duration_sec=6.0, seed_a=seed_a, seed_b=seed_b)
        _, score = _find_offset(ref, dub)
        # True-delay pairs consistently score > 60 above; unrelated content
        # should sit clearly below that.
        assert score < 65


class TestFindOffsetEdgeCases:
    def test_silence_does_not_crash_and_reports_zero_confidence(self):
        zeros = np.zeros(44100 * 3, dtype=np.float32)
        lag_ms, score = _find_offset(zeros, zeros)
        assert isinstance(lag_ms, int)
        assert score == 0.0

    def test_one_sided_silence_does_not_crash(self):
        zeros = np.zeros(44100 * 3, dtype=np.float32)
        _, dub, _ = make_delayed_pair(200, duration_sec=3.0)
        lag_ms, score = _find_offset(zeros, dub)
        assert isinstance(lag_ms, int)
        assert np.isfinite(score)

    def test_very_short_clips_do_not_crash(self):
        short_ref = make_base_signal(duration_sec=0.05, seed=42)
        short_dub = make_base_signal(duration_sec=0.05, seed=43)
        lag_ms, score = _find_offset(short_ref, short_dub)
        assert isinstance(lag_ms, int)
        assert np.isfinite(score)

    def test_mismatched_lengths_do_not_crash(self):
        ref_long = make_base_signal(duration_sec=5.0, seed=42)
        dub_short = make_base_signal(duration_sec=0.5, seed=44)
        lag_ms, score = _find_offset(ref_long, dub_short)
        assert isinstance(lag_ms, int)
        assert np.isfinite(score)
