"""`dop` — operates the project's environment from its `dop.toml`.

Exit codes: 0 done, 1 failed, 2 usage or configuration error (including a
missing tool), 3 partial.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from . import __version__
from .config import TEST_LAYERS, find_config, load
from .core.errors import ConfigError, MCPError, SecurityViolationError, ToolMissing
from .core.security import redact

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_PARTIAL = 3

VERBS = ("up", "down", "status", "logs", "test", "reset")


class SecureArgumentParser(argparse.ArgumentParser):
    """argparse whose usage errors are redacted before printing (exit 2)."""

    def error(self, message: str):  # type: ignore[override]
        try:
            message = redact(message)
        except SecurityViolationError:
            message = "invalid arguments"
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def _global_flags(parser: argparse.ArgumentParser, *, suppress: bool) -> None:
    default = argparse.SUPPRESS if suppress else None
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS if suppress else False,
        help="print what would run (WOULD RUN / WOULD WAIT) and change nothing",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS if suppress else False,
        help="say more",
    )
    parser.add_argument("--config", metavar="PATH", default=default, help="the dop.toml to use (default: nearest upwards, or $DOP_CONFIG)")


def build_parser() -> argparse.ArgumentParser:
    parser = SecureArgumentParser(prog="dop", description="Operates the project's environment from its dop.toml.")
    parser.add_argument("--version", action="version", version=f"dop {__version__}")
    _global_flags(parser, suppress=False)
    sub = parser.add_subparsers(dest="command", metavar="{" + ",".join(VERBS) + "}", parser_class=SecureArgumentParser)
    sub.required = True

    def verb(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, description=help_text)
        _global_flags(p, suppress=True)
        return p

    p = verb("up", "brings up the environment and the named components (default: all) with what they need")
    p.add_argument("components", nargs="*", metavar="component")
    verb("down", "stops the app processes and the local cluster; data is kept")
    verb("status", "shows the environment and every component; changes nothing")
    p = verb("logs", "follows a component's logs")
    p.add_argument("component")
    p = verb("test", "runs a component's test layer as dop.toml declares it")
    p.add_argument("component")
    p.add_argument("--layer", choices=TEST_LAYERS, default="unit", help="default: unit")
    verb("reset", "starts the local environment over; all local data is lost")
    return parser


def _error(message: str) -> None:
    try:
        message = redact(message)
    except SecurityViolationError:
        message = "error message withheld (security policy)"
    print(f"dop: {message}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        path = Path(args.config).expanduser() if args.config else find_config(Path.cwd())
        config = load(path)
        module = importlib.import_module(f".verbs.{args.command}", __package__)
        return int(module.run(config, args))
    except (ConfigError, ToolMissing) as exc:
        _error(str(exc))
        return EXIT_USAGE
    except NotImplementedError:
        _error(f"{args.command}: not implemented in this slice")
        return EXIT_FAILED
    except MCPError as exc:
        _error(str(exc))
        return EXIT_FAILED
    except KeyboardInterrupt:
        _error("interrupted")
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
