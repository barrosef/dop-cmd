"""The command line, generated from the verb registry. The one place an exit code is decided (B14):
0 all done · 3 nothing failed but something skipped or nothing to do · 1 something failed ·
2 usage or configuration error, nothing attempted."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TextIO

from . import __version__
from .config import find_workspace, load
from .context import Context, Filters, demand_key
from .outcome import EXIT_FAILED, EXIT_USAGE, UsageError
from .verbs import registry

_FILTER_ARGS = {
    "tasks": (("--tasks",), {"metavar": "KEY", "help": "demand keys (e.g. SUOPT-1530)"}),
    "app": (("--app",), {"dest": "apps", "metavar": "APP", "help": "applications"}),
    "repo": (("--repo",), {"dest": "repos", "metavar": "REPO", "help": "repositories"}),
}


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # usage errors are exit 2, as argparse does, but through main()
        raise UsageError(f"{self.prog}: {message}")


def _add_globals(p: argparse.ArgumentParser, top: bool) -> None:
    default = None if top else argparse.SUPPRESS
    p.add_argument("--workspace", metavar="PATH", default=default,
                   help="workspace root (default: nearest ancestor holding dop.toml)")
    p.add_argument("--dry-run", action="store_true", default=False if top else argparse.SUPPRESS,
                   help="resolve and print what would be done; change nothing")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="dop", description="One local k3s environment for several demands at once.")
    parser.add_argument("--version", action="version", version=f"dop {__version__}")
    _add_globals(parser, top=True)
    top = parser.add_subparsers(dest="_verb", metavar="VERB", parser_class=_Parser)
    groups: dict[str, argparse._SubParsersAction] = {}

    for name, mod in registry().items():
        spec = mod.VERB
        words = name.split()
        container = top
        for i, w in enumerate(words[:-1]):
            key = " ".join(words[: i + 1])
            if key not in groups:
                gp = container.add_parser(w, help=f"{key} …")
                groups[key] = gp.add_subparsers(dest=f"_verb{i + 1}", metavar="VERB", parser_class=_Parser)
            container = groups[key]
        p = container.add_parser(words[-1], help=spec.help, description=spec.help)
        _add_globals(p, top=False)
        for f in spec.filters:
            flags, kw = _FILTER_ARGS[f]
            p.add_argument(*flags, nargs="+", action="extend", default=[], **kw)
        dests = [p.add_argument(*opt.flags, **opt.kwargs).dest for opt in spec.options]
        p.set_defaults(_module=mod, _options=dests)
    return parser


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    out = out or sys.stdout
    argv = sys.argv[1:] if argv is None else argv
    try:
        parser = build_parser()
        try:
            args = parser.parse_args(argv)
        except SystemExit as exc:  # --help / --version
            return int(exc.code or 0)
        mod = getattr(args, "_module", None)
        if mod is None:
            parser.print_help(out)
            return EXIT_USAGE
        root = Path(args.workspace) if args.workspace else find_workspace(Path.cwd())
        ws = load(root)
        filters = Filters(
            tasks=tuple(dict.fromkeys(demand_key(t) for t in getattr(args, "tasks", []))),
            apps=tuple(dict.fromkeys(getattr(args, "apps", []))),
            repos=tuple(dict.fromkeys(getattr(args, "repos", []))),
        )
        options = {d: getattr(args, d) for d in args._options}
        ctx = Context(ws=ws, filters=filters, dry_run=args.dry_run, out=out, options=options,
                      command="dop " + " ".join(argv))
        summary = mod.run(ctx)
    except UsageError as exc:
        print(f"dop: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("dop: interrupted", file=sys.stderr)
        return EXIT_FAILED
    except Exception as exc:  # a defect in dop itself: still one exit point
        print(f"dop: internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED

    if args.dry_run:
        print("dry-run: nothing was changed", file=out)
    summary.render(out)
    return summary.exit_code()


def entry() -> None:
    sys.exit(main())
