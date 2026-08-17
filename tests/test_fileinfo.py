import copy
import json

import pytest

from delay_check.config import Config, reload_config
from delay_check.fileinfo import FileInfo


def _write_config(tmp_path, sample_rate, channels=1, codec="pcm_s16le"):
    cfg = copy.deepcopy(Config.DEFAULT_CONFIG)
    cfg["audio_processing"]["sample_rate"] = sample_rate
    cfg["audio_processing"]["channels"] = channels
    cfg["audio_processing"]["codec"] = codec
    path = tmp_path / f"config_{sample_rate}_{channels}_{codec}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return path


@pytest.fixture(autouse=True)
def _reset_config_singleton():
    yield
    # restore the shipped config.json as the process-wide singleton so
    # other tests aren't affected by the custom configs loaded here.
    reload_config()


class TestGetSamplePathCacheKey:
    def test_different_sample_rate_produces_different_path(self, tmp_path):
        path_a = _write_config(tmp_path, sample_rate=44100)
        path_b = _write_config(tmp_path, sample_rate=48000)
        fi = FileInfo("movie.mkv", "reference", tmp_path)

        reload_config(path_a)
        sample_a = fi.get_sample_path(display_id=0)

        reload_config(path_b)
        sample_b = fi.get_sample_path(display_id=0)

        assert sample_a != sample_b

    def test_different_codec_produces_different_path(self, tmp_path):
        path_a = _write_config(tmp_path, sample_rate=44100, codec="pcm_s16le")
        path_b = _write_config(tmp_path, sample_rate=44100, codec="pcm_s24le")
        fi = FileInfo("movie.mkv", "reference", tmp_path)

        reload_config(path_a)
        sample_a = fi.get_sample_path(display_id=0)

        reload_config(path_b)
        sample_b = fi.get_sample_path(display_id=0)

        assert sample_a != sample_b

    def test_same_config_produces_same_path(self, tmp_path):
        path_a = _write_config(tmp_path, sample_rate=44100)
        fi = FileInfo("movie.mkv", "reference", tmp_path)

        reload_config(path_a)
        first = fi.get_sample_path(display_id=0)
        second = fi.get_sample_path(display_id=0)

        assert first == second

    def test_audio_file_path_also_reflects_config(self, tmp_path):
        path_a = _write_config(tmp_path, sample_rate=44100)
        path_b = _write_config(tmp_path, sample_rate=48000)
        fi = FileInfo("track.wav", "dubbed", tmp_path)

        reload_config(path_a)
        sample_a = fi.get_sample_path()

        reload_config(path_b)
        sample_b = fi.get_sample_path()

        assert sample_a != sample_b
