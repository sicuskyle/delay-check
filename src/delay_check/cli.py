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
    clamp = max(max_duration_sec - max_sec, 0)
    for segment in range(1, n_segments + 1):
        raw_time = max_duration_sec * (segment / (n_segments + 1))
        yield segment, min(raw_time, clamp)


async def segment_delays(ref_sample: Path, dub_sample: Path, max_sec=None) -> list[dict]:
    if max_sec is None:
        max_sec = config.segment_analysis_time_sec

    n_segments = config.segments_to_analyze

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

    try:
        for segment, seg_st_time in segments_times(max_duration_sec, max_sec, n_segments):

            seg_start_ts = ms_to_timestamp(sec_to_ms(seg_st_time))
            print_subt(f" Analyzing Window #{segment} | Start Time: {seg_start_ts}", 50)
            ref_audio = await load_spinner(
                load_audio_sf, ref_sample, seg_st_time, max_sec, message="Ref audio"
            )
            dub_audio = await load_spinner(
                load_audio_sf, dub_sample, seg_st_time, max_sec, message="Dub audio"
            )
            delay_ms, corr_score = await find_offset_fgp(ref_audio, dub_audio, verbose=False)
            print(f"    Delay: {delay_ms} ms | Correlation score: {corr_score}%")
            segment_results.append({
                "Start": ms_to_timestamp(sec_to_ms(seg_st_time)),
                "End": ms_to_timestamp(sec_to_ms(seg_st_time + max_sec)),
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

        if median_delay_ms is None:
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

        else:
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
                    drift = segment.get('drift_amount')
                    print(f"    #{idx:<5} | {delay:>6}ms | {drift:>6} ms")
            print("\nRecommendation: Visually review the audio files")
            print("\nPossible causes:\nDifferent FPS\nDifferent versions")
            return None

        return median_delay_ms

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
