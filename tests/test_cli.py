import pytest

from delay_check import cli
from delay_check.cli import initargs


class _FakeFileInfo:
    def __init__(self, path="fake.mkv"):
        self.path = path


_DRIFT = {
    "slope_ms_per_s": -42.7,
    "r_squared": 0.99,
    "percent": 4.27,
    "ppm": 42700,
    "direction": "SLOWER",
    "atempo": 1.04465,
}


_PROGRESSIVE = {
    "total_jump_ms": 2000.0,
    "steps": [{
        "before_time_sec": 60.0, "after_time_sec": 120.0,
        "before_delay_ms": 1000.0, "after_delay_ms": 3000.0,
        "jump_ms": 2000.0,
    }],
}


def _patch_pipeline(
    monkeypatch, *, median_delay_ms, correlated_delays=None, drift=None,
    progressive=None, confidence=(100.0, []),
):
    """Patches every delay_check() collaborator except the branching logic
    under test, so each of its possible outcomes can be exercised in
    isolation without touching real files, ffmpeg, or audio correlation.
    """
    monkeypatch.setattr(cli, "initargs", lambda: ("ref.mkv", "dub.mkv"))
    monkeypatch.setattr(
        cli, "get_file_data",
        lambda ref, dub: (_FakeFileInfo("ref.mkv"), _FakeFileInfo("dub.mkv")),
    )

    async def fake_audio_samples(ref_info, dub_info):
        return "ref_sample.wav", "dub_sample.wav"

    async def fake_segment_delays(ref_sample, dub_sample, max_sec=None):
        return [{"Delay": 1000, "Score": 50.0, "StartSec": 0.0, "EndSec": 60.0}]

    monkeypatch.setattr(cli, "audio_samples", fake_audio_samples)
    monkeypatch.setattr(cli, "segment_delays", fake_segment_delays)
    monkeypatch.setattr(
        cli, "aggregate_delay",
        lambda segment_results: (median_delay_ms, correlated_delays or []),
    )
    monkeypatch.setattr(cli, "analyze_timebase_drift", lambda segment_results: drift)
    monkeypatch.setattr(
        cli, "detect_progressive_delay", lambda segment_results: progressive
    )
    monkeypatch.setattr(
        cli, "calculate_confidence", lambda delays, anchor=None: confidence
    )


class TestDelayCheckOutcomes:
    """One test per outcome of delay_check()'s branching (see the case
    table built while designing the timebase-drift feature): only case #3
    returns a usable delay, the other four all return None but for
    different, mutually exclusive reasons distinguished here by their
    printed output.
    """

    @pytest.mark.asyncio
    async def test_case1_drift_with_no_median_returns_none(self, monkeypatch, capsys):
        _patch_pipeline(monkeypatch, median_delay_ms=None, drift=_DRIFT)

        result = await cli.delay_check()

        assert result is None
        assert "No constant delay could be estimated" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_progressive_with_no_median_returns_none(self, monkeypatch, capsys):
        _patch_pipeline(monkeypatch, median_delay_ms=None, progressive=_PROGRESSIVE)

        result = await cli.delay_check()

        assert result is None
        out = capsys.readouterr().out
        assert "PROGRESSIVE DELAY DETECTED" in out.upper()
        assert "delay jumps at specific points" in out

    @pytest.mark.asyncio
    async def test_case2_no_median_no_drift_returns_none(self, monkeypatch, capsys):
        _patch_pipeline(monkeypatch, median_delay_ms=None, drift=None)

        result = await cli.delay_check()

        assert result is None
        assert "No reliable constant delay found" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_case3_high_confidence_returns_the_delay(self, monkeypatch, capsys):
        _patch_pipeline(
            monkeypatch, median_delay_ms=5000, correlated_delays=[5000],
            drift=None, confidence=(100.0, []),
        )

        result = await cli.delay_check()

        assert result == 5000
        assert "(constant)" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_case4_low_confidence_with_drift_returns_none(self, monkeypatch, capsys):
        _patch_pipeline(
            monkeypatch, median_delay_ms=5000, correlated_delays=[5000],
            drift=_DRIFT, confidence=(40.0, []),
        )

        result = await cli.delay_check()

        assert result is None
        out = capsys.readouterr().out
        assert "not constant" in out
        assert "TIMEBASE DRIFT DETECTED" in out.upper()

    @pytest.mark.asyncio
    async def test_low_confidence_with_progressive_returns_none(self, monkeypatch, capsys):
        _patch_pipeline(
            monkeypatch, median_delay_ms=5000, correlated_delays=[5000],
            drift=None, progressive=_PROGRESSIVE, confidence=(40.0, []),
        )

        result = await cli.delay_check()

        assert result is None
        out = capsys.readouterr().out
        assert "not constant" in out
        assert "PROGRESSIVE DELAY DETECTED" in out.upper()
        # drift already checked first -- progressive must not also print the
        # generic "visually review" recommendation meant for neither case.
        assert "Recommendation: Visually review" not in out

    @pytest.mark.asyncio
    async def test_case5_low_confidence_no_drift_returns_none(self, monkeypatch, capsys):
        _patch_pipeline(
            monkeypatch, median_delay_ms=5000, correlated_delays=[5000],
            drift=None, confidence=(40.0, []),
        )

        result = await cli.delay_check()

        assert result is None
        out = capsys.readouterr().out
        assert "not constant" in out
        assert "Recommendation: Visually review" in out

    @pytest.mark.asyncio
    async def test_case5_mentions_edits_not_global_fps(self, monkeypatch, capsys):
        # Regression for the outdated "Different FPS" wording: this branch
        # only runs after analyze_timebase_drift already ruled out global
        # drift, so the message should point at localized causes instead.
        _patch_pipeline(
            monkeypatch, median_delay_ms=5000, correlated_delays=[5000],
            drift=None, confidence=(40.0, []),
        )

        await cli.delay_check()

        out = capsys.readouterr().out
        assert "already ruled out" in out
        assert "edits/cuts" in out

    @pytest.mark.asyncio
    async def test_out_of_sync_segments_are_listed_before_the_drift_or_recommendation(
        self, monkeypatch, capsys
    ):
        _patch_pipeline(
            monkeypatch, median_delay_ms=5000, correlated_delays=[5000],
            drift=None, confidence=(40.0, [
                {"segment_index": 3, "delay_found": 9000, "drift_amount": 4000}
            ]),
        )

        await cli.delay_check()

        out = capsys.readouterr().out
        assert "#3" in out
        assert "9000ms" in out


class TestInitargs:
    def test_missing_both_files_exits(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["delay-check"])
        with pytest.raises(SystemExit):
            initargs()
        assert "reference file not specified" in capsys.readouterr().err

    def test_missing_dubbed_file_exits(self, monkeypatch, tmp_path, capsys):
        ref = tmp_path / "ref.wav"
        ref.touch()
        monkeypatch.setattr("sys.argv", ["delay-check", str(ref)])
        with pytest.raises(SystemExit):
            initargs()
        assert "dubbed file not specified" in capsys.readouterr().err

    def test_nonexistent_file_exits(self, monkeypatch, tmp_path, capsys):
        ref = tmp_path / "ref.wav"
        ref.touch()
        missing = tmp_path / "does_not_exist.wav"
        monkeypatch.setattr("sys.argv", ["delay-check", str(ref), str(missing)])
        with pytest.raises(SystemExit):
            initargs()
        assert "does not exist" in capsys.readouterr().err

    def test_unsupported_extension_exits(self, monkeypatch, tmp_path, capsys):
        ref = tmp_path / "ref.wav"
        ref.touch()
        dub = tmp_path / "dub.txt"
        dub.touch()
        monkeypatch.setattr("sys.argv", ["delay-check", str(ref), str(dub)])
        with pytest.raises(SystemExit):
            initargs()
        assert "is not supported" in capsys.readouterr().err

    def test_valid_files_returns_both_paths(self, monkeypatch, tmp_path):
        ref = tmp_path / "ref.wav"
        dub = tmp_path / "dub.wav"
        ref.touch()
        dub.touch()
        monkeypatch.setattr("sys.argv", ["delay-check", str(ref), str(dub)])
        assert initargs() == (str(ref), str(dub))
