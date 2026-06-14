import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional


class ConfigError(Exception):
    pass


class Config:
    DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.json"

    DEFAULT_CONFIG = {
        "version": "1.0.0",
        "application": {
            "name": "Delay Check",
            "temp_dir_name": "delay_check",
            "default_analysis_time_sec": 300,
            "segment_analysis_time_sec": 60,
            "confidence_threshold": 80,
            "drift_tolerance_ms": {
                "excellent": 25,
                "good": 50,
                "acceptable": 75,
                "poor": 100
            }
        },
        "audio_processing": {
            "sample_rate": 44100,
            "channels": 1,
            "codec": "pcm_s16le",
            "mfcc": {
                "n_mfcc": 13,
                "n_fft": 1024,
                "n_mels": 20,
                "hop_length": 512
            }
        },
        "file_extensions": {
            "containers": [".mkv", ".mp4", ".mka", ".m4a"],
            "audio": [".wav", ".eac3", ".e-ac3", ".ac3", ".ec3", ".aac", ".dts", ".dtshd", ".flac", ".thd", ".mp3"],
            "supported_formats": []
        },
        "confidence_scoring": {
            "base_confidence": 20.0,
            "max_per_segment": 20.0,
            "segments_to_analyze": 4,
            "penalty_factors": {
                "excellent": 1.0,
                "good": 0.95,
                "acceptable": 0.85,
                "poor": 0.70,
                "failed": 0.0
            }
        },
        "external_tools": {
            "required": ["ffmpeg"],
            "optional": []
        },
        "logging": {
            "level": "INFO",
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            "file": "delay_check.log"
        }
    }

    def __init__(self, config_path: Optional[Path] = None):
        self._config_path = config_path or self.DEFAULT_CONFIG_PATH
        self._config = self._load_config()
        self._validate_config()
        self._compute_derived_values()

    def _load_config(self) -> Dict[str, Any]:
        try:
            if not self._config_path.exists():
                logging.warning(
                    f"Config file not found at {self._config_path}. "
                    "Using default configuration."
                )
                return self.DEFAULT_CONFIG.copy()

            with open(self._config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                logging.info(f"Configuration loaded from {self._config_path}")
                return config

        except json.JSONDecodeError as e:
            logging.error(f"Invalid JSON in config file: {e}")
            logging.warning("Using default configuration.")
            return self.DEFAULT_CONFIG.copy()
        except IOError as e:
            logging.error(f"Error reading config file: {e}")
            logging.warning("Using default configuration.")
            return self.DEFAULT_CONFIG.copy()

    def _validate_config(self) -> None:
        required_sections = [
            "application",
            "audio_processing",
            "file_extensions",
            "confidence_scoring",
            "external_tools"
        ]

        for section in required_sections:
            if section not in self._config:
                logging.error(f"Missing required config section: {section}")
                raise ConfigError(f"Missing required config section: {section}")

    def _compute_derived_values(self) -> None:
        containers = self._config["file_extensions"].get("containers", [])
        audio = self._config["file_extensions"].get("audio", [])
        self._config["file_extensions"]["supported_formats"] = list(
            set(containers + audio)
        )

    def get(self, *keys: str, default: Any = None) -> Any:
        current = self._config
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    @property
    def app_name(self) -> str:
        return self.get("application", "name", default="MediaTools")

    @property
    def temp_dir_name(self) -> str:
        return self.get("application", "temp_dir_name", default="delay_check")

    @property
    def default_analysis_time_sec(self) -> int:
        return self.get("application", "default_analysis_time_sec", default=300)

    @property
    def segment_analysis_time_sec(self) -> int:
        return self.get("application", "segment_analysis_time_sec", default=60)

    @property
    def confidence_threshold(self) -> int:
        return self.get("application", "confidence_threshold", default=80)

    @property
    def drift_tolerance(self) -> Dict[str, int]:
        return self.get("application", "drift_tolerance_ms", default={
            "excellent": 25, "good": 50, "acceptable": 75, "poor": 100
        })

    @property
    def sample_rate(self) -> int:
        return self.get("audio_processing", "sample_rate", default=44100)

    @property
    def audio_channels(self) -> int:
        return self.get("audio_processing", "channels", default=1)

    @property
    def audio_codec(self) -> str:
        return self.get("audio_processing", "codec", default="pcm_s16le")

    @property
    def mfcc_settings(self) -> Dict[str, Any]:
        return self.get("audio_processing", "mfcc", default={
            "n_mfcc": 13, "n_fft": 1024, "n_mels": 20, "hop_length": 512
        })

    @property
    def container_extensions(self) -> list:
        return self.get("file_extensions", "containers", default=[])

    @property
    def audio_extensions(self) -> list:
        return self.get("file_extensions", "audio", default=[])

    @property
    def supported_extensions(self) -> list:
        return self.get("file_extensions", "supported_formats", default=[])

    def is_container(self, extension: str) -> bool:
        return extension.lower() in [ext.lower() for ext in self.container_extensions]

    def is_audio(self, extension: str) -> bool:
        return extension.lower() in [ext.lower() for ext in self.audio_extensions]

    def is_supported(self, extension: str) -> bool:
        return extension.lower() in [ext.lower() for ext in self.supported_extensions]

    @property
    def base_confidence(self) -> float:
        return self.get("confidence_scoring", "base_confidence", default=20.0)

    @property
    def max_confidence_per_segment(self) -> float:
        return self.get("confidence_scoring", "max_per_segment", default=20.0)

    @property
    def penalty_factors(self) -> Dict[str, float]:
        return self.get("confidence_scoring", "penalty_factors", default={
            "excellent": 1.0, "good": 0.95, "acceptable": 0.85,
            "poor": 0.70, "failed": 0.0
        })

    @property
    def required_tools(self) -> list:
        return self.get("external_tools", "required", default=["ffmpeg"])

    @property
    def log_level(self) -> str:
        return self.get("logging", "level", default="INFO")

    @property
    def log_format(self) -> str:
        return self.get("logging", "format", default="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    @property
    def log_file(self) -> str:
        return self.get("logging", "file", default="delay_check.log")

    def to_dict(self) -> Dict[str, Any]:
        return self._config.copy()


_config: Optional[Config] = None


def get_config(config_path: Optional[Path] = None) -> Config:
    global _config
    if _config is None:
        _config = Config(config_path)
    return _config


def reload_config(config_path: Optional[Path] = None) -> Config:
    global _config
    _config = Config(config_path)
    return _config
