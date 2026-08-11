"""Tests for cycdp CLI."""

import json
import os
import subprocess
import sys

import pytest

import cycdp
from cycdp.cli import (
    CATEGORIES,
    COMMANDS,
    SCRAMBLE_MAP,
    WAVEFORM_MAP,
    build_parser,
    format_analysis,
    main,
    prepare_kwargs,
    resolve_output_path,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def wav_file(tmp_path):
    """Generate a short mono sine wave as a test fixture."""
    buf = cycdp.synth_wave(
        waveform=cycdp.WAVE_SINE,
        frequency=440.0,
        amplitude=0.8,
        duration=0.5,
        sample_rate=44100,
    )
    path = str(tmp_path / "test_input.wav")
    cycdp.write_file(path, buf)
    return path


@pytest.fixture
def wav_file2(tmp_path):
    """Generate a second short mono sine wave."""
    buf = cycdp.synth_wave(
        waveform=cycdp.WAVE_SINE,
        frequency=880.0,
        amplitude=0.6,
        duration=0.5,
        sample_rate=44100,
    )
    path = str(tmp_path / "test_input2.wav")
    cycdp.write_file(path, buf)
    return path


# =============================================================================
# Registry integrity tests
# =============================================================================


class TestRegistry:
    def test_all_commands_have_required_keys(self):
        for name, spec in COMMANDS.items():
            assert "func" in spec, f"{name}: missing 'func'"
            assert "category" in spec, f"{name}: missing 'category'"
            assert "input" in spec, f"{name}: missing 'input'"
            assert "help" in spec, f"{name}: missing 'help'"
            assert "params" in spec, f"{name}: missing 'params'"

    def test_all_categories_are_valid(self):
        for name, spec in COMMANDS.items():
            assert spec["category"] in CATEGORIES, (
                f"{name}: unknown category '{spec['category']}'"
            )

    def test_all_input_types_are_valid(self):
        valid = {"single", "dual", "synth", "analysis"}
        for name, spec in COMMANDS.items():
            assert spec["input"] in valid, (
                f"{name}: invalid input type '{spec['input']}'"
            )

    def test_all_funcs_exist_on_cycdp(self):
        for name, spec in COMMANDS.items():
            assert hasattr(cycdp, spec["func"]), (
                f"{name}: cycdp.{spec['func']} does not exist"
            )

    def test_param_types_are_valid(self):
        valid_types = {
            int,
            float,
            str,
            "json",
            "nargs+",
            "waveform",
            "scramble",
            "bool",
        }
        for cmd_name, spec in COMMANDS.items():
            for param_name, (ptype, default, help_text) in spec["params"].items():
                assert ptype in valid_types, (
                    f"{cmd_name}.{param_name}: invalid type '{ptype}'"
                )

    def test_command_names_use_hyphens(self):
        for name in COMMANDS:
            assert "_" not in name, (
                f"Command '{name}' should use hyphens, not underscores"
            )

    def test_waveform_map_covers_all(self):
        assert set(WAVEFORM_MAP.keys()) == {"sine", "square", "saw", "ramp", "triangle"}

    def test_scramble_map_covers_all(self):
        assert set(SCRAMBLE_MAP.keys()) == {
            "shuffle",
            "reverse",
            "size-up",
            "size-down",
            "level-up",
            "level-down",
        }


# =============================================================================
# Parser tests
# =============================================================================


class TestParser:
    def test_build_parser_succeeds(self):
        parser = build_parser()
        assert parser is not None

    def test_parse_single_input_command(self):
        parser = build_parser()
        args = parser.parse_args(["time-stretch", "input.wav", "--factor", "2.0"])
        assert args.command == "time-stretch"
        assert args.input == "input.wav"
        assert args.factor == 2.0

    def test_parse_dual_input_command(self):
        parser = build_parser()
        args = parser.parse_args(["morph", "a.wav", "b.wav", "--morph-end", "0.7"])
        assert args.command == "morph"
        assert args.input1 == "a.wav"
        assert args.input2 == "b.wav"
        assert args.morph_end == 0.7

    def test_parse_synth_command(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "synth-wave",
                "--waveform",
                "saw",
                "--frequency",
                "220",
            ]
        )
        assert args.command == "synth-wave"
        assert args.waveform == "saw"
        assert args.frequency == 220.0

    def test_parse_analysis_command(self):
        parser = build_parser()
        args = parser.parse_args(["pitch", "input.wav", "--min-freq", "100"])
        assert args.command == "pitch"
        assert args.input == "input.wav"
        assert args.min_freq == 100.0

    def test_defaults_applied(self):
        parser = build_parser()
        args = parser.parse_args(["reverb", "input.wav"])
        assert args.mix == 0.5
        assert args.decay_time == 2.0
        assert args.damping == 0.5
        assert args.lpfreq == 8000.0
        assert args.predelay == 0.0
        assert args.normalize == 0.95
        assert args.no_normalize is False
        assert args.format == "float"

    def test_global_options(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "reverb",
                "input.wav",
                "-o",
                "out.wav",
                "-n",
                "0.8",
                "--no-normalize",
                "--format",
                "pcm16",
            ]
        )
        assert args.output == "out.wav"
        assert args.normalize == 0.8
        assert args.no_normalize is True
        assert args.format == "pcm16"

    def test_analysis_format_options(self):
        parser = build_parser()
        args = parser.parse_args(["pitch", "input.wav", "--format", "json"])
        assert args.format == "json"

    def test_nargs_plus_param(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "synth-chord",
                "--midi-notes",
                "60",
                "64",
                "67",
            ]
        )
        assert args.midi_notes == [60, 64, 67]

    def test_list_command(self):
        parser = build_parser()
        args = parser.parse_args(["list"])
        assert args.command == "list"
        assert args.category is None

    def test_list_with_category(self):
        parser = build_parser()
        args = parser.parse_args(["list", "spectral"])
        assert args.command == "list"
        assert args.category == "spectral"

    def test_version_command(self):
        parser = build_parser()
        args = parser.parse_args(["version"])
        assert args.command == "version"

    def test_info_command(self):
        parser = build_parser()
        args = parser.parse_args(["info", "test.wav"])
        assert args.command == "info"
        assert args.input == "test.wav"

    def test_missing_required_param_exits(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["time-stretch", "input.wav"])  # missing --factor

    def test_bool_param_default_true(self):
        parser = build_parser()
        # fofex-extract has window=True by default
        args = parser.parse_args(["fofex-extract", "input.wav", "--time", "0.5"])
        assert args.window is True

    def test_bool_param_no_flag(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "fofex-extract",
                "input.wav",
                "--time",
                "0.5",
                "--no-window",
            ]
        )
        assert args.window is False


# =============================================================================
# Output path resolution tests
# =============================================================================


class TestOutputPath:
    def test_explicit_file_path(self):
        class Args:
            output = "/tmp/out.wav"

        result = resolve_output_path(Args(), "reverb", "/data/voice.wav")
        assert result == "/tmp/out.wav"

    def test_directory_output(self, tmp_path):
        d = str(tmp_path)

        class Args:
            output = d

        result = resolve_output_path(Args(), "reverb", "/data/voice.wav")
        assert result == os.path.join(d, "voice_reverb.wav")

    def test_auto_name_from_input(self):
        class Args:
            output = None

        result = resolve_output_path(Args(), "time-stretch", "/data/voice.wav")
        assert result == os.path.join("/data", "voice_time-stretch.wav")

    def test_auto_name_synth(self):
        class Args:
            output = None

        result = resolve_output_path(Args(), "synth-wave")
        assert result == "output_synth-wave.wav"

    def test_auto_name_input_in_current_dir(self):
        class Args:
            output = None

        result = resolve_output_path(Args(), "reverb", "voice.wav")
        assert result == os.path.join(".", "voice_reverb.wav")


# =============================================================================
# Prepare kwargs tests
# =============================================================================


class TestPrepareKwargs:
    def test_basic_params(self):
        spec = COMMANDS["time-stretch"]
        parser = build_parser()
        args = parser.parse_args(["time-stretch", "in.wav", "--factor", "2.0"])
        kwargs = prepare_kwargs(spec, args)
        assert kwargs["factor"] == 2.0
        assert kwargs["fft_size"] == 1024
        assert kwargs["overlap"] == 3

    def test_json_param(self):
        spec = COMMANDS["zigzag"]
        parser = build_parser()
        args = parser.parse_args(
            [
                "zigzag",
                "in.wav",
                "--times",
                "[0.1, 0.5, 0.8]",
            ]
        )
        kwargs = prepare_kwargs(spec, args)
        assert kwargs["times"] == [0.1, 0.5, 0.8]

    def test_waveform_param(self):
        spec = COMMANDS["synth-wave"]
        parser = build_parser()
        args = parser.parse_args(["synth-wave", "--waveform", "square"])
        kwargs = prepare_kwargs(spec, args)
        assert kwargs["waveform"] == cycdp.WAVE_SQUARE

    def test_scramble_param(self):
        spec = COMMANDS["scramble"]
        parser = build_parser()
        args = parser.parse_args(["scramble", "in.wav", "--mode", "reverse"])
        kwargs = prepare_kwargs(spec, args)
        assert kwargs["mode"] == cycdp.SCRAMBLE_REVERSE

    def test_nargs_plus_param(self):
        spec = COMMANDS["synth-chord"]
        parser = build_parser()
        args = parser.parse_args(
            [
                "synth-chord",
                "--midi-notes",
                "60",
                "64",
                "67",
            ]
        )
        kwargs = prepare_kwargs(spec, args)
        assert kwargs["midi_notes"] == [60, 64, 67]


# =============================================================================
# Handler integration tests (actually run cycdp functions)
# =============================================================================


class TestHandlerSingle:
    def test_time_stretch(self, wav_file, tmp_path):
        out = str(tmp_path / "stretched.wav")
        main(["time-stretch", wav_file, "--factor", "2.0", "-o", out])
        assert os.path.exists(out)
        result = cycdp.read_file(out)
        # Stretched by 2x should be roughly double length
        original = cycdp.read_file(wav_file)
        assert result.frame_count > original.frame_count

    def test_reverb(self, wav_file, tmp_path):
        out = str(tmp_path / "reverbed.wav")
        main(["reverb", wav_file, "-o", out])
        assert os.path.exists(out)

    def test_reverse(self, wav_file, tmp_path):
        out = str(tmp_path / "reversed.wav")
        main(["reverse", wav_file, "-o", out])
        assert os.path.exists(out)
        result = cycdp.read_file(out)
        original = cycdp.read_file(wav_file)
        assert result.frame_count == original.frame_count

    def test_no_normalize_flag(self, wav_file, tmp_path):
        out = str(tmp_path / "raw.wav")
        main(["reverb", wav_file, "-o", out, "--no-normalize"])
        assert os.path.exists(out)

    def test_output_directory(self, wav_file, tmp_path):
        outdir = str(tmp_path / "outdir")
        os.makedirs(outdir)
        main(["reverb", wav_file, "-o", outdir])
        expected = os.path.join(outdir, "test_input_reverb.wav")
        assert os.path.exists(expected)

    def test_auto_name_output(self, wav_file):
        main(["reverb", wav_file])
        expected = wav_file.replace(".wav", "_reverb.wav")
        assert os.path.exists(expected)
        os.unlink(expected)  # cleanup

    def test_missing_input_file(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            main(["reverb", str(tmp_path / "nonexistent.wav")])


class TestHandlerDual:
    def test_morph(self, wav_file, wav_file2, tmp_path):
        out = str(tmp_path / "morphed.wav")
        main(["morph", wav_file, wav_file2, "--morph-end", "0.5", "-o", out])
        assert os.path.exists(out)

    def test_mix2(self, wav_file, wav_file2, tmp_path):
        out = str(tmp_path / "mixed.wav")
        main(["mix2", wav_file, wav_file2, "-o", out])
        assert os.path.exists(out)

    def test_missing_second_input(self, wav_file, tmp_path):
        with pytest.raises(SystemExit):
            main(["morph", wav_file, str(tmp_path / "nope.wav"), "-o", "out.wav"])


class TestHandlerSynth:
    def test_synth_wave(self, tmp_path):
        out = str(tmp_path / "tone.wav")
        main(
            [
                "synth-wave",
                "--waveform",
                "sine",
                "--frequency",
                "440",
                "--duration",
                "0.5",
                "-o",
                out,
            ]
        )
        assert os.path.exists(out)
        result = cycdp.read_file(out)
        assert result.frame_count > 0

    def test_synth_noise(self, tmp_path):
        out = str(tmp_path / "noise.wav")
        main(["synth-noise", "--duration", "0.5", "-o", out])
        assert os.path.exists(out)

    def test_synth_click(self, tmp_path):
        out = str(tmp_path / "click.wav")
        main(["synth-click", "--tempo", "120", "--duration", "1.0", "-o", out])
        assert os.path.exists(out)

    def test_synth_chord(self, tmp_path):
        out = str(tmp_path / "chord.wav")
        main(
            [
                "synth-chord",
                "--midi-notes",
                "60",
                "64",
                "67",
                "--duration",
                "0.5",
                "-o",
                out,
            ]
        )
        assert os.path.exists(out)


class TestHandlerAnalysis:
    def test_pitch_text(self, wav_file, capsys):
        main(["pitch", wav_file])
        captured = capsys.readouterr()
        assert "Frame" in captured.out
        assert "Time (s)" in captured.out
        assert "Freq (Hz)" in captured.out

    def test_pitch_json(self, wav_file, capsys):
        main(["pitch", wav_file, "--format", "json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, dict)
        assert "pitch" in data
        assert "confidence" in data

    def test_pitch_csv(self, wav_file, capsys):
        main(["pitch", wav_file, "--format", "csv"])
        captured = capsys.readouterr()
        assert "frame,time,frequency,confidence" in captured.out

    def test_pitch_to_file(self, wav_file, tmp_path):
        out = str(tmp_path / "pitch.txt")
        main(["pitch", wav_file, "-o", out])
        assert os.path.exists(out)
        with open(out) as f:
            content = f.read()
        assert "Time (s)" in content

    def test_formants(self, wav_file, capsys):
        main(["formants", wav_file])
        captured = capsys.readouterr()
        assert "Frame" in captured.out

    def test_get_partials(self, wav_file, capsys):
        main(["get-partials", wav_file])
        captured = capsys.readouterr()
        assert "Partial tracks" in captured.out

    def test_peak(self, wav_file, capsys):
        main(["peak", wav_file])
        captured = capsys.readouterr()
        assert "Peak level" in captured.out


# =============================================================================
# Utility command tests
# =============================================================================


class TestVersion:
    def test_version_output(self, capsys):
        main(["version"])
        captured = capsys.readouterr()
        assert "cycdp" in captured.out
        assert cycdp.__version__ in captured.out


class TestList:
    def test_list_all(self, capsys):
        main(["list"])
        captured = capsys.readouterr()
        # Should contain category names
        for cat in CATEGORIES:
            assert cat in captured.out

    def test_list_category(self, capsys):
        main(["list", "spectral"])
        captured = capsys.readouterr()
        assert "spectral" in captured.out
        assert "time-stretch" in captured.out

    def test_list_category_filter(self, capsys):
        main(["list", "synth"])
        captured = capsys.readouterr()
        assert "synth-wave" in captured.out
        # Should not contain unrelated commands
        assert "reverb" not in captured.out


class TestInfo:
    def test_info_output(self, wav_file, capsys):
        main(["info", wav_file])
        captured = capsys.readouterr()
        assert "Duration:" in captured.out
        assert "Channels:" in captured.out
        assert "Sample rate:" in captured.out
        assert "Peak level:" in captured.out

    def test_info_missing_file(self):
        with pytest.raises(SystemExit):
            main(["info", "/nonexistent/file.wav"])


# =============================================================================
# No-command shows help
# =============================================================================


class TestNoCommand:
    def test_no_args_shows_help(self, capsys):
        main([])
        captured = capsys.readouterr()
        assert "cycdp" in captured.out


# =============================================================================
# Analysis formatting tests
# =============================================================================


class TestFormatAnalysis:
    def test_pitch_text(self):
        data = {
            "pitch": [440.0, 441.0],
            "confidence": [0.95, 0.92],
            "num_frames": 2,
            "frame_time": 0.01,
            "sample_rate": 44100,
        }
        text = format_analysis("pitch", data, "text")
        assert "Time (s)" in text
        assert "440.00" in text

    def test_pitch_json(self):
        data = {
            "pitch": [440.0],
            "confidence": [0.95],
            "num_frames": 1,
            "frame_time": 0.01,
            "sample_rate": 44100,
        }
        text = format_analysis("pitch", data, "json")
        parsed = json.loads(text)
        assert parsed["pitch"] == [440.0]
        assert parsed["confidence"] == [0.95]

    def test_pitch_csv(self):
        data = {
            "pitch": [440.0, 441.0],
            "confidence": [0.95, 0.92],
            "num_frames": 2,
            "frame_time": 0.01,
            "sample_rate": 44100,
        }
        text = format_analysis("pitch", data, "csv")
        lines = text.strip().split("\n")
        assert lines[0] == "frame,time,frequency,confidence"
        assert lines[1] == "0,0.0,440.0,0.95"

    def test_peak_text(self):
        data = (0.8, 500)
        text = format_analysis("peak", data, "text")
        assert "Peak level: 0.800000" in text

    def test_peak_json(self):
        data = (0.8, 500)
        text = format_analysis("peak", data, "json")
        parsed = json.loads(text)
        assert parsed == [0.8, 500]

    def test_partials_csv(self):
        data = {
            "tracks": [
                {"freq": [440.0], "amp": [0.5], "start_frame": 0, "end_frame": 10},
                {"freq": [880.0], "amp": [0.25], "start_frame": 0, "end_frame": 10},
            ],
            "num_tracks": 2,
            "total_frames": 10,
        }
        text = format_analysis("get-partials", data, "csv")
        assert "track,start_frame,end_frame,avg_freq,avg_amp" in text

    def test_formants_csv(self):
        data = {
            "f1": [500.0, 600.0],
            "b1": [100.0, 110.0],
            "f2": [1500.0, 1600.0],
            "b2": [200.0, 210.0],
            "f3": [2500.0, 2600.0],
            "b3": [300.0, 310.0],
            "f4": [3500.0, 3600.0],
            "b4": [400.0, 410.0],
            "num_frames": 2,
            "frame_time": 0.01,
            "sample_rate": 44100,
        }
        text = format_analysis("formants", data, "csv")
        assert "frame" in text
        assert "f1" in text


# =============================================================================
# Entry point test (subprocess)
# =============================================================================


class TestEntryPoint:
    def test_python_m_cycdp_version(self):
        result = subprocess.run(
            [sys.executable, "-m", "cycdp", "version"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "cycdp" in result.stdout

    def test_python_m_cycdp_list(self):
        result = subprocess.run(
            [sys.executable, "-m", "cycdp", "list", "spectral"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "time-stretch" in result.stdout


# =============================================================================
# Output normalization policy
# =============================================================================


class TestNormalizationPolicy:
    """Amplitude commands must not have their output level silently rewritten.

    Regression guard: the default -n 0.95 normalization used to be applied
    unconditionally, which made gain/limiter/normalize no-ops -- a 20x
    difference in requested gain produced the same output peak.
    """

    def test_gain_is_not_renormalized(self, wav_file, tmp_path):
        """Requested gain must survive to the output file."""
        quiet = str(tmp_path / "quiet.wav")
        loud = str(tmp_path / "loud.wav")
        main(["gain", wav_file, "--gain-factor", "0.1", "-o", quiet])
        main(["gain", wav_file, "--gain-factor", "0.5", "-o", loud])

        source_peak = cycdp.peak(cycdp.read_file(wav_file))[0]
        quiet_peak = cycdp.peak(cycdp.read_file(quiet))[0]
        loud_peak = cycdp.peak(cycdp.read_file(loud))[0]

        assert quiet_peak == pytest.approx(source_peak * 0.1, rel=1e-3)
        assert loud_peak == pytest.approx(source_peak * 0.5, rel=1e-3)
        # The whole point: different gains produce different levels.
        assert loud_peak > quiet_peak * 4

    def test_limiter_threshold_is_honoured(self, wav_file, tmp_path):
        """A -20 dB ceiling must actually cap the output near 0.1."""
        out = str(tmp_path / "limited.wav")
        main(["limiter", wav_file, "--threshold-db", "-20", "-o", out])
        peak = cycdp.peak(cycdp.read_file(out))[0]
        assert peak == pytest.approx(0.1, abs=0.02)

    def test_explicit_normalize_still_applies(self, wav_file, tmp_path):
        """Opting in with -n must override the per-command exemption."""
        out = str(tmp_path / "forced.wav")
        main(["gain", wav_file, "--gain-factor", "0.1", "-n", "0.5", "-o", out])
        assert cycdp.peak(cycdp.read_file(out))[0] == pytest.approx(0.5, rel=1e-3)

    def test_non_amplitude_command_still_auto_normalizes(self, wav_file, tmp_path):
        """Spectral/effect commands keep the 0.95 default."""
        out = str(tmp_path / "verb.wav")
        main(["reverb", wav_file, "--decay-time", "1.0", "-o", out])
        assert cycdp.peak(cycdp.read_file(out))[0] == pytest.approx(0.95, rel=1e-3)

    def test_no_normalize_flag_still_works(self, wav_file, tmp_path):
        """--no-normalize must suppress normalization for normalized commands."""
        out = str(tmp_path / "raw.wav")
        main(["reverb", wav_file, "--decay-time", "1.0", "--no-normalize", "-o", out])
        assert cycdp.peak(cycdp.read_file(out))[0] != pytest.approx(0.95, rel=1e-3)

    def test_exempt_commands_are_all_real_commands(self):
        """NO_AUTO_NORMALIZE must not drift out of sync with the registry."""
        from cycdp.cli import NO_AUTO_NORMALIZE

        for name in NO_AUTO_NORMALIZE:
            assert name in COMMANDS, f"{name} is not a registered command"
            assert COMMANDS[name]["input"] in ("single", "dual", "synth")
