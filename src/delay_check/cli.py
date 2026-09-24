import argparse
import asyncio
import logging
import sys
from pathlib import Path

from delay_check import __version__
from delay_check.config import get_config, ConfigError
from delay_check.commons import print_Title, print_subt, load_spinner
from delay_check.utils import (
    initialize, extension_lists, ms_to_seconds,
    check_file, get_samples,
    FileValidationError, AudioProcessingError
)
from delay_check.windowing import segment_delays
from delay_check.confidence import aggregate_delay, calculate_confidence
from delay_check.drift import analyze_timebase_drift, print_timebase_drift
from delay_check.fileinfo import FileInfo


try:
    config = get_config()
except ConfigError as e:
    print(f"[ERROR] Configuration error: {e}")
    input('<press enter to exit>')
    sys.exit(1)


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
                    "\n the delay changes linearly over time."
                )
                logging.info(
                    "Timebase drift: %.3f%% (%s ppm) dub %s, slope %.3f ms/s",
                    drift["percent"], drift["ppm"],
                    drift["direction"].lower(), drift["slope_ms_per_s"],
                )
                return None

            print_subt("Warning: No confidence found in the correlation", 55, center=True)
            print(
                "\n Case: No reliable constant delay found between these audio files.\n"
                " Possible causes:\n"
                "  1. The audio files are not related content\n"
                "  2. Different cuts/versions (Director's Cut, Extended, Theatrical,\n"
                "    Unrated, etc.) with added/removed/reordered scenes or different music"
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
            "  • Process two audio files (reference + dubbed)\n"
            "  • Process two video files (reference + dubbed) "
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
