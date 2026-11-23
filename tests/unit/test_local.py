"""--local (one home directory and settings file) and EVOLVE_PROXY: altk_evolve.local and the launchers."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from altk_evolve import local

pytestmark = pytest.mark.unit


@pytest.fixture
def restore_environ():
    """For tests that go through os.environ, as the launchers do: put it back afterwards."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


def write_settings(home: Path, text: str, mode: int = 0o600) -> Path:
    path = home / local.SETTINGS_FILE
    path.write_text(text)
    path.chmod(mode)
    return path


# --- The settings file and defaults ------------------------------------------------------------


def test_the_settings_file_is_loaded_into_the_environment(home):
    write_settings(home, '# the model\nEVOLVE_MODEL_NAME=litellm_proxy/m\nLITELLM_PROXY_API_KEY="sk-x"\n')  # pragma: allowlist secret
    env: dict[str, str] = {}
    setup = local.apply_local(home, env)
    # LITELLM_PROXY_API_KEY isn't an EVOLVE_ setting: a .env file can't supply it, this file can.
    assert env["EVOLVE_MODEL_NAME"] == "litellm_proxy/m"
    assert env["LITELLM_PROXY_API_KEY"] == "sk-x"
    assert sorted(setup.from_file) == ["EVOLVE_MODEL_NAME", "LITELLM_PROXY_API_KEY"]


def test_the_environment_wins_over_the_file_and_the_file_over_the_defaults(home):
    write_settings(home, "EVOLVE_MODEL_NAME=from-file\nEVOLVE_INJECTION_MODE=static\n")
    env = {"EVOLVE_MODEL_NAME": "from-env"}
    setup = local.apply_local(home, env)
    assert env["EVOLVE_MODEL_NAME"] == "from-env"
    assert env["EVOLVE_INJECTION_MODE"] == "static"  # the file's, not the default
    assert env["EVOLVE_BACKEND"] == "filesystem"  # nothing else set it
    assert "EVOLVE_INJECTION_MODE" not in setup.defaults


def test_an_empty_variable_in_the_environment_still_wins(home):
    # HTTPS_PROXY= is a choice (no proxy), not an absence.
    write_settings(home, "HTTPS_PROXY=http://proxy.example:3128\n")
    env = {"HTTPS_PROXY": ""}
    local.apply_local(home, env)
    assert env["HTTPS_PROXY"] == ""


def test_defaults_put_the_data_under_the_home_whatever_the_working_directory(home, tmp_path, monkeypatch):
    seen = []
    for cwd in (tmp_path, home):
        monkeypatch.chdir(cwd)
        env: dict[str, str] = {}
        local.apply_local(home, env)
        seen.append((env["EVOLVE_DATA_DIR"], env["EVOLVE_SQLITE_PATH"]))
    assert seen[0] == seen[1] == (str(home / "data"), str(home / "evolve.sqlite.db"))
    assert Path(seen[0][0]).is_absolute()


def test_defaults_without_a_settings_file(home):
    env: dict[str, str] = {}
    setup = local.apply_local(home, env)
    assert not setup.settings_loaded
    assert env["EVOLVE_INJECTION_MODE"] == "retrieval"
    assert env["EVOLVE_OTEL_ENABLED"] == "false"
    assert env[local.HOME_ENV] == str(home)


def test_the_home_is_the_flag_then_the_environment_then_the_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    assert local.resolve_home("/flag", {local.HOME_ENV: "/env"}) == Path("/flag").resolve()
    assert local.resolve_home(None, {local.HOME_ENV: "/env"}) == Path("/env").resolve()
    assert local.resolve_home(None, {}) == (tmp_path / "user" / ".evolve").resolve()


def test_the_home_is_created_private(tmp_path):
    local.apply_local(tmp_path / "new-home", {})
    assert stat.S_IMODE((tmp_path / "new-home").stat().st_mode) == 0o700


@pytest.mark.parametrize(("mode", "warned"), [(0o644, True), (0o640, True), (0o600, False)])
def test_a_settings_file_others_can_read_is_reported(home, mode, warned):
    write_settings(home, "EVOLVE_MODEL_NAME=m\n", mode=mode)
    setup = local.apply_local(home, {})
    assert any("can be read by other users" in w for w in setup.warnings) is warned


def test_a_hooks_file_in_the_home_is_used(home):
    (home / local.HOOKS_FILE).write_text("plugins: []\n")
    env: dict[str, str] = {}
    local.apply_local(home, env)
    assert env["EVOLVE_HOOKS_CONFIG"] == str(home / local.HOOKS_FILE)


def test_without_a_hooks_file_the_usual_lookup_stays(home):
    env: dict[str, str] = {}
    local.apply_local(home, env)
    assert "EVOLVE_HOOKS_CONFIG" not in env


# --- EVOLVE_PROXY -------------------------------------------------------------------------------

ALL_PROXIES = {key: "http://127.0.0.1:47600" for key in local.PROXY_VARS}


def test_evolve_proxy_is_consumed_so_no_client_reads_it_as_a_proxy(restore_environ):
    """urllib, requests and botocore read every *_proxy variable as a proxy for the scheme its
    prefix names: left in place, EVOLVE_PROXY=none read as {'evolve': 'none'}."""
    import urllib.request

    for key in local.PROXY_VARS:
        os.environ.pop(key, None)
    os.environ.update({"HTTPS_PROXY": "http://127.0.0.1:47600", local.PROXY_ENV: "none"})
    local.apply_proxy()
    assert local.PROXY_ENV not in os.environ
    assert urllib.request.getproxies() == {}


def test_a_chosen_loopback_proxy_is_not_pointed_out_after_it_is_consumed(home, restore_environ, capsys):
    for key in [k for k in os.environ if k.startswith("EVOLVE_") or k in local.PROXY_VARS]:
        del os.environ[key]
    os.environ["OPENAI_API_KEY"] = "sk"  # pragma: allowlist secret
    write_settings(home, "HTTPS_PROXY=http://127.0.0.1:47600\nEVOLVE_PROXY=inherit\n")
    local.prepare_server(["--local", "--home", str(home)])
    err = capsys.readouterr().err
    assert "proxy: http://127.0.0.1:47600" in err and "go through the local proxy" not in err


def test_proxy_none_removes_every_spelling():
    env = {**ALL_PROXIES, local.PROXY_ENV: "none", "NO_PROXY": "example.com"}
    assert local.apply_proxy(env) is None
    assert not any(key in env for key in local.PROXY_VARS)
    assert env["NO_PROXY"] == "example.com"


@pytest.mark.parametrize("value", ["", "inherit", "INHERIT"])
def test_proxy_inherit_leaves_the_environment_alone(value):
    env = {**ALL_PROXIES, local.PROXY_ENV: value}
    assert local.apply_proxy(env) == "http://127.0.0.1:47600"
    assert all(env[key] == "http://127.0.0.1:47600" for key in local.PROXY_VARS)


def test_a_proxy_url_becomes_evolves_proxy():
    env = {**ALL_PROXIES, local.PROXY_ENV: "http://proxy.example:3128"}
    assert local.apply_proxy(env) == "http://proxy.example:3128"
    assert env["HTTPS_PROXY"] == env["http_proxy"] == "http://proxy.example:3128"
    assert "ALL_PROXY" not in env


@pytest.mark.parametrize("value", ["bogus", "proxy.example:3128", "ftp://proxy.example", "http://"])
def test_a_bad_proxy_value_is_an_error(value):
    with pytest.raises(ValueError, match="EVOLVE_PROXY must be inherit, none or a proxy URL"):
        local.apply_proxy({local.PROXY_ENV: value})


@pytest.mark.parametrize(
    ("env", "warned"),
    [
        ({"HTTPS_PROXY": "http://127.0.0.1:47600"}, True),
        ({"https_proxy": "http://localhost:47600"}, True),
        ({"HTTPS_PROXY": "http://[::1]:47600"}, True),
        ({"HTTPS_PROXY": "http://127.0.0.1:47600", local.PROXY_ENV: "inherit"}, False),
        ({"HTTPS_PROXY": "http://proxy.corp.example:8080"}, False),
        ({}, False),
    ],
)
def test_a_loopback_proxy_is_pointed_out_until_chosen(env, warned):
    env = {**env, "OPENAI_API_KEY": "sk"}  # pragma: allowlist secret
    assert any("go through the local proxy" in w for w in local.startup_warnings(env)) is warned


# --- Credentials and the report -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "missing"),
    [
        ({"EVOLVE_MODEL_NAME": "litellm_proxy/aws/m"}, True),
        ({"EVOLVE_MODEL_NAME": "litellm_proxy/aws/m", "LITELLM_PROXY_API_KEY": "k"}, False),
        ({}, True),  # gpt-4o, and no OPENAI_API_KEY
        ({"OPENAI_API_KEY": "k"}, False),
        ({"EVOLVE_MODEL_NAME": "bedrock/anthropic.claude", "AWS_PROFILE": "dev"}, False),
        ({"EVOLVE_MODEL_NAME": "someprovider/model"}, False),  # a provider the check doesn't know
        ({"EVOLVE_GUIDELINES_MODEL": "anthropic/claude", "EVOLVE_MODEL_NAME": "openai/gpt", "OPENAI_API_KEY": "k"}, True),
    ],
)
def test_missing_model_credentials_are_reported(env, missing):
    assert any(w.startswith("No credentials for") for w in local.startup_warnings(env)) is missing


def test_the_report_never_shows_a_credential(home):
    write_settings(home, "EVOLVE_MODEL_NAME=litellm_proxy/m\nLITELLM_PROXY_API_KEY=sk-secret-value\n")
    env: dict[str, str] = {}
    setup = local.apply_local(home, env)
    text = "\n".join(local.report(setup, env))
    assert "sk-secret-value" not in text
    assert f"Evolve home: {home}" in text and "model: litellm_proxy/m" in text


# --- Where the options are read -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--local", "entities", "list"], (True, None)),
        (["--home", "/h", "serve"], (True, "/h")),
        (["--home=/h", "namespaces", "list"], (True, "/h")),
        (["entities", "list", "--local"], (False, None)),  # after the command it is the command's
        (["--help"], (False, None)),
    ],
)
def test_evolve_takes_local_before_the_command(argv, expected):
    assert local.parse_local_options(argv) == expected


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--transport", "sse", "--local"], (True, None)),
        (["--port", "9300", "--home", "/h"], (True, "/h")),
        (["--transport", "sse"], (False, None)),
    ],
)
def test_evolve_mcp_takes_local_anywhere(argv, expected):
    assert local._server_local_options(argv) == expected


def _run_python(code: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("EVOLVE_") and k not in local.PROXY_VARS}
    return subprocess.run([sys.executable, "-c", code], cwd=cwd, env={**clean, **env}, capture_output=True, text=True, timeout=120)


def test_evolves_configuration_reads_the_local_settings(home, tmp_path):
    """The point of applying --local first: settings objects are built when their modules load."""
    write_settings(home, "EVOLVE_MODEL_NAME=litellm_proxy/from-file\nEVOLVE_RETRIEVAL_TOP_K=4\n")
    code = (
        "import json, sys\n"
        "from altk_evolve.local import prepare_server\n"
        f"prepare_server(['--local', '--home', {str(home)!r}])\n"
        "from altk_evolve.config.evolve import evolve_config\n"
        "from altk_evolve.config.filesystem import filesystem_settings\n"
        "from altk_evolve.config.llm import llm_settings\n"
        "print(json.dumps([filesystem_settings.data_dir, evolve_config.injection_mode, evolve_config.retrieval_top_k, llm_settings.guidelines_model]))\n"
    )
    out = _run_python(code, cwd=tmp_path, env={"HOME": str(tmp_path)})
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == [str(home / "data"), "retrieval", 4, "litellm_proxy/from-file"]


def test_evolve_mcp_reports_on_stderr_before_the_server_loads(home, tmp_path):
    # stdout is the MCP channel over stdio, so nothing of the report may reach it.
    code = f"import sys\nsys.argv = ['evolve-mcp', '--local', '--home', {str(home)!r}, '--help']\nfrom altk_evolve import launch\nlaunch.mcp_main()\n"
    out = _run_python(code, cwd=tmp_path, env={"HOME": str(tmp_path)})
    assert out.returncode == 0, out.stderr
    assert f"Evolve home: {home}" in out.stderr
    assert "Evolve home" not in out.stdout and "--local" in out.stdout


# --- evolve serve, and evolve's options -----------------------------------------------------------


def test_evolve_serve_runs_the_same_server(monkeypatch):
    from altk_evolve.cli.cli import app
    from altk_evolve.frontend.mcp import __main__ as server

    calls: list[list[str]] = []
    monkeypatch.setattr(server, "main", lambda argv=None: calls.append(argv))
    result = CliRunner().invoke(app, ["serve", "--transport", "sse", "--port", "9300", "--no-dashboard"])
    assert result.exit_code == 0, result.output
    assert calls == [["--transport", "sse", "--host", "127.0.0.1", "--port", "9300", "--no-dashboard"]]


def test_evolves_local_option_says_when_it_cannot_take_effect(monkeypatch):
    # Through CliRunner, not the evolve command, so nothing applied --local first.
    from altk_evolve.cli.cli import app

    monkeypatch.delenv(local.HOME_ENV, raising=False)
    result = CliRunner().invoke(app, ["--local", "prompts", "--help"])
    assert "take effect only through the `evolve` command" in result.output


def test_only_evolve_serve_prints_the_server_report(home, restore_environ, capsys):
    for key in [k for k in os.environ if k.startswith("EVOLVE_")]:
        del os.environ[key]
    write_settings(home, "EVOLVE_MODEL_NAME=litellm_proxy/m\n", mode=0o644)
    local.prepare_cli(["--home", str(home), "entities", "list"])
    listing = capsys.readouterr().err
    assert "can be read by other users" in listing and "Evolve home" not in listing
    local.prepare_cli(["--home", str(home), "serve"])
    assert f"Evolve home: {home}" in capsys.readouterr().err
