import argparse
import asyncio
import logging
import statistics
import sys
from pathlib import Path

from delay_check import __version__
from delay_check.config import get_config, ConfigError
from delay_check.commons import print_Title, print_subt, load_spinner
from delay_check.utils import (
    initialize, extension_lists, ms_to_timestamp, ms_to_seconds, sec_to_ms,
    get_duration, check_file, get_samples, load_audio_sf,
    FileValidationError, AudioProcessingError
)
from delay_check.fgp import find_offset_fgp
from delay_check.fileinfo import FileInfo


try:
    config = get_config()
except ConfigError as e:
    print(f"[ERROR] Configuration error: {e}")
    input('<press enter to exit>')
    sys.exit(1)


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

    This is what lets later windows track an arbitrarily large accumulated
    delay: each window only has to resolve the residual error in this
    prediction (typically small, since consecutive segments' actual drift
    delta is tiny compared to the total accumulated over the file), not the
    full delay -- so the window's own correlation-window size no longer caps
    how much cumulative timebase drift can be tracked end to end.
    """
    if not anchors:
        return 0.0
    if len(anchors) == 1:
        return anchors[0][1]
    xs = [a[0] for a in anchors]
    ys = [a[1] for a in anchors]
    slope, intercept, _ = _linear_fit(xs, ys)
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


async def segment_delays(ref_sample: Path, dub_sample: Path, max_sec=None) -> list[dict]:
    if max_sec is None:
        max_sec = config.segment_analysis_time_sec

    n_segments = config.segments_to_analyze
    confidence_threshold = config.confidence_threshold

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

            predicted_delay_ms = _predict_delay_ms(anchors, seg_st_time)
            ref_start, dub_start = _shifted_starts(seg_st_time, predicted_delay_ms)

            seg_start_ts = ms_to_timestamp(sec_to_ms(seg_st_time))
            print_subt(f" Analyzing Window #{segment} | Start Time: {seg_start_ts}", 50)
            if predicted_delay_ms:
                print(f"    Tracking prediction: {predicted_delay_ms:.0f} ms (pre-shifting read start)")
            ref_audio = await load_spinner(
                load_audio_sf, ref_sample, ref_start, max_sec, message="Ref audio"
            )
            dub_audio = await load_spinner(
                load_audio_sf, dub_sample, dub_start, max_sec, message="Dub audio"
            )
            residual_ms, corr_score = await find_offset_fgp(ref_audio, dub_audio, verbose=False)
            delay_ms = int(round(predicted_delay_ms)) + residual_ms
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
    print("# |    Start   -   End        |  Delay      | Score\n" + "-" * 58)
    for idx, segment in enumerate(segment_results, start=1):
        print(
            f"{idx}: |{segment['Start']} - {segment['End']}| "
            f"{segment['Delay']} ms | {segment['Score']}%"
        )

    return segment_results


def _largest_agreement_cluster(delays: list[int], tolerance: int) -> list[int]:
    ordered = sorted(delays)
    clusters = []
    current_cluster = [ordered[0]]
    for delay in ordered[1:]:
        if delay - current_cluster[-1] <= tolerance:
            current_cluster.append(delay)
        else:
            clusters.append(current_cluster)
            current_cluster = [delay]
    clusters.append(current_cluster)
    return max(clusters, key=len)


def aggregate_delay(segment_results: list[dict]) -> tuple:
    if not segment_results:
        return None, []

    confidence_threshold = config.confidence_threshold
    correlated_delays = [
        s["Delay"] for s in segment_results if s["Score"] >= confidence_threshold
    ]

    if correlated_delays:
        return int(round(statistics.median(correlated_delays))), correlated_delays

    # No single window individually clears the score threshold -- this is
    # common for real dubbed content, where only part of a window's audio
    # (shared music/effects, not the re-recorded dialogue) actually
    # correlates, capping the per-window score even for a correct match.
    # Fall back to cross-window consensus: many independent windows tightly
    # agreeing on the same delay is itself strong evidence, regardless of
    # their individual scores -- the chance of that happening for unrelated
    # audio is far lower than any single window's score being spuriously
    # high.
    all_delays = [s["Delay"] for s in segment_results]
    cluster = _largest_agreement_cluster(all_delays, config.drift_tolerance['excellent'])

    if len(cluster) > len(segment_results) / 2:
        return int(round(statistics.median(cluster))), cluster

    return None, []


def calculate_confidence(delays: list[int], anchor: int | None = None) -> tuple[float, list[dict]]:
    print_Title("Phase 2: Confidence level (constant delay)", 55)
    print("Strategy: Cross-Correlation Consistency Check")
    if anchor is None:
        print("* Anchor Point: Establishes base delay from first Segment")
    else:
        print("* Anchor Point: Median delay across correlated windows")
    print(f"* Multi-Point Analysis: Samples {config.segments_to_analyze} windows across the file.")
    print("* Drift Calculation: Measures variance against the anchor.")
    print("* Confidence Scoring: Detects VFR or edits via sync penalties.\n")

    if not delays:
        return 0.0, []

    drift_tolerance = config.drift_tolerance
    base_confidence = config.base_confidence
    penalty_factors = config.penalty_factors

    if anchor is None:
        # delays[0] is the anchor itself and isn't re-scored against itself;
        # scored_delays[0] (delays[1]) is window #2, so segment_index = i+1.
        anchor = delays[0]
        scored_delays = delays[1:]
        max_per_segment = config.max_confidence_per_segment
        index_offset = 1
    else:
        # Every real window (including delays[0], window #1) is scored
        # against the external anchor, so segment_index = i.
        scored_delays = delays
        max_per_segment = (100.0 - base_confidence) / len(delays)
        index_offset = 0

    confidence_score = base_confidence
    out_of_sync_segments = []

    for i, current_delay in enumerate(scored_delays, start=1):
        diff = abs(anchor - current_delay)

        if diff <= drift_tolerance['excellent']:
            penalty_factor = penalty_factors['excellent']
        elif diff <= drift_tolerance['good']:
            penalty_factor = penalty_factors['good']
        elif diff <= drift_tolerance['acceptable']:
            penalty_factor = penalty_factors['acceptable']
        elif diff <= drift_tolerance['poor']:
            penalty_factor = penalty_factors['poor']
        else:
            penalty_factor = penalty_factors['failed']
            out_of_sync_segments.append({
                "segment_index": i + index_offset,
                "delay_found": current_delay,
                "drift_amount": diff
            })

        confidence_score += max_per_segment * penalty_factor

    return round(confidence_score, 2), out_of_sync_segments


DRIFT_MIN_R2 = 0.9
DRIFT_MIN_SLOPE_MS_PER_S = 0.5


def _linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    if ss_xx == 0:
        return 0.0, mean_y, 0.0
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    r_squared = 1.0 if ss_tot == 0 else max(0.0, 1.0 - ss_res / ss_tot)
    return slope, intercept, r_squared


def analyze_timebase_drift(segment_results: list[dict]) -> dict | None:
    if len(segment_results) < 3:
        return None
    if not all("StartSec" in s and "EndSec" in s for s in segment_results):
        return None

    xs = [(s["StartSec"] + s["EndSec"]) / 2 for s in segment_results]
    ys = [float(s["Delay"]) for s in segment_results]

    slope, _, r_squared = _linear_fit(xs, ys)

    if r_squared < DRIFT_MIN_R2 or abs(slope) < DRIFT_MIN_SLOPE_MS_PER_S:
        return None

    rate = slope / 1000.0
    percent = rate * 100.0
    ppm = int(round(rate * 1_000_000))
    atempo = 1.0 / (1.0 + rate)

    return {
        "slope_ms_per_s": slope,
        "r_squared": r_squared,
        "percent": abs(percent),
        "ppm": abs(ppm),
        "direction": "FASTER" if slope > 0 else "SLOWER",
        "atempo": atempo,
    }


def print_timebase_drift(drift: dict) -> None:
    print_subt("### Timebase drift Detected ###", 55, center=True)
    print(
        f" ### Dubbed is {drift['percent']:.3f}% ({drift['ppm']} ppm) "
        f"{drift['direction']} than reference"
    )
    print(f" ### Delay slope: {drift['slope_ms_per_s']:+.3f} ms per second")
    print(" ### Recommendation: apply tempo correction, not a fixed offset")
    print(
        f' ### Suggested: ffmpeg -i dubbed -af "atempo={drift["atempo"]:.5f}" output'
    )


def get_file_data(ref_file: Path, dub_file: Path) -> tuple[FileInfo, FileInfo]:
    print_Title("Getting file information")
    try:
        ref_file_info = check_file(ref_file, filetype="reference")
        dub_file_info = check_file(dub_file, filetype="dubbed")
        return ref_file_info, dub_file_info
    except (FileValidationError, AudioProcessingError) as e:
        logging.error(f"Error getting file data: {e}")
        raise


async def audio_samples(ref_file: FileInfo, dub_file: FileInfo):
    print_Title("Generating samples", 55)

    print("""Description:
    A monophonic sample in .wav format will be
    generated from both audio files using ffmpeg:
    acodec {} and ar {}""".format(config.audio_codec, config.sample_rate))

    print_subt(" # FFmpeg > Generating Samples...", 55)
    try:
        ref_sample_path = await load_spinner(get_samples, ref_file, message="Ref audio")
        dub_sample_path = await load_spinner(get_samples, dub_file, message="Dub audio")
        return ref_sample_path, dub_sample_path
    except AudioProcessingError as e:
        logging.error(f"Error generating audio samples: {e}")
        raise


async def delay_check():
    try:
        ref_file, dub_file = initargs()
    except (FileValidationError, AudioProcessingError) as e:
        print(f"\n[ERROR] {e}")
        return None
    except SystemExit:
        raise

    print(f"\nStarting {config.app_name} {__version__}")

    try:
        ref_file_info, dub_file_info = get_file_data(ref_file, dub_file)

        ref_sample_path, dub_sample_path = await audio_samples(ref_file_info, dub_file_info)

        segment_results = await segment_delays(ref_sample_path, dub_sample_path)

        median_delay_ms, correlated_delays = aggregate_delay(segment_results)
        drift = analyze_timebase_drift(segment_results)

        if median_delay_ms is None:
            if drift is not None:
                print_timebase_drift(drift)
                print(
                    "\n No constant delay could be estimated: "
                    "the delay changes linearly over time."
                )
                logging.info(
                    "Timebase drift: %.3f%% (%s ppm) dub %s, slope %.3f ms/s",
                    drift["percent"], drift["ppm"],
                    drift["direction"].lower(), drift["slope_ms_per_s"],
                )
                return None
            print("\n Warning: No confidence found in the correlation")
            print(
                "\n Case #1: The audio files are not the same "
                "(excluding dubbing and volume differences)"
            )
            return None

        print_subt("### Delay Results ###", 55, center=True)
        print(f" ### Correlated windows: {len(correlated_delays)}/{len(segment_results)}")
        print(
            f" ### Estimated Delay: ({median_delay_ms} ms) "
            f"| ({ms_to_seconds(median_delay_ms)} Seconds)"
        )

        all_delays = [segment["Delay"] for segment in segment_results]
        total_percent, out_diff = calculate_confidence(all_delays, anchor=median_delay_ms)

        print_subt("### Confidence level ###", 55, center=True)
        if total_percent >= 95:
            print(f" ### Delay: ({median_delay_ms} ms) (constant)")
            print(f" ### Confidence percentage: ({(total_percent)})")
            print("-" * 55)
            logging.info(f"Delay {median_delay_ms} file: {dub_file_info.path}")
            return median_delay_ms

        print(f" ### Delay: {median_delay_ms} ms (not constant)")
        print(f" ### Confidence percentage: ({(total_percent)})")
        print("-" * 55)
        if out_diff:
            print(f"\nDifferences > {config.drift_tolerance['poor']} ms found:\n")
            print("Segment # | Delay     | Drift")
            print("-" * 55)
            for segment in out_diff:
                idx = segment.get('segment_index')
                delay = segment.get('delay_found')
                drift_amount = segment.get('drift_amount')
                print(f"    #{idx:<5} | {delay:>6}ms | {drift_amount:>6} ms")
        if drift is not None:
            print_timebase_drift(drift)
            logging.info(
                "Timebase drift: %.3f%% (%s ppm) dub %s, slope %.3f ms/s",
                drift["percent"], drift["ppm"],
                drift["direction"].lower(), drift["slope_ms_per_s"],
            )
        else:
            print("\nRecommendation: Visually review the audio files")
            print("\nPossible causes:\nDifferent FPS\nDifferent versions")
        return None

    except (FileValidationError, AudioProcessingError) as e:
        print(f"\n[ERROR] Process failed: {e}")
        logging.error(f"Delay check failed: {e}")
        return None
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        logging.exception("Unexpected error during delay check")
        return None


def main():
    initialize()

    try:
        result = asyncio.run(delay_check())
        if result is not None:
            print_subt("Process completed successfully.", 55, center=True)
        else:
            print_subt("Process completed with warnings.", 55, center=True)
    except KeyboardInterrupt:
        if sys.stderr:
            print("\nProcess aborted by user!")
        sys.exit(130)
    except Exception as e:
        print(f"\n[ERROR] Fatal error: {e}")
        logging.exception("Fatal error")
        sys.exit(1)


def initargs() -> tuple[Path, Path]:
    parser = argparse.ArgumentParser(
        prog="delay-check",
        usage=(
            "delay-check [reference] [dubbed]\n"
            "       delay-check -h              show full help"
        ),
        add_help=False,
        description=(
            f"{config.app_name}\n\n"
            "Tool to calculate audio delay between a reference track and a dubbed track.\n\n"
            "You can either:\n"
            "  \u2022 Process two audio files (reference + dubbed)\n"
            "  \u2022 Process two video files (reference + dubbed) "
            "(You will be asked if there is more than one audio track)\n\n"
            f"The analysis samples {config.segments_to_analyze} windows of "
            f"{config.segment_analysis_time_sec} seconds each, spread across the files."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  delay-check reference_audio.wav dubbed_audio.wav\n\n"
            "  delay-check reference_video.mkv dubbed_video.mkv\n\n"
            f"Supported formats: {', '.join(config.supported_extensions)}"
        )
    )

    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {__version__}"
    )

    parser.add_argument(
        "-h", "--help",
        action="help",
        help="show full help"
    )

    parser.add_argument(
        "reference", nargs="?", help="Reference audio or video file"
    )

    parser.add_argument(
        "dubbed", nargs="?", help="Dubbed audio or video file"
    )

    args = parser.parse_args()

    REFERENCE = args.reference
    DUBBED = args.dubbed

    if not REFERENCE:
        parser.error("reference file not specified")

    if not DUBBED:
        parser.error("dubbed file not specified")

    for path, label in [(REFERENCE, "Reference"), (DUBBED, "Dubbed")]:
        org_path = Path(path)

        if not org_path.exists():
            parser.error(
                f'\n{label.upper()} file: "{org_path}" does not exist or is not valid!'
            )

        if org_path.suffix.lower() not in extension_lists("supported"):
            parser.error(
                f"{label}'s extension -> ({org_path.suffix}) is not supported!\n"
                f"Supported files: {', '.join(extension_lists('supported'))}"
            )

        if not org_path.is_file():
            parser.error(f'"{org_path}" is not a valid file')

    return REFERENCE, DUBBED


if __name__ == "__main__":
    main()
