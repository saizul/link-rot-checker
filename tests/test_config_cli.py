import io
import json

import pytest

from fakeweb import demo_site, wayback_api
from link_rot_checker.cli import EXIT_ERROR, EXIT_OK, EXIT_PROBLEMS, _formats, main
from link_rot_checker.config import ConfigError, Settings, load_config_file, settings_from_mapping

FAST = ["--rate", "0", "--wayback-rate", "0", "--retries", "0", "--timeout", "5"]


def run_cli(args, tmp_path, stdin=None, web=None):
    web = web or demo_site()
    out = io.StringIO()
    code = main(
        [*args, "-o", str(tmp_path / "out"), *FAST],
        stdout=out,
        stdin=stdin,
        transport=web.transport(),
        wayback_transport=wayback_api({}),
    )
    return code, out.getvalue(), web


def test_config_file(tmp_path):
    path = tmp_path / "lrc.toml"
    path.write_text(
        '[link-rot-checker]\nmax-pages = 20\nexclude = ["/tag/"]\nfail_on = ["broken", "parked"]\n'
        'rate_per_host = 2\nheader_env = { "X-Token" = "SITE_PREVIEW_TOKEN" }\n'
    )
    settings = load_config_file(path)
    assert settings.max_pages == 20 and settings.exclude == ["/tag/"] and settings.rate_per_host == 2.0
    assert settings.header_env == {"X-Token": "SITE_PREVIEW_TOKEN"}
    settings.validate()


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"nope": 1}, "unknown setting"),
        ({"max_pages": "ten"}, "integer"),
        ({"crawl": "yes"}, "true or false"),
        ({"exclude": 5}, "list"),
        ({"header_env": "x"}, "table"),
    ],
)
def test_config_type_errors(data, message):
    with pytest.raises(ConfigError, match=message):
        settings_from_mapping(data)


@pytest.mark.parametrize(
    "settings",
    [
        Settings(max_pages=0),
        Settings(concurrency=0),
        Settings(rate_per_host=-1),
        Settings(chain_threshold=1),
        Settings(fail_on=["nope"]),
        Settings(exclude=["("]),
        Settings(wayback_timestamp="2020-01"),
        Settings(header_env={"X": "not a var"}),
        Settings(timeout=0),
    ],
)
def test_validate(settings):
    with pytest.raises(ConfigError):
        settings.validate()


def test_config_file_errors(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        load_config_file(tmp_path / "missing.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("[x\n")
    with pytest.raises(ConfigError):
        load_config_file(bad)
    other = tmp_path / "other.toml"
    other.write_text("[other]\na = 1\n")
    with pytest.raises(ConfigError, match="no \\[link-rot-checker\\]"):
        load_config_file(other)


def test_formats():
    assert _formats(["md,html", "csv"]) == ["markdown", "html", "csv"]
    assert _formats(["all"]) == ["csv", "json", "markdown", "html"]
    assert _formats(None) == []


def test_scan_fails_on_broken(tmp_path):
    code, out, _ = run_cli(["scan", "https://example.com/", "-f", "json"], tmp_path)
    assert code == EXIT_PROBLEMS
    assert "fix first:" in out and "FAIL:" in out
    data = json.loads((tmp_path / "out" / "link-rot.json").read_text())
    assert data["summary"]["broken"] == 4


def test_scan_fail_on_none_and_threshold(tmp_path):
    assert run_cli(["scan", "https://example.com/", "--fail-on", "none", "-q"], tmp_path)[0] == EXIT_OK
    assert run_cli(["scan", "https://example.com/", "--fail-threshold", "6", "-q"], tmp_path)[0] == EXIT_OK
    assert run_cli(["scan", "https://example.com/", "--fail-on", "parked", "-q"], tmp_path)[0] == EXIT_PROBLEMS


def test_scan_quiet_and_default_formats(tmp_path):
    _code, out, _ = run_cli(["scan", "https://example.com/", "-q"], tmp_path)
    assert out == ""
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["link-rot.html", "link-rot.json"]


def test_check_command_from_stdin(tmp_path):
    stdin = io.StringIO("# list\nhttps://ext.test/ok\n\nhttps://ext.test/ok extra words\n")
    code, out, web = run_cli(["check", "-", "-f", "csv"], tmp_path, stdin=stdin)
    assert code == EXIT_OK
    assert "1 unique targets" in out
    assert all("example.com" not in url for _, url in web.requests)


def test_check_command_from_file(tmp_path):
    urls = tmp_path / "urls.txt"
    urls.write_text("https://ext.test/gone\n")
    code, _, _ = run_cli(["check", str(urls), "-q"], tmp_path)
    assert code == EXIT_PROBLEMS


@pytest.mark.parametrize(
    "args",
    [
        ["scan"],
        ["scan", "https://example.com/", "--max-pages", "0"],
        ["scan", "https://example.com/", "-f", "pdf"],
        ["scan", "ftp://example.com/"],
        ["scan", "https://example.com/", "--host-rate", "x"],
        ["scan", "https://example.com/", "--host-rate", "a.test=fast"],
        ["scan", "https://example.com/", "--config", "/nonexistent/lrc.toml"],
        ["check", "-"],
    ],
)
def test_errors_exit_2(args, tmp_path):
    code, _, _ = run_cli(args, tmp_path, stdin=io.StringIO(""))
    assert code == EXIT_ERROR


def test_no_command_prints_help(tmp_path):
    out = io.StringIO()
    assert main([], stdout=out) == EXIT_ERROR
    assert "scan" in out.getvalue()


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "link-rot-checker 0.1.0" in capsys.readouterr().out
