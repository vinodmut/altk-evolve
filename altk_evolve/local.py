"""Running Evolve for one user on this machine, from one home directory and one settings file.

Evolve reads its settings from the environment (and ``EVOLVE_*`` ones from a ``.env`` in the
working directory), and its defaults put data where it is started: ``evolve_data`` and
``entities.sqlite.db`` are relative paths. Running it as a long-lived local server, and reviewing
what it learned with the ``evolve`` CLI, therefore needed the same environment, the same working
directory and absolute paths in both. ``--local`` (on ``evolve-mcp`` and ``evolve``) replaces that
with:

- a home directory: ``--home``, else ``$EVOLVE_HOME``, else ``~/.evolve``;
- one settings file, ``<home>/config.env``, loaded into the process environment, so it can hold the
  model name and the provider's credentials as well as ``EVOLVE_*`` settings. (A ``.env`` covers
  only the ``EVOLVE_*`` settings Evolve's config classes read: ``EVOLVE_MODEL_NAME`` and LiteLLM's
  credentials are read from the environment itself.);
- defaults for what is still unset: the filesystem backend with its data and database under the
  home directory, retrieval injection, and OpenTelemetry off.

A variable already in the environment wins over the file, and the file wins over the defaults.

``EVOLVE_PROXY`` (``inherit``, ``none`` or a URL) chooses the proxy for Evolve's own outbound
connections, with or without ``--local``. It exists because an agent routed through a local proxy,
such as Cortex, passes ``HTTPS_PROXY`` to everything it starts, an MCP server included.

Both run before anything imports Evolve's configuration: its settings objects are built when their
modules are imported, so the environment has to be ready first.
"""

from __future__ import annotations

import ipaddress
import os
import stat
import sys
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

HOME_ENV = "EVOLVE_HOME"
DEFAULT_HOME = "~/.evolve"
SETTINGS_FILE = "config.env"
HOOKS_FILE = "evolve.hooks.yaml"
PROXY_ENV = "EVOLVE_PROXY"
# Every spelling an HTTP client may read. httpx, requests and botocore read both cases.
PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy")

# The credentials LiteLLM reads for a model's provider, by the model string's prefix, for the
# missing-credentials warning. Not exhaustive: a prefix that isn't here gets no warning.
_PROVIDER_CREDENTIALS: dict[str, tuple[str, ...]] = {
    "litellm_proxy": ("LITELLM_PROXY_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "azure": ("AZURE_API_KEY", "AZURE_AD_TOKEN"),
    "bedrock": ("AWS_ACCESS_KEY_ID", "AWS_PROFILE", "AWS_BEARER_TOKEN_BEDROCK", "AWS_WEB_IDENTITY_TOKEN_FILE"),
    "gemini": ("GEMINI_API_KEY",),
    "watsonx": ("WATSONX_APIKEY", "WATSONX_API_KEY", "WATSONX_TOKEN"),
    "groq": ("GROQ_API_KEY",),
}


@dataclass
class LocalSetup:
    """What ``apply_local`` did, for the startup report."""

    home: Path
    settings_file: Path
    settings_loaded: bool
    from_file: list[str] = field(default_factory=list)
    defaults: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def resolve_home(home: str | os.PathLike[str] | None = None, environ: MutableMapping[str, str] | None = None) -> Path:
    """``home``, else ``$EVOLVE_HOME``, else ``~/.evolve``, as an absolute path."""
    environ = os.environ if environ is None else environ
    raw = os.fspath(home) if home is not None else (environ.get(HOME_ENV, "").strip() or DEFAULT_HOME)
    return Path(raw).expanduser().resolve()


def apply_local(home: str | os.PathLike[str] | None = None, environ: MutableMapping[str, str] | None = None) -> LocalSetup:
    """Prepare ``environ`` for a local Evolve rooted at ``home``. See the module docstring."""
    environ = os.environ if environ is None else environ
    from dotenv import dotenv_values

    root = resolve_home(home, environ)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    settings = root / SETTINGS_FILE
    setup = LocalSetup(home=root, settings_file=settings, settings_loaded=settings.is_file())

    if setup.settings_loaded:
        if settings.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            setup.warnings.append(f"{settings} can be read by other users, and it may hold API keys: chmod 600 {settings}")
        for key, value in dotenv_values(settings).items():
            # A variable already in the environment wins, even an empty one: HTTPS_PROXY= is a choice.
            if value is None or key in environ:
                continue
            environ[key] = value
            setup.from_file.append(key)

    defaults = {
        "EVOLVE_BACKEND": "filesystem",
        "EVOLVE_DATA_DIR": str(root / "data"),
        "EVOLVE_SQLITE_PATH": str(root / "evolve.sqlite.db"),
        "EVOLVE_INJECTION_MODE": "retrieval",
        "EVOLVE_OTEL_ENABLED": "false",
    }
    # Evolve has no "no hooks" value: an empty EVOLVE_HOOKS_CONFIG falls through to ./evolve.hooks.yaml
    # and ~/.config/evolve/hooks.yaml. So the home directory's file is used when there is one, and the
    # usual lookup otherwise.
    if (root / HOOKS_FILE).is_file():
        defaults["EVOLVE_HOOKS_CONFIG"] = str(root / HOOKS_FILE)
    for key, value in defaults.items():
        if key not in environ:
            environ[key] = value
            setup.defaults[key] = value
    # So anything started from here, and a later `evolve --local`, uses the same home.
    environ[HOME_ENV] = str(root)
    return setup


def apply_proxy(environ: MutableMapping[str, str] | None = None) -> str | None:
    """Apply ``EVOLVE_PROXY`` to ``environ`` and return the proxy Evolve will use, if any.

    ``inherit`` (or unset) leaves the proxy variables alone; ``none`` removes them, so every
    connection Evolve makes goes direct; a URL makes it Evolve's proxy. It applies to all of
    Evolve's outbound connections (its LLM calls, and also, say, downloading its embedding model),
    because clients read the proxy from the environment.
    """
    environ = os.environ if environ is None else environ
    choice = environ.get(PROXY_ENV, "").strip()
    if choice and choice.lower() not in ("inherit", "none"):
        parts = urlsplit(choice)
        if parts.scheme not in ("http", "https", "socks5", "socks5h") or not parts.hostname:
            raise ValueError(f"{PROXY_ENV} must be inherit, none or a proxy URL such as http://proxy.example:3128, got {choice!r}")
        for key in PROXY_VARS:
            environ.pop(key, None)
        for key in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            environ[key] = choice
    elif choice.lower() == "none":
        for key in PROXY_VARS:
            environ.pop(key, None)
    return effective_proxy(environ)


def effective_proxy(environ: MutableMapping[str, str] | None = None) -> str | None:
    """The proxy an HTTPS request from Evolve would use, the way clients choose it."""
    environ = os.environ if environ is None else environ
    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        value = environ.get(key, "").strip()
        if value:
            return value
    return None


def is_loopback(url: str) -> bool:
    host = urlsplit(url if "://" in url else f"http://{url}").hostname or ""
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def guidelines_model(environ: MutableMapping[str, str] | None = None) -> str:
    """The model guideline generation will use, chosen as altk_evolve.config.llm chooses it."""
    environ = os.environ if environ is None else environ
    for key in ("EVOLVE_GUIDELINES_MODEL", "EVOLVE_TIPS_MODEL", "EVOLVE_MODEL_NAME"):
        value = environ.get(key, "").strip()
        if value:
            return value
    return "gpt-4o"


def missing_credentials(model: str, environ: MutableMapping[str, str] | None = None) -> tuple[str, ...]:
    """The credential variables ``model``'s provider takes when none of them is set, else ()."""
    environ = os.environ if environ is None else environ
    provider = model.split("/", 1)[0] if "/" in model else "openai"
    wanted = _PROVIDER_CREDENTIALS.get(provider, ())
    if any(environ.get(key, "").strip() for key in wanted):
        return ()
    return wanted


def startup_warnings(environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Warnings worth printing whenever a server starts, local or not."""
    environ = os.environ if environ is None else environ
    warnings = []
    proxy = effective_proxy(environ)
    if proxy and is_loopback(proxy) and not environ.get(PROXY_ENV, "").strip():
        warnings.append(
            f"Evolve's outbound connections, its LLM calls included, go through the local proxy {proxy}. "
            f"If that is Cortex or another proxy recording agent traffic, set {PROXY_ENV}=none; "
            f"set {PROXY_ENV}=inherit to keep it and silence this warning."
        )
    model = guidelines_model(environ)
    if wanted := missing_credentials(model, environ):
        warnings.append(f"No credentials for {model}: set {' or '.join(wanted)}, or guideline generation will fail.")
    return warnings


def report(setup: LocalSetup | None, environ: MutableMapping[str, str] | None = None) -> list[str]:
    """The lines the server prints at startup. Values of credentials are never printed."""
    environ = os.environ if environ is None else environ
    lines = []
    if setup is not None:
        source = (
            f"{setup.settings_file} ({len(setup.from_file)} set from it)"
            if setup.settings_loaded
            else f"none ({setup.settings_file} not found)"
        )
        lines += [
            f"Evolve home: {setup.home}",
            f"  settings file: {source}",
            f"  backend: {environ.get('EVOLVE_BACKEND', '')}, data: {environ.get('EVOLVE_DATA_DIR', '')}",
            f"  database: {environ.get('EVOLVE_SQLITE_PATH', '')}",
            f"  injection mode: {environ.get('EVOLVE_INJECTION_MODE', '')}",
            f"  hooks: {environ.get('EVOLVE_HOOKS_CONFIG') or 'the usual lookup (./evolve.hooks.yaml, ~/.config/evolve/hooks.yaml)'}",
        ]
    lines.append(f"  model: {guidelines_model(environ)}")
    lines.append(f"  proxy: {effective_proxy(environ) or 'none (direct)'}")
    lines += [f"warning: {w}" for w in ((setup.warnings if setup else []) + startup_warnings(environ))]
    return lines


def parse_local_options(argv: list[str]) -> tuple[bool, str | None]:
    """``--local`` and ``--home DIR`` (or ``--home=DIR``) among the options before the first
    positional argument, which is where ``evolve`` takes them. ``--home`` implies ``--local``."""
    local, home = False, None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--local":
            local = True
        elif arg == "--home" and i + 1 < len(argv):
            local, home = True, argv[i + 1]
            i += 1
        elif arg.startswith("--home="):
            local, home = True, arg.split("=", 1)[1]
        elif not arg.startswith("-"):
            break
        i += 1
    return local, home


def _server_local_options(argv: list[str]) -> tuple[bool, str | None]:
    """``--local`` and ``--home`` anywhere in ``evolve-mcp``'s options, which take no positionals."""
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--home")
    known, _ = parser.parse_known_args(argv)
    return known.local or known.home is not None, known.home


def _apply(local: bool, home: str | None) -> LocalSetup | None:
    setup = apply_local(home) if local else None
    try:
        apply_proxy()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    return setup


def prepare_server(argv: list[str]) -> LocalSetup | None:
    """For a server starting with ``argv``: apply ``--local``, ``--home`` and ``EVOLVE_PROXY``, then
    print to stderr (stdout is the MCP channel over stdio) the report with ``--local``, or only the
    warnings without it."""
    local, home = _server_local_options(argv)
    setup = _apply(local, home)
    lines = report(setup) if setup is not None else [f"warning: {w}" for w in startup_warnings()]
    for line in lines:
        print(line, file=sys.stderr)
    return setup


def prepare_cli(argv: list[str]) -> LocalSetup | None:
    """For the ``evolve`` CLI: apply ``--local``, ``--home`` and ``EVOLVE_PROXY`` given before the
    command, and print only the settings file's warnings: a command isn't a server starting."""
    local, home = parse_local_options(argv)
    setup = _apply(local, home)
    for warning in setup.warnings if setup else []:
        print(f"warning: {warning}", file=sys.stderr)
    return setup
