import numpy as np
import librosa
from scipy import signal

from delay_check.commons import load_spinner
from delay_check.config import get_config


config = get_config()


def compute_fingerprint(audio, sr, n_fft=None, n_mfcc=None):
    if n_fft is None:
        n_fft = config.mfcc_settings['n_fft']
    if n_mfcc is None:
        n_mfcc = config.mfcc_settings['n_mfcc']

    n_mels = config.mfcc_settings['n_mels']

    mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=n_mfcc, n_fft=n_fft, n_mels=n_mels)
    return np.mean(mfcc, axis=0)


async def find_offset_fgp(reference: np.ndarray, dubbed: np.ndarray, sr=None, n_fft=None, verbose=True) -> tuple:
    if sr is None:
        sr = config.sample_rate
    if n_fft is None:
        n_fft = config.mfcc_settings['n_fft']

    hop_length = config.mfcc_settings['hop_length']

    if verbose:
        print("Computing audio fingerprints (MFCC)...")
    sig1 = await load_spinner(compute_fingerprint, reference, sr, n_fft)
    sig2 = await load_spinner(compute_fingerprint, dubbed, sr, n_fft)

    if verbose:
        print("Normalizing signals...")
    sig1 = (sig1 - np.mean(sig1)) / (np.std(sig1) + 1e-10)
    sig2 = (sig2 - np.mean(sig2)) / (np.std(sig2) + 1e-10)

    if verbose:
        print("Computing optimal offset using cross-correlation...")
    correlation = signal.correlate(sig1, sig2, mode='full', method='auto')

    lags = signal.correlation_lags(len(sig1), len(sig2), mode='full')
    max_corr_idx = np.argmax(correlation)
    lag_samples = lags[max_corr_idx]

    lag_seconds = (lag_samples * hop_length) / sr
    corr_score = correlation[max_corr_idx] / (len(sig1) * len(sig2)) ** 0.5
    corr_score = float(f"{corr_score*100:.2f}")

    if verbose:
        print(f"Found offset: {lag_seconds:.3f} seconds")
        print(f"corr_score: {corr_score} %")

    lag_ms = int(round(lag_seconds * 1000))

    return lag_ms, corr_score