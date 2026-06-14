import argparse
import asyncio
import logging
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


async def initial_delay(ref_sample: Path, dub_sample: Path, max_sec=None) -> tuple:
    if max_sec is None:
        max_sec = config.default_analysis_time_sec

    print_Title("Phase 1: Getting initial delay", 55)
    print("Strategy:\nFingerprints + Cross-Correlation (1 Segment)")

    start_sec = 0
    analize_time = f"  > {ms_to_timestamp(sec_to_ms(max_sec))} ({str(max_sec)} Seconds) in both Samples"

    print(f"Time to be analyzed: \n {analize_time}")
    print_subt(" # SoundFile > Loading Samples...", 55)

    try:
        ref_audio = await load_spinner(load_audio_sf, ref_sample, start_sec, max_sec, message="Ref audio")
        dub_audio = await load_spinner(load_audio_sf, dub_sample, start_sec, max_sec, message="Dub audio")
        print_subt("Analyzing Samples to Obtain Delay...", 55)
        return await find_offset_fgp(ref_audio, dub_audio)
    except AudioProcessingError as e:
        logging.error(f"Error during initial delay calculation: {e}")
        print(f"\n[ERROR] Failed to calculate initial delay: {e}")
        raise


async def segment_delays(ref_sample: Path, dub_sample: Path, delay_s1: int, max_sec=None) -> list:
    if max_sec is None:
        max_sec = config.segment_analysis_time_sec

    print_Title("Phase 2: Validating delay (segments)", 55)
    print("Strategy:\nFingerprints + Cross-Correlation (4 more Segments)")
    analize_time = f"  > {ms_to_timestamp(sec_to_ms(max_sec))} ({str(max_sec)} Seconds)"

    print(f"Time to be analyzed by segment: \n {analize_time}\n")

    def get_lowest(a: int, b: int) -> int:
        return a if a < b else b

    try:
        print('Identifying the audio with the shortest duration...')
        ref_duration_ms = get_duration(ref_sample)
        dub_duration_sec = get_duration(dub_sample)
        max_duration_sec = ms_to_seconds(get_lowest(ref_duration_ms, dub_duration_sec))
    except AudioProcessingError as e:
        logging.error(f"Error getting audio duration: {e}")
        print(f"\n[ERROR] Failed to get audio duration: {e}")
        raise

    print('Calculating all segment times...')

    def segments_times(max_duration_sec, max_sec):
        times = [
            max_duration_sec * 0.25,
            max_duration_sec // 2,
            max_duration_sec * 0.75,
            max(max_duration_sec - max_sec, 0),
        ]

        for segment, time in enumerate(times, start=2):
            yield segment, time

    segments_delay_data = []

    try:
        for segment, seg_st_time in segments_times(max_duration_sec, max_sec):

            print_subt(f" Analyzing Segment #{segment} | Start Time: {ms_to_timestamp(sec_to_ms(seg_st_time))}", 50)
            ref_audio = load_audio_sf(ref_sample, seg_st_time, max_sec)
            dub_audio = load_audio_sf(dub_sample, seg_st_time, max_sec)
            delay_ms, corr_score = await find_offset_fgp(ref_audio, dub_audio)
            segments_delay_data.append({"Start": ms_to_timestamp(sec_to_ms(seg_st_time)),
                                        "End": ms_to_timestamp(sec_to_ms(seg_st_time + max_sec)),
                                        "Delay": delay_ms})
    except AudioProcessingError as e:
        logging.error(f"Error during segment analysis: {e}")
        print(f"\n[ERROR] Failed to analyze segments: {e}")
        raise

    delays_segments = []

    if segments_delay_data:
        fisrt_seg_max_sec = config.default_analysis_time_sec
        segments_delay_data.insert(0, {"Start": ms_to_timestamp(0), "End": ms_to_timestamp(sec_to_ms(0 + fisrt_seg_max_sec)), "Delay": delay_s1})

        print_subt("### Segment delay list ###", 58, center=True)
        print("# |    Start   -   End        |  Delay\n" + "-" * 58)
        for idx, segment in enumerate(segments_delay_data, start=1):
            print(f"{idx}: |{segment.get('Start')} - {segment.get('End')}| {segment.get('Delay')} ms ({ms_to_seconds(segment.get('Delay'))}) Seconds")
            delays_segments.append(segment.get('Delay'))
    return delays_segments


def calculate_confidence(delays: list[int]) -> tuple[float, list[dict]]:
    print_Title("Phase 3: Confidence level (constant delay)", 55)
    print("Strategy: Cross-Correlation Consistency Check")
    print("* Anchor Point: Establishes base delay from first Segment")
    print("* Multi-Point Analysis: Samples 4 segments (25%, 50%, 75%, End).")
    print("* Drift Calculation: Measures variance against the anchor.")
    print("* Confidence Scoring: Detects VFR or edits via sync penalties.\n")

    if not delays:
        return 0.0, []

    drift_tolerance = config.drift_tolerance

    base_delay = delays[0]

    confidence_score = config.base_confidence

    MAX_POINTS_PER_SEGMENT = config.max_confidence_per_segment

    penalty_factors = config.penalty_factors

    out_of_sync_segments = []

    for i in range(1, len(delays)):
        current_delay = delays[i]
        diff = abs(base_delay - current_delay)

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
                "segment_index": i + 1,
                "delay_found": current_delay,
                "drift_amount": diff
            })

        confidence_score += MAX_POINTS_PER_SEGMENT * penalty_factor

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
    except SystemExit as e:
        raise

    init_delay_ms = None

    print(f"\nStarting {config.app_name} {__version__}")

    try:
        ref_file_info, dub_file_info = get_file_data(ref_file, dub_file)

        ref_sample_path, dub_sample_path = await audio_samples(ref_file_info, dub_file_info)

        init_delay_ms, corr_score = await initial_delay(ref_sample_path, dub_sample_path)

        print_subt("### Delay Results ###", 55, center=True)
        print(f" ### Correlation Score: {(corr_score)}%")
        print(f" ### Estimated Delay: ({(init_delay_ms)} ms) | ({ms_to_seconds(init_delay_ms)} Seconds)")

        if corr_score < config.confidence_threshold:
            print(f"\n Warning: No confidence found in the correlation")
            print(f"\n Case #1: The audio files are not the same (excluding dubbing and volume differences)")
            return None

        delays_segments = await segment_delays(ref_sample_path, dub_sample_path, init_delay_ms)

        total_percent, out_diff = calculate_confidence(delays_segments)

        print_subt("### Confidence level ###", 55, center=True)
        if total_percent >= 95:
            print(f" ### Delay: ({(init_delay_ms)} ms) (constant)")
            print(f" ### Confidence percentage: ({(total_percent)})")
            print("-" * 55)
            logging.info(f"Delay {init_delay_ms} file: {dub_file_info.path}")

        else:
            print(f" ### Delay: {(init_delay_ms)} ms (not constant)")
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
            print(f"\nRecommendation: Visually review the audio files")
            print(f"\nPossible causes:\nDifferent FPS\nDifferent versions")
            return None

        return init_delay_ms

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
            "  \u2022 Process two video files (reference + dubbed) (You will be asked if there is more than one audio track)\n\n"
            f"The basic analysis is limited to {config.default_analysis_time_sec // 60} minutes:\n"
            f"   (the first {config.default_analysis_time_sec} seconds of the audio files.)"
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
