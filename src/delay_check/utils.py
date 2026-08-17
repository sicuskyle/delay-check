import os
import sys
import logging
import soundfile as sf
import numpy as np
from pathlib import Path
from typing import Optional
from pymediainfo import MediaInfo

from delay_check.commons import mk_temp, check_tool, run_cmd
from delay_check.config import get_config
from delay_check.fileinfo import FileInfo


class AudioProcessingError(Exception):
    pass


class FileValidationError(Exception):
    pass


ffmpeg_cmd: Optional[str] = None
temp_dir: Optional[Path] = None


def setup_logging():
    config = get_config()
    log_file = config.log_file
    log_level = getattr(logging, config.log_level.upper(), logging.INFO)
    log_format = config.log_format

    logger = logging.getLogger()
    logger.setLevel(log_level)
    logger.handlers.clear()

    try:
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter(log_format))
        logger.addHandler(file_handler)
    except IOError as e:
        print(f"Warning: Could not create log file {log_file}: {e}")


def initialize():
    global ffmpeg_cmd, temp_dir

    config = get_config()

    setup_logging()
    temp_dir = mk_temp(config.temp_dir_name)

    for tool in config.required_tools:
        tool_path = check_tool(tool)
        if tool == "ffmpeg":
            ffmpeg_cmd = tool_path


def extension_lists(category: str) -> list:
    config = get_config()
    if category == "container":
        return config.container_extensions
    elif category == "audio":
        return config.audio_extensions
    elif category == "supported":
        return config.supported_extensions
    else:
        logging.warning(f"Unknown extension category: {category}")
        return []


def ms_to_timestamp(ms: int) -> str:
    sign = "-" if ms < 0 else ""
    ms = abs(ms)
    total_seconds, ms = divmod(ms, 1000)
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{sign}{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def ms_to_seconds(ms: int) -> float:
    return ms / 1000.0


def sec_to_ms(seconds: float) -> int:
    return int(seconds * 1000)


def get_duration(sample_wav: Path) -> int:
    try:
        media_info = MediaInfo.parse(sample_wav)
        if not isinstance(media_info, str):
            for track in media_info.tracks:
                if hasattr(track, 'duration') and track.duration is not None:
                    return int(track.duration)

        raise AudioProcessingError(f"Could not determine duration of {sample_wav}")
    except Exception as e:
        logging.error(f"Error getting duration for {sample_wav}: {e}")
        raise AudioProcessingError(f"Failed to get duration: {e}")


def safe_get(track, data: str, default="", index=0):
    try:
        value = track.get(data, default)
        if isinstance(value, list):
            if len(value) > index:
                return value[index]
            else:
                return default
        return value if value is not None else default
    except (AttributeError, TypeError) as e:
        logging.debug(f"Error extracting {data} from track: {e}")
        return default


def get_audio_tracks(file_info: FileInfo):
    try:
        audio_tracks = []
        audio_idx = []
        media_info = MediaInfo.parse(file_info.path)

        if isinstance(media_info, str):
            raise AudioProcessingError(f"Failed to parse media info: {media_info}")

        data = media_info.to_data()
        for track in data['tracks']:
            track_type = track.get("track_type", {})
            if track_type == "Audio":
                track_id = safe_get(track, "track_id", index=0)
                language = safe_get(track, 'other_language', "", index=0)
                title = safe_get(track, 'title')
                acodec = safe_get(track, 'format')
                channels = safe_get(track, "other_channel_s", "N/A")
                bitrate = safe_get(track, "other_bit_rate")
                samplerate = safe_get(track, "other_sampling_rate", "No samplerate found")
                duration = safe_get(track, "other_duration", index=3)

                audio_track = {
                    "title": title,
                    "language": language,
                    "acodec": acodec,
                    "channels": channels,
                    "bitrate": bitrate,
                    "samplerate": samplerate,
                    "duration": duration
                }

                audio_tracks.append(audio_track)
                audio_idx.append(track_id)

        if not audio_tracks:
            raise AudioProcessingError(f"No audio tracks found in: {file_info.name}")

        def format_audio_track(t):
            fields = [
                ("title", True),
                ("language", True),
                ("acodec", False),
                ("channels", False),
                ("bitrate", True),
                ("samplerate", False),
                ("duration", True),
            ]

            return " ".join(
                f"[{t[field]}]"
                for field, optional in fields
                if not optional or t.get(field)
            )

        if len(audio_tracks) == 1:
            print(f"\n{file_info.type} Audio:\n       {format_audio_track(audio_tracks[0])}")
            return audio_tracks[0], 0, 1

        valid_selection = None
        while not valid_selection:
            print(f"Audio tracks found in ({file_info.type}):")
            for idx, audio in enumerate(audio_tracks, start=1):
                print(f"      Audio Track [#{idx}]: {format_audio_track(audio)}")
            try:
                usr_track_sel = input("Select track number: ").strip()
                usr_track_sel = int(usr_track_sel)
                if 1 <= usr_track_sel <= len(audio_tracks):
                    sel_track = audio_tracks[usr_track_sel - 1]
                    print(f"\n{file_info.type} Audio:\n       {format_audio_track(sel_track)}")
                    valid_selection = sel_track, usr_track_sel - 1, usr_track_sel
            except ValueError:
                print("\nInvalid option or Track does not exist! Please try again.")
            except EOFError:
                if sys.stderr:
                    print("\nProcess aborted!")
                sys.exit(130)

        return valid_selection

    except AudioProcessingError:
        raise
    except Exception as e:
        logging.error(f"Unexpected error getting audio tracks: {e}")
        raise AudioProcessingError(f"Failed to get audio tracks: {e}")


def check_file(file_path: Path, filetype: str) -> FileInfo:
    global temp_dir

    try:
        file_info = FileInfo(file_path, filetype, temp_dir)

        logging.info(f"Analyzing {filetype.upper()} file: {file_info.name}")
        print(f"\nAnalyzing ({filetype.upper()} FILE): ({file_info.name})")

        if not file_info.exists():
            raise FileValidationError(f"File does not exist: {file_path}")

        if not file_info.is_file():
            raise FileValidationError(f"Path is not a file: {file_path}")

        config = get_config()
        if not config.is_supported(file_info.extension):
            supported = ', '.join(config.supported_extensions)
            raise FileValidationError(
                f"Unsupported file extension '{file_info.extension}'. "
                f"Supported: {supported}"
            )

        audio_track, ffmpeg_idx, human_idx = get_audio_tracks(file_info)

        file_info.audio_track = audio_track
        file_info.selected_track = ffmpeg_idx
        file_info.track_index = human_idx

        return file_info

    except (FileValidationError, AudioProcessingError):
        raise
    except Exception as e:
        logging.error(f"Unexpected error checking file {file_path}: {e}")
        raise FileValidationError(f"Failed to validate file: {e}")


def get_samples(file_info: FileInfo) -> Path:
    global ffmpeg_cmd

    try:
        track_id = file_info.selected_track
        out_file = file_info.get_sample_path()

        cmd = [
            ffmpeg_cmd, '-y',
            '-i', str(file_info.path),
            '-map', f'0:a:{track_id}',
            '-ac', str(get_config().audio_channels),
            '-acodec', get_config().audio_codec,
            '-ar', str(get_config().sample_rate),
            str(out_file)
        ]

        logging.info(f"Generating sample: {out_file}")

        if os.path.exists(out_file):
            return out_file

        run_cmd(cmd)

        if not os.path.exists(out_file):
            raise AudioProcessingError(f"Sample file was not created: {out_file}")

        return out_file

    except AudioProcessingError:
        raise
    except Exception as e:
        logging.error(f"Unexpected error generating sample: {e}")
        raise AudioProcessingError(f"Failed to generate sample: {e}")


def load_audio_sf(
    file_path: Path, start_sec: float = 0.0, duration_sec: float | None = None
) -> np.ndarray:
    try:
        if not os.path.exists(file_path):
            raise AudioProcessingError(f"Audio file not found: {file_path}")

        with sf.SoundFile(file_path) as f:
            sr = f.samplerate

            start_sample = int(start_sec * sr)
            f.seek(start_sample)

            if duration_sec is None or duration_sec == 0:
                audio = f.read(dtype="float32")
            else:
                frames = int(duration_sec * sr)
                audio = f.read(frames, dtype="float32")

        if audio.ndim > 1:
            audio = audio.mean(axis=1)

        return audio

    except sf.SoundFileError as e:
        logging.error(f"SoundFile error loading {file_path}: {e}")
        raise AudioProcessingError(f"Failed to load audio file: {e}")
    except Exception as e:
        logging.error(f"Error loading audio {file_path}: {e}")
        raise AudioProcessingError(f"Failed to load audio: {e}")
