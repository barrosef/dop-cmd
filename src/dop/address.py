"""Names and addresses (B3, B4, B17, B18, B19). The scheme lives in the workspace config; nothing
here hard-codes a host or a port."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping

from .config import Workspace
from .outcome import UsageError

_URL_REF = re.compile(r"\{url:([^}]*)\}")

# Kubernetes names a namespace with one DNS label: at most 63 characters.
NAME_MAX = 63


def namespace(ws: Workspace, demand: str) -> str:
    """B4: `<prefix>-<demand>`, lower case."""
    return f"{ws.address.namespace_prefix}-{demand}".lower()


def check_namespace(ws: Workspace, demand: str) -> None:
    """A demand whose namespace can never exist is a usage error, before anything is attempted
    (B4, B14)."""
    ns = namespace(ws, demand)
    if len(ns) > NAME_MAX:
        raise UsageError(f"{demand}: namespace {ns} is longer than {NAME_MAX} characters")


def host(ws: Workspace, demand: str | None, app: str) -> str:
    """B3 hostname, no scheme or port: `<app>.<demand>.<domain>`, or `<service>.<domain>` for a
    shared service (demand None). Lower case always."""
    parts = [app] if demand is None else [app, demand]
    return ".".join([*parts, ws.address.domain]).lower()


def address(ws: Workspace, demand: str | None, app: str, form: str = "browser") -> str:
    """The URL an app answers at.

    form="browser": through the one entry port, `http://<app>.<demand>.<domain>:<port>` (B2, B3);
                    demand None gives a shared service, `http://<service>.<domain>:<port>`.
    form="service": inside the demand's namespace, `http://<service>:<app port>` — the same in
                    every namespace, so it does not depend on the demand (B18).
    """
    if form == "browser":
        if demand is not None and app not in ws.apps:
            raise UsageError(f"unknown app {app!r}")
        return f"http://{host(ws, demand, app)}:{ws.address.port}"
    if form == "service":
        if app not in ws.apps:
            raise UsageError(f"unknown app {app!r}")
        a = ws.apps[app]
        return f"http://{a.service}:{a.port}"
    raise ValueError(f"unknown address form {form!r}")


def expand(
    template: str,
    ws: Workspace,
    demand: str,
    *,
    apps: Collection[str] | None = None,
    fallbacks: Mapping[str, str] | None = None,
    form: str = "browser",
) -> str:
    """Render every `{url:<app>}` in `template` with the demand's address of that app.

    apps:      the demand's apps (B9 + B19). When given, a referenced app outside it renders as
               its fallback from `fallbacks` (B17, B18) — typically `App.fallbacks()` of the app
               declaring the template. None means every referenced app is taken to be in the demand.
    Unknown app, or an app outside the demand with no fallback: UsageError.
    """

    def one(m: re.Match) -> str:
        ref = m.group(1)
        if ref not in ws.apps:
            raise UsageError(f"{{url:{ref}}}: unknown app")
        if apps is not None and ref not in apps:
            if fallbacks is None or ref not in fallbacks:
                raise UsageError(f"{{url:{ref}}}: {ref} is not in {demand} and has no declared fallback")
            return fallbacks[ref]
        return address(ws, demand, ref, form)

    return _URL_REF.sub(one, template)
