import numpy as np
from scipy import signal as sp_signal

BAND_EDGES_HZ = [
    (200, 500), (500, 900), (900, 1400), (1400, 2000),
    (2000, 2700), (2700, 3500), (3500, 4500), (4500, 5700),
    (5700, 7000), (7000, 8500), (8500, 10000), (10000, 12000),
]


def _bandpass(sig: np.ndarray, sr: int, low_hz: float, high_hz: float) -> np.ndarray:
    nyq = sr / 2
    low = max(low_hz / nyq, 1e-4)
    high = min(high_hz / nyq, 0.999)
    b, a = sp_signal.butter(4, [low, high], btype="band")
    return sp_signal.lfilter(b, a, sig).astype(np.float32)


def make_base_signal(sr: int = 44100, duration_sec: float = 6.0, seed: int = 42) -> np.ndarray:
    """Deterministic sequence of band-limited noise bursts (not a pure tone,
    not white noise) so it has genuine, reproducible spectral/temporal
    structure to fingerprint and correlate against.

    The per-burst frequency band, gain, AND duration are all drawn from the
    seeded RNG (not cycled in a fixed schedule/fixed length), so that two
    different seeds produce genuinely different envelope/spectral-timing
    structure rather than just different noise instantiations under an
    identical shared schedule -- otherwise two "unrelated" signals could
    share enough amplitude-envelope/band structure to spuriously correlate
    over short windows. A wide bank of 12 narrow bands (vs. few wide ones)
    further reduces the chance of two independent signals picking the same
    band in the same time window.
    """
    rng = np.random.default_rng(seed)
    total_samples = int(duration_sec * sr) + sr  # small buffer, trimmed below
    chunks = []
    samples_so_far = 0
    while samples_so_far < total_samples:
        burst_sec = rng.uniform(0.3, 0.7)
        n_samples = int(burst_sec * sr)
        low, high = BAND_EDGES_HZ[rng.integers(0, len(BAND_EDGES_HZ))]
        gain = rng.uniform(0.4, 1.0)
        noise = rng.standard_normal(n_samples).astype(np.float32)
        chunks.append((_bandpass(noise, sr, low, high) * gain).astype(np.float32))
        samples_so_far += n_samples
    sig = np.concatenate(chunks)[: int(duration_sec * sr)]
    peak = np.max(np.abs(sig)) + 1e-9
    return (sig / peak).astype(np.float32)


def make_delayed_pair(
    delay_ms: int,
    sr: int = 44100,
    duration_sec: float = 6.0,
    gain_db: float = 0.0,
    bandpass_hz: tuple | None = None,
    noise_snr_db: float | None = None,
    seed: int = 42,
):
    """Build a (reference, dubbed) pair where `dubbed` lags `reference` by
    `delay_ms` (negative delay_ms means dubbed leads/arrives earlier).

    Delay is applied via zero-padding + slicing (never np.roll, which wraps
    the signal and creates spurious edge-boundary matches).

    Returns (ref, dub, expected_lag_ms) where expected_lag_ms is what
    `find_offset_fgp(ref, dub)` is expected to report, matching that
    function's own sign convention (empirically: dub lagging ref by +D ms
    is reported as a lag of approximately -D ms).
    """
    delay_samples = int(round(delay_ms / 1000.0 * sr))
    pad = abs(delay_samples)
    extra_sec = (pad / sr) + 1.0
    base = make_base_signal(sr=sr, duration_sec=duration_sec + extra_sec, seed=seed)
    n = int(duration_sec * sr)

    if delay_samples >= 0:
        ref = base[:n].copy()
        dub = np.concatenate([np.zeros(delay_samples, dtype=np.float32), base])[:n]
    else:
        dub = base[:n].copy()
        ref = np.concatenate([np.zeros(pad, dtype=np.float32), base])[:n]

    if bandpass_hz is not None:
        low, high = bandpass_hz
        dub = _bandpass(dub, sr, low, high)

    gain = 10 ** (gain_db / 20.0)
    dub = (dub * gain).astype(np.float32)

    if noise_snr_db is not None:
        rng = np.random.default_rng(seed + 1000)
        sig_power = np.mean(dub**2) + 1e-12
        noise_power = sig_power / (10 ** (noise_snr_db / 10.0))
        noise = rng.standard_normal(len(dub)).astype(np.float32) * np.sqrt(noise_power)
        dub = (dub + noise).astype(np.float32)

    expected_lag_ms = -delay_ms
    return ref, dub, expected_lag_ms


def make_unrelated_pair(
    sr: int = 44100, duration_sec: float = 6.0, seed_a: int = 1, seed_b: int = 2
):
    a = make_base_signal(sr=sr, duration_sec=duration_sec, seed=seed_a)
    b = make_base_signal(sr=sr, duration_sec=duration_sec, seed=seed_b)
    return a, b
