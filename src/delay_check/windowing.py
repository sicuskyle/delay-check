import logging
from pathlib import Path

from delay_check.config import get_config
from delay_check.commons import print_Title, print_subt, load_spinner
from delay_check.utils import (
    ms_to_timestamp, ms_to_seconds, sec_to_ms, get_duration, load_audio_sf,
    AudioProcessingError
)
from delay_check.fgp import find_offset_fgp
from delay_check.drift import linear_fit


config = get_config()


def get_lowest(a: int, b: int) -> int:
    return a if a < b else b


def segments_times(max_duration_sec, max_sec, n_segments):
    span = max(max_duration_sec - max_sec, 0)
    if n_segments <= 1:
        yield 1, 0.0
        return
    for segment in range(1, n_segments + 1):
        yield segment, span * (segment - 1) / (n_segments - 1)


def _predict_delay_ms(anchors: list[tuple[float, float]], t: float) -> float:
    """Predicts the delay (ms) expected at time t (seconds) from previously
    confirmed (time_sec, delay_ms) anchors: zero-order hold with 0-1 anchors,
    linear extrapolation of the running trend with 2+.

    Used only as a sanity check against the naive (unshifted) correlation and,
    when that check fails, as the basis for a pre-shifted retry -- see
    segment_delays. Not applied unconditionally, so a window whose naive
    result already agrees with the trend is reported exactly as it would be
    without any tracking at all.
    """
    if not anchors:
        return 0.0
    if len(anchors) == 1:
        return anchors[0][1]
    xs = [a[0] for a in anchors]
    ys = [a[1] for a in anchors]
    slope, intercept, _ = linear_fit(xs, ys)
    return slope * t + intercept


def _shifted_starts(seg_st_time: float, predicted_delay_ms: float) -> tuple[float, float]:
    """Pre-compensates the read start of whichever side is "ahead" by the
    predicted delay, so find_offset_fgp only has to resolve the residual
    error in the prediction rather than the full accumulated delay. Sign
    convention matches find_offset_fgp's own Delay output: positive ->
    ref leads (ref reads further into itself), negative -> dub leads.
    """
    shift_sec = abs(predicted_delay_ms) / 1000.0
    if predicted_delay_ms >= 0:
        return seg_st_time + shift_sec, seg_st_time
    return seg_st_time, seg_st_time + shift_sec


# A naive (unshifted) window whose delay differs from the tracked trend by
# more than this fraction of the window's own length is treated as having hit
# the correlation window's overlap-capacity limit (see min_overlap_fraction in
# fgp.py) rather than reflecting real per-window variance -- real edits/VFR
# jitter stays within tens to a few hundred ms, far below this. Only windows
# that cross it get retried with a pre-shifted read.
TRACKING_DEVIATION_FRACTION = 0.2


async def _correlate_window(
    ref_sample: Path, dub_sample: Path, ref_start: float, dub_start: float, max_sec
) -> tuple:
    ref_audio = await load_spinner(
        load_audio_sf, ref_sample, ref_start, max_sec, message="Ref audio"
    )
    dub_audio = await load_spinner(
        load_audio_sf, dub_sample, dub_start, max_sec, message="Dub audio"
    )
    return await find_offset_fgp(ref_audio, dub_audio, verbose=False)


async def segment_delays(ref_sample: Path, dub_sample: Path, max_sec=None) -> list[dict]:
    if max_sec is None:
        max_sec = config.segment_analysis_time_sec

    n_segments = config.segments_to_analyze
    confidence_threshold = config.confidence_threshold
    deviation_cap_ms = max_sec * 1000 * TRACKING_DEVIATION_FRACTION

    print_Title("Phase 1: Analyzing windows across the file", 55)
    print(f"Strategy:\nFingerprints + Cross-Correlation ({n_segments} windows)")
    analize_time = f"  > {ms_to_timestamp(sec_to_ms(max_sec))} ({str(max_sec)} Seconds)"

    print(f"Time to be analyzed per window: \n {analize_time}\n")

    try:
        print('Identifying the audio with the shortest duration...')
        ref_duration_ms = get_duration(ref_sample)
        dub_duration_sec = get_duration(dub_sample)
        max_duration_sec = ms_to_seconds(get_lowest(ref_duration_ms, dub_duration_sec))
    except AudioProcessingError as e:
        logging.error(f"Error getting audio duration: {e}")
        print(f"\n[ERROR] Failed to get audio duration: {e}")
        raise

    print('Calculating all window times...')

    segment_results = []
    anchors: list[tuple[float, float]] = []

    try:
        for segment, seg_st_time in segments_times(max_duration_sec, max_sec, n_segments):

            seg_start_ts = ms_to_timestamp(sec_to_ms(seg_st_time))
            print_subt(f" Analyzing Window #{segment} | Start Time: {seg_start_ts}", 50)

            delay_ms, corr_score = await _correlate_window(
                ref_sample, dub_sample, seg_st_time, seg_st_time, max_sec
            )

            if anchors:
                predicted_delay_ms = _predict_delay_ms(anchors, seg_st_time)
                if abs(delay_ms - predicted_delay_ms) > deviation_cap_ms:
                    print(
                        f"    Naive delay ({delay_ms} ms) diverges from tracked "
                        f"trend ({predicted_delay_ms:.0f} ms)\n"
                        f"    -- retrying with pre-shifted read"
                    )
                    ref_start, dub_start = _shifted_starts(seg_st_time, predicted_delay_ms)
                    residual_ms, retry_score = await _correlate_window(
                        ref_sample, dub_sample, ref_start, dub_start, max_sec
                    )
                    delay_ms = int(round(predicted_delay_ms)) + residual_ms
                    corr_score = retry_score

            print(f"    Delay: {delay_ms} ms | Correlation score: {corr_score}%")

            if corr_score >= confidence_threshold:
                anchors.append((seg_st_time, float(delay_ms)))

            segment_results.append({
                "Start": ms_to_timestamp(sec_to_ms(seg_st_time)),
                "End": ms_to_timestamp(sec_to_ms(seg_st_time + max_sec)),
                "StartSec": float(seg_st_time),
                "EndSec": float(seg_st_time + max_sec),
                "Delay": delay_ms,
                "Score": corr_score,
            })
    except AudioProcessingError as e:
        logging.error(f"Error during window analysis: {e}")
        print(f"\n[ERROR] Failed to analyze windows: {e}")
        raise

    print_subt("### Window delay list ###", 58, center=True)
    print(f"{'#':>2} |    Start   -   End        | {'Delay':>9} | {'Score':>6}\n" + "-" * 58)
    for idx, segment in enumerate(segment_results, start=1):
        print(
            f"{idx:>2}: |{segment['Start']} - {segment['End']}| "
            f"{segment['Delay']:>7} ms | {segment['Score']:>6}%"
        )

    return segment_results
