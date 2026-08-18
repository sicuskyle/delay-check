import numpy as np
import librosa
from scipy import signal

from delay_check.commons import load_spinner
from delay_check.config import get_config


config = get_config()

# Absolute floor (in MFCC/dB-scale units) below which a row's std is treated
# as float32 quantization noise on an otherwise-constant (e.g. silent) input
# rather than real signal, regardless of the relative floor above -- needed
# because the relative floor alone degenerates when most/all rows are
# exactly zero (median collapses to ~0, making the ratio hypersensitive to
# any nonzero row, however tiny). Real audio content's row std is orders of
# magnitude above this.
ROW_STD_ABS_FLOOR = 1e-3


def compute_fingerprint(audio, sr, n_fft=None, n_mfcc=None):
    if n_fft is None:
        n_fft = config.mfcc_settings['n_fft']
    if n_mfcc is None:
        n_mfcc = config.mfcc_settings['n_mfcc']

    n_mels = config.mfcc_settings['n_mels']

    mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=n_mfcc, n_fft=n_fft, n_mels=n_mels)
    return mfcc


def _row_zscore_and_validity(mfcc: np.ndarray) -> tuple:
    eps = 1e-10
    row_mean = mfcc.mean(axis=1, keepdims=True)
    row_std = mfcc.std(axis=1)
    normalized = (mfcc - row_mean) / (row_std[:, None] + eps)

    median_std = np.median(row_std) + eps
    floor_ratio = config.row_std_floor_ratio
    valid_rows = (row_std >= floor_ratio * median_std) & (row_std >= ROW_STD_ABS_FLOOR)

    return normalized, valid_rows


def _overlap_lengths(n1: int, n2: int, lags: np.ndarray) -> np.ndarray:
    """Number of overlapping samples between two sequences of length n1/n2
    at each lag in `lags` (as produced by `scipy.signal.correlation_lags`
    with mode='full'). Verified against a brute-force reference in tests.
    """
    return np.minimum(np.minimum(n1, n2), np.minimum(n1 - lags, n2 + lags))


def _parabolic_interpolate(y_minus1: float, y0: float, y_plus1: float) -> float:
    """Sub-frame peak refinement via parabolic interpolation (standard TDOA
    technique). Returns a fractional-frame offset in [-0.5, 0.5]; 0.0 if the
    local curve is too flat to fit a meaningful parabola.
    """
    denom = y_minus1 - 2 * y0 + y_plus1
    if abs(denom) < 1e-12:
        return 0.0
    delta = 0.5 * (y_minus1 - y_plus1) / denom
    if abs(delta) > 0.5:
        return 0.0
    return delta


async def find_offset_fgp(
    reference: np.ndarray, dubbed: np.ndarray, sr=None, n_fft=None, verbose=True
) -> tuple:
    if sr is None:
        sr = config.sample_rate
    if n_fft is None:
        n_fft = config.mfcc_settings['n_fft']

    hop_length = config.mfcc_settings['hop_length']

    if verbose:
        print("Computing audio fingerprints (MFCC)...")
    sig1 = await load_spinner(compute_fingerprint, reference, sr, n_fft)
    sig2 = await load_spinner(compute_fingerprint, dubbed, sr, n_fft)

    n_mfcc = sig1.shape[0]

    if verbose:
        print("Normalizing signals (per MFCC coefficient)...")
    sig1_z, valid1 = _row_zscore_and_validity(sig1)
    sig2_z, valid2 = _row_zscore_and_validity(sig2)
    # A row is only trusted if it carries real signal on both sides -- if
    # every row is excluded (e.g. both inputs silent), the correlation sum
    # over zero rows is naturally all-zero, giving a deterministic score of
    # 0.0 rather than needing a special-cased fallback.
    valid_rows = valid1 & valid2

    if verbose:
        print("Computing optimal offset using cross-correlation (multi-channel)...")
    n1, n2 = sig1_z.shape[1], sig2_z.shape[1]
    corr_sum = np.zeros(n1 + n2 - 1)
    for i in range(n_mfcc):
        if not valid_rows[i]:
            continue
        corr_sum += signal.correlate(sig1_z[i], sig2_z[i], mode='full', method='fft')

    lags = signal.correlation_lags(n1, n2, mode='full')
    overlap = _overlap_lengths(n1, n2, lags)

    n_contributing_rows = max(int(valid_rows.sum()), 1)
    score_curve = corr_sum / (n_contributing_rows * np.maximum(overlap, 1))

    min_overlap = max(config.min_overlap_frames, config.min_overlap_fraction * min(n1, n2))
    valid_lags = overlap >= min_overlap
    if not np.any(valid_lags):
        # Degenerate case (very short clips): no lag clears the overlap
        # floor -- fall back to considering every lag rather than crashing.
        valid_lags = np.ones_like(valid_lags)

    masked_curve = np.where(valid_lags, score_curve, -np.inf)
    max_corr_idx = int(np.argmax(masked_curve))
    lag_samples = lags[max_corr_idx]

    delta = 0.0
    has_left = max_corr_idx > 0 and valid_lags[max_corr_idx - 1]
    has_right = max_corr_idx < len(masked_curve) - 1 and valid_lags[max_corr_idx + 1]
    if has_left and has_right:
        delta = _parabolic_interpolate(
            score_curve[max_corr_idx - 1],
            score_curve[max_corr_idx],
            score_curve[max_corr_idx + 1],
        )

    lag_seconds = ((lag_samples + delta) * hop_length) / sr
    corr_score = float(np.clip(score_curve[max_corr_idx], 0.0, 1.0)) * 100
    corr_score = float(f"{corr_score:.2f}")

    if verbose:
        print(f"Found offset: {lag_seconds:.3f} seconds")
        print(f"corr_score: {corr_score} %")

    lag_ms = int(round(lag_seconds * 1000))

    return lag_ms, corr_score
