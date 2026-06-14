import json
import tempfile
from pathlib import Path

import pytest

from delay_check.config import Config, ConfigError


class TestConfig:
    def test_default_config_loads(self):
        config = Config()
        assert config.app_name == "Delay Check"
        assert config.temp_dir_name == "delay_check"
        assert config.default_analysis_time_sec == 300
        assert config.sample_rate == 44100
        assert config.audio_channels == 1
        assert config.audio_codec == "pcm_s16le"

    def test_drift_tolerance_defaults(self):
        config = Config()
        tolerance = config.drift_tolerance
        assert tolerance["excellent"] == 25
        assert tolerance["good"] == 50
        assert tolerance["acceptable"] == 75
        assert tolerance["poor"] == 100

    def test_supported_extensions(self):
        config = Config()
        exts = config.supported_extensions
        assert ".mkv" in exts
        assert ".wav" in exts
        assert ".mp4" in exts
        assert isinstance(exts, list)

    def test_is_container(self):
        config = Config()
        assert config.is_container(".mkv") is True
        assert config.is_container(".wav") is False

    def test_is_audio(self):
        config = Config()
        assert config.is_audio(".wav") is True
        assert config.is_audio(".mkv") is False

    def test_is_supported(self):
        config = Config()
        assert config.is_supported(".mkv") is True
        assert config.is_supported(".wav") is True
        assert config.is_supported(".xyz") is False

    def test_load_from_custom_file(self, tmp_path):
        custom_config = {
            "version": "2.0.0",
            "application": {
                "name": "Custom App",
                "temp_dir_name": "custom_temp",
                "default_analysis_time_sec": 600,
                "segment_analysis_time_sec": 120,
                "confidence_threshold": 70,
                "drift_tolerance_ms": {
                    "excellent": 10,
                    "good": 30,
                    "acceptable": 50,
                    "poor": 80
                }
            },
            "audio_processing": {
                "sample_rate": 48000,
                "channels": 2,
                "codec": "pcm_s16le",
                "mfcc": {
                    "n_mfcc": 20,
                    "n_fft": 2048,
                    "n_mels": 40,
                    "hop_length": 1024
                }
            },
            "file_extensions": {
                "containers": [".mkv"],
                "audio": [".wav", ".flac"],
                "supported_formats": []
            },
            "confidence_scoring": {
                "base_confidence": 10.0,
                "max_per_segment": 30.0,
                "segments_to_analyze": 3,
                "penalty_factors": {
                    "excellent": 1.0,
                    "good": 0.9,
                    "acceptable": 0.8,
                    "poor": 0.5,
                    "failed": 0.0
                }
            },
            "external_tools": {
                "required": ["ffmpeg"],
                "optional": []
            },
            "logging": {
                "level": "DEBUG",
                "format": "%(message)s",
                "file": "custom.log"
            }
        }

        config_path = tmp_path / "config.json"
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(custom_config, f)

        config = Config(config_path)
        assert config.app_name == "Custom App"
        assert config.default_analysis_time_sec == 600
        assert config.sample_rate == 48000
        assert config.audio_channels == 2
        assert config.log_level == "DEBUG"
        assert config.log_file == "custom.log"

    def test_missing_config_file_uses_defaults(self):
        fake_path = Path("/nonexistent/path/config.json")
        config = Config(fake_path)
        assert config.app_name == "Delay Check"

    def test_invalid_json_uses_defaults(self, tmp_path):
        config_path = tmp_path / "config.json"
        with open(config_path, 'w') as f:
            f.write("not valid json")

        config = Config(config_path)
        assert config.app_name == "Delay Check"

    def test_get_with_default(self):
        config = Config()
        value = config.get("nonexistent", "key", default="fallback")
        assert value == "fallback"

    def test_to_dict(self):
        config = Config()
        d = config.to_dict()
        assert isinstance(d, dict)
        assert "application" in d
        assert "audio_processing" in d

    def test_config_error_on_missing_section(self, tmp_path):
        bad_config = {
            "application": {"name": "Test"},
        }
        config_path = tmp_path / "config.json"
        with open(config_path, 'w') as f:
            json.dump(bad_config, f)

        with pytest.raises(ConfigError):
            Config(config_path)

    def test_singleton_get_config(self):
        from delay_check.config import get_config
        c1 = get_config()
        c2 = get_config()
        assert c1 is c2

    def test_reload_config(self):
        from delay_check.config import reload_config
        c = reload_config()
        assert c.app_name == "Delay Check"
