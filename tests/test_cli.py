from __future__ import annotations

import argparse
import re

import pytest

from dop import cli


def _subcommands(parser: argparse.ArgumentParser) -> list[str]:
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return list(action.choices)


def test_exactly_six_subcommands():
    assert _subcommands(cli.build_parser()) == ["up", "down", "status", "logs", "test", "reset"]


def test_help_lists_exactly_the_six(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    listed = re.findall(r"^    (\w+)\s", out, flags=re.MULTILINE)
    assert listed == ["up", "down", "status", "logs", "test", "reset"]
    flat = " ".join(out.split())
    assert "data is kept" in flat and "all local data is lost" in flat


@pytest.mark.parametrize("argv", [["start"], ["e2e"], [], ["test"], ["test", "app", "--layer", "e2e"]])
def test_usage_errors_exit_2(argv, capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(argv)
    assert excinfo.value.code == 2


def test_global_flags_before_or_after_the_verb(example_config):
    parser = cli.build_parser()
    a = parser.parse_args(["--dry-run", "-v", "--config", str(example_config), "test", "app"])
    b = parser.parse_args(["test", "app", "--dry-run", "-v", "--config", str(example_config)])
    for args in (a, b):
        assert args.dry_run is True and args.verbose is True and args.config == str(example_config)
        assert args.layer == "unit"
    c = parser.parse_args(["status"])
    assert c.dry_run is False and c.verbose is False and c.config is None


def test_config_error_exits_2_with_one_line(write_config, capsys):
    path = write_config('[project]\nname = "x"\nbogus = 1\n')
    assert cli.main(["--config", str(path), "status"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("dop: ") and "bogus" in err
    assert "Traceback" not in err and len(err.strip().splitlines()) == 1


def test_no_config_found_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOP_CONFIG", str(tmp_path / "missing.toml"))
    assert cli.main(["status"]) == 2


def test_missing_tool_exits_2_naming_it(write_config, tmp_path, capsys):
    (tmp_path / "r").mkdir()
    path = write_config('''
[project]
name = "x"
default_environment = "local"
[environments.local]
kubeconfig = "echo /k"
namespace = "ns"
[components.c]
repo = "r"
kind = "none"
[components.c.test]
unit = ["no-such-tool-xyz run"]
''')
    assert cli.main(["--config", str(path), "test", "c"]) == 2
    err = capsys.readouterr().err
    assert "no-such-tool-xyz" in err and "Traceback" not in err


def test_unknown_layer_exits_2(example_config, capsys):
    assert cli.main(["--config", str(example_config), "test", "cli", "--layer", "contract"]) == 2


def test_unknown_component_exits_2(example_config, capsys):
    assert cli.main(["--config", str(example_config), "test", "ghost"]) == 2


def test_failing_test_command_exits_1(write_config, tmp_path):
    (tmp_path / "r").mkdir()
    path = write_config('''
[project]
name = "x"
default_environment = "local"
[environments.local]
kubeconfig = "echo /k"
namespace = "ns"
[components.c]
repo = "r"
kind = "none"
[components.c.test]
unit = ["sh -c 'exit 7'"]
''')
    assert cli.main(["--config", str(path), "test", "c"]) == 1


def test_dry_run_through_main(example_config, capsys):
    assert cli.main(["--config", str(example_config), "--dry-run", "test", "bff"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[:2] == ["WOULD RUN uv sync", "WOULD RUN make test"]


def test_later_slice_verbs_exit_1(example_config, capsys):
    assert cli.main(["--config", str(example_config), "logs", "app"]) == 1
    assert "not implemented in this slice" in capsys.readouterr().err
