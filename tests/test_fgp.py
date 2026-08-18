import asyncio

import numpy as np
import pytest

from scipy import signal as sp_signal

from delay_check import fgp
from delay_check.fgp import (
    compute_fingerprint, find_offset_fgp, _overlap_lengths, _parabolic_interpolate
)

from .helpers import make_base_signal, make_delayed_pair, make_unrelated_pair

# The raw correlation-lag grid is hop_length/sr ~= 512/44100 ~= 11.6ms, but
# sub-frame parabolic interpolation (Phase 3) refines within that grid --
# measured error is now 0-2ms across both grid-aligned and non-grid-aligned
# delays. 5ms keeps a comfortable margin above that.
LAG_TOLERANCE_MS = 5


def _find_offset(ref, dub):
    return asyncio.run(find_offset_fgp(ref, dub, verbose=False))


class TestComputeFingerprint:
    def test_shape_is_two_dimensional(self):
        audio = make_base_signal(duration_sec=2.0)
        fp = compute_fingerprint(audio, sr=44100)
        assert fp.ndim == 2
        assert fp.shape[0] == 13  # default n_mfcc
        assert fp.shape[1] > 1


class TestFindOffsetKnownDelay:
    @pytest.mark.parametrize(
        "delay_ms", [0, 250, 1000, -250, -1000, 3000, -3000]
    )
    def test_recovers_known_delay(self, delay_ms):
        ref, dub, expected_lag_ms = make_delayed_pair(delay_ms, duration_sec=10.0)
        lag_ms, score = _find_offset(ref, dub)
        assert abs(lag_ms - expected_lag_ms) <= LAG_TOLERANCE_MS
        # Overlap-aware normalization (Phase 2) measures ~95-100 on clean
        # matches (up from Phase 1's ~64-95, which was diluted by dividing
        # by a constant instead of the true per-lag overlap).
        assert score > 90

    def test_gain_shifted_dub_is_still_recovered(self):
        ref, dub, expected_lag_ms = make_delayed_pair(1000, gain_db=-6, duration_sec=10.0)
        lag_ms, score = _find_offset(ref, dub)
        assert abs(lag_ms - expected_lag_ms) <= LAG_TOLERANCE_MS
        assert score > 90

    def test_bandpass_filtered_dub_is_still_recovered(self):
        ref, dub, expected_lag_ms = make_delayed_pair(
            1000, bandpass_hz=(1000, 4000), duration_sec=10.0
        )
        lag_ms, score = _find_offset(ref, dub)
        assert abs(lag_ms - expected_lag_ms) <= LAG_TOLERANCE_MS
        # Measured ~83 with overlap-aware normalization (up from ~37-41
        # with Phase 1's temporary formula).
        assert score > 70

    @pytest.mark.parametrize("snr_db,min_score", [(10, 65), (0, 55)])
    def test_degrades_gracefully_with_additive_noise(self, snr_db, min_score):
        ref, dub, expected_lag_ms = make_delayed_pair(
            1000, noise_snr_db=snr_db, duration_sec=10.0
        )
        lag_ms, score = _find_offset(ref, dub)
        assert abs(lag_ms - expected_lag_ms) <= LAG_TOLERANCE_MS
        # Measured ~77 (10dB) / ~68 (0dB) with overlap-aware normalization
        # (up from ~32/~26 with Phase 1's temporary formula). Lag recovery
        # is accurate at both levels; only confidence changes with SNR.
        assert score > min_score

    @pytest.mark.parametrize("delay_ms", [253, 1005, -257, 47, -613, 1999, -33, 3011])
    def test_recovers_non_grid_aligned_delay(self, delay_ms):
        # Delays deliberately NOT aligned to the ~11.6ms hop grid -- only
        # accurately recoverable once sub-frame interpolation (Phase 3)
        # refines the integer-frame lag. Measured error 0-2ms.
        ref, dub, expected_lag_ms = make_delayed_pair(delay_ms, duration_sec=10.0)
        lag_ms, score = _find_offset(ref, dub)
        assert abs(lag_ms - expected_lag_ms) <= LAG_TOLERANCE_MS
        assert score > 90

    def test_small_overlap_near_segment_boundary_does_not_crash(self):
        # Delay close to the clip duration -> little true overlap (10% for
        # this 2700ms delay in a 3s clip). Overlap-aware normalization
        # (Phase 2) fixed the argmax bias that used to pick a wrong lag
        # here -- measured error dropped from >2000ms (unmasked, pre-Phase-2)
        # to ~150ms. Some residual imprecision at this extreme a ratio is
        # expected (few frames to average over), so this is not as tight as
        # the other known-delay tests.
        ref, dub, expected_lag_ms = make_delayed_pair(2700, duration_sec=3.0)
        lag_ms, score = _find_offset(ref, dub)
        assert abs(lag_ms - expected_lag_ms) <= 200
        assert score > 50


class TestFindOffsetUnrelatedSignals:
    @pytest.mark.parametrize(
        "seed_a,seed_b",
        [(1, 2), (5, 9), (42, 43), (3, 4), (7, 8), (11, 13), (20, 21)],
    )
    def test_unrelated_signals_score_lower_than_true_matches(self, seed_a, seed_b):
        ref, dub = make_unrelated_pair(duration_sec=6.0, seed_a=seed_a, seed_b=seed_b)
        _, score = _find_offset(ref, dub)
        # Unrelated pairs measure up to ~47; degraded-but-true matches sit
        # at ~55+ (noise/EQ tests above) and clean matches at ~90+, so 55
        # keeps a clear margin above the measured unrelated-pair ceiling
        # without encroaching on genuine (if degraded) matches.
        assert score < 55


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


def _brute_force_overlap(n1, n2, lags):
    counts = []
    for lag in lags:
        count = 0
        for i in range(n1):
            j = i - lag
            if 0 <= j < n2:
                count += 1
        counts.append(count)
    return np.array(counts)


class TestOverlapLengths:
    @pytest.mark.parametrize(
        "n1,n2", [(5, 3), (3, 5), (4, 4), (1, 1), (10, 7), (7, 10), (2, 9)]
    )
    def test_matches_brute_force_reference(self, n1, n2):
        lags = sp_signal.correlation_lags(n1, n2, mode='full')
        expected = _brute_force_overlap(n1, n2, lags)
        got = _overlap_lengths(n1, n2, lags)
        assert np.array_equal(got, expected)


class TestParabolicInterpolate:
    def test_symmetric_peak_gives_zero_offset(self):
        assert _parabolic_interpolate(1.0, 4.0, 1.0) == 0.0

    def test_asymmetric_peak_matches_hand_calculation(self):
        # denom = 1 - 2*4 + 3 = -4; delta = 0.5*(1-3)/-4 = 0.25
        assert _parabolic_interpolate(1.0, 4.0, 3.0) == pytest.approx(0.25)
        # Mirrored case should flip sign.
        assert _parabolic_interpolate(3.0, 4.0, 1.0) == pytest.approx(-0.25)

    def test_flat_curve_gives_zero_offset_without_dividing_by_zero(self):
        assert _parabolic_interpolate(2.0, 2.0, 2.0) == 0.0


class TestRowVarianceGuard:
    def test_near_constant_row_does_not_corrupt_result(self, monkeypatch):
        ref, dub, expected_lag_ms = make_delayed_pair(1000, duration_sec=10.0)

        original_compute = fgp.compute_fingerprint

        def flattened_compute(audio, sr, n_fft=None, n_mfcc=None):
            mfcc = original_compute(audio, sr, n_fft, n_mfcc).copy()
            mfcc[0, :] = mfcc[0, 0]  # force one coefficient row to be exactly constant
            return mfcc

        monkeypatch.setattr(fgp, "compute_fingerprint", flattened_compute)
        lag_flat, score_flat = _find_offset(ref, dub)

        monkeypatch.setattr(fgp, "compute_fingerprint", original_compute)
        lag_normal, score_normal = _find_offset(ref, dub)

        assert lag_flat == lag_normal
        # Excluding one degenerate row costs only a modest amount of
        # confidence, not a collapse -- the guard should not amplify the
        # constant row into a spurious dominant signal.
        assert abs(score_flat - score_normal) < 10
