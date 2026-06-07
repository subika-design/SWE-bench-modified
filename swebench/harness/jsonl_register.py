"""
Register SWE-rebench-style JSONL tasks into the SWE-bench harness at runtime.

Each row supplies ``install_config`` (→ ``MAP_REPO_VERSION_TO_SPECS[repo][version]``),
``repo``, ``version``, and ``language`` (→ ``MAP_REPO_TO_EXT`` for Dockerfile selection).
Used automatically when ``run_evaluation --dataset_name`` points at a ``.jsonl`` file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from swebench.harness.constants import (
    MAP_REPO_TO_ENV_YML_PATHS,
    MAP_REPO_TO_EXT,
    MAP_REPO_TO_REQS_PATHS,
    MAP_REPO_VERSION_TO_SPECS,
)
from swebench.harness.log_parsers import MAP_REPO_TO_PARSER
from swebench.harness.log_parsers.c import MAP_REPO_TO_PARSER_C
from swebench.harness.log_parsers.go import MAP_REPO_TO_PARSER_GO
from swebench.harness.log_parsers.java import MAP_REPO_TO_PARSER_JAVA
from swebench.harness.log_parsers.javascript import MAP_REPO_TO_PARSER_JS
from swebench.harness.log_parsers.php import MAP_REPO_TO_PARSER_PHP
from swebench.harness.log_parsers.python import (
    MAP_REPO_TO_PARSER_PY,
    parse_log_django,
    parse_log_pytest,
)
from swebench.harness.log_parsers.ruby import MAP_REPO_TO_PARSER_RUBY
from swebench.harness.log_parsers.rust import MAP_REPO_TO_PARSER_RUST

LANGUAGE_TO_EXT: dict[str, str] = {
    "python": "py",
    "javascript": "js",
    "java": "java",
    "go": "go",
    "c": "c",
    "php": "php",
    "ruby": "rb",
    "rust": "rs",
}

HARNESS_INSTALL_CONFIG_KEYS: tuple[str, ...] = (
    "python",
    "packages",
    "install",
    "test_cmd",
    "pre_install",
    "pip_packages",
    "post_install",
    "reqs_path",
    "eval_commands",
    "docker_specs",
    "no_use_env",
    "apt-pkgs",
    "maven_junit_roots",
)


@dataclass
class RegisterReport:
    jsonl_path: str | None = None
    rows_seen: int = 0
    registered: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    parsers_added: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Registered {len(self.registered)} repo/version spec(s) "
            f"from {self.rows_seen} JSONL row(s)."
        ]
        for item in self.registered:
            lines.append(f"  + {item}")
        if self.parsers_added:
            lines.append(f"Added log parser(s) for: {', '.join(self.parsers_added)}")
        if self.skipped:
            lines.append(f"Skipped {len(self.skipped)} row(s) without install_config.")
        return "\n".join(lines)


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def normalize_repo(repo: str) -> str:
    repo = str(repo or "").strip()
    if "__" in repo and "/" not in repo:
        return repo.replace("__", "/", 1)
    return repo


def parse_install_config(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict) or not raw:
        return None
    return dict(raw)


def language_to_harness_ext(language: str) -> str:
    lang = str(language or "").strip().lower()
    ext = LANGUAGE_TO_EXT.get(lang)
    if not ext:
        raise ValueError(
            f"Unsupported language {language!r}; expected one of {sorted(LANGUAGE_TO_EXT)}"
        )
    return ext


def _as_cmd_list(cmd: str | list[str] | None) -> list[str]:
    flat: list[str] = []
    if isinstance(cmd, list):
        for item in cmd:
            flat.extend(_as_cmd_list(item))
        return flat
    s = str(cmd or "").strip()
    return [s] if s else []


def export_install_config_for_harness(
    install_config: dict[str, Any],
    *,
    language: str | None = None,
) -> dict[str, Any]:
    """Keep only keys the SWE-bench harness reads from specs."""
    _ = language
    out: dict[str, Any] = {}
    for key in HARNESS_INSTALL_CONFIG_KEYS:
        if key not in install_config:
            continue
        val = install_config[key]
        if val is None:
            continue
        if key in (
            "pre_install",
            "post_install",
            "reqs_path",
            "pip_packages",
            "eval_commands",
            "apt-pkgs",
            "maven_junit_roots",
        ):
            if not isinstance(val, list) or not val:
                continue
        out[key] = val
    if "install" in out:
        out["install"] = _as_cmd_list(out["install"])
    lang = str(language or install_config.get("language") or "").lower()
    if lang == "java":
        specs = dict(out.get("docker_specs") or {})
        if not specs.get("java_version"):
            specs["java_version"] = "17"
        out["docker_specs"] = specs
    return out


def install_config_to_harness_specs(
    install_config: dict[str, Any],
    *,
    language: str | None = None,
) -> dict[str, Any]:
    """Map task ``install_config`` to a ``MAP_REPO_VERSION_TO_SPECS`` entry."""
    ic = export_install_config_for_harness(install_config, language=language)
    specs: dict[str, Any] = {}
    for key in HARNESS_INSTALL_CONFIG_KEYS:
        if key in ic:
            specs[key] = ic[key]

    reqs = ic.get("reqs_path")
    if reqs and not specs.get("packages"):
        specs["packages"] = "requirements.txt"

    post = ic.get("post_install") or []
    if isinstance(post, list) and post:
        install = _as_cmd_list(specs.get("install") or "true")
        extra = [ln.strip() for ln in post if isinstance(ln, str) and ln.strip()]
        if extra:
            specs["install"] = extra if install == ["true"] else install + extra

    if not specs.get("install"):
        specs["install"] = ["true"]
    if not specs.get("test_cmd"):
        lang = str(language or install_config.get("language") or "python").lower()
        specs["test_cmd"] = (
            "./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1"
            if lang == "python"
            and "django" in str(install_config.get("repo") or "").lower()
            else "pytest -rA"
        )
    return specs


def default_log_parser(language: str, specs: dict[str, Any]):
    """Pick a log parser when the repo is not in upstream ``MAP_REPO_TO_PARSER``."""
    test_cmd = str(specs.get("test_cmd") or "").lower()
    lang = str(language or "").strip().lower()

    if "runtests.py" in test_cmd or (lang == "python" and "django" in test_cmd):
        return parse_log_django
    if lang == "java":
        if "gradle" in test_cmd:
            from swebench.harness.log_parsers.java import parse_log_gradle_custom

            return parse_log_gradle_custom
        from swebench.harness.log_parsers.java import parse_log_maven

        return parse_log_maven
    if lang == "javascript":
        from swebench.harness.log_parsers.javascript import (
            parse_log_javascript_jsonl,
            parse_log_jest,
            parse_log_vitest,
        )

        if "vitest" in test_cmd:
            if "junit" in test_cmd or "outputfile" in test_cmd.replace(" ", ""):
                return parse_log_javascript_jsonl
            return parse_log_vitest
        if "jest" in test_cmd:
            return parse_log_jest
        if "mocha" in test_cmd:
            from swebench.harness.log_parsers.junit_xml import specs_use_mocha_junit

            if specs_use_mocha_junit(specs.get("test_cmd")):
                return parse_log_javascript_jsonl
            from swebench.harness.log_parsers.javascript import parse_log_tap

            return parse_log_tap
        return parse_log_javascript_jsonl
    if lang == "ruby":
        from swebench.harness.log_parsers.ruby import parse_log_minitest

        return parse_log_minitest
    if lang == "rust":
        from swebench.harness.log_parsers.rust import parse_log_cargo

        return parse_log_cargo
    if lang == "php":
        from swebench.harness.log_parsers.php import parse_log_phpunit

        return parse_log_phpunit
    return parse_log_pytest


def _register_parser_in_language_map(repo: str, language: str, parser) -> None:
    lang = str(language or "").strip().lower()
    target = {
        "python": MAP_REPO_TO_PARSER_PY,
        "javascript": MAP_REPO_TO_PARSER_JS,
        "java": MAP_REPO_TO_PARSER_JAVA,
        "go": MAP_REPO_TO_PARSER_GO,
        "c": MAP_REPO_TO_PARSER_C,
        "php": MAP_REPO_TO_PARSER_PHP,
        "ruby": MAP_REPO_TO_PARSER_RUBY,
        "rust": MAP_REPO_TO_PARSER_RUST,
    }.get(lang)
    if target is not None:
        target[repo] = parser


def register_task_harness_specs(
    repo: str,
    version: str,
    install_config: dict[str, Any],
    language: str,
    *,
    override: bool = True,
) -> dict[str, Any]:
    """Register one repo/version into harness maps (mutates module-level dicts)."""
    repo = normalize_repo(repo)
    version = str(version or "0.0")
    specs = install_config_to_harness_specs(install_config, language=language)
    ext = language_to_harness_ext(language)

    repo_specs = MAP_REPO_VERSION_TO_SPECS.setdefault(repo, {})
    if not override and version in repo_specs:
        return repo_specs[version]
    repo_specs[version] = specs
    MAP_REPO_TO_EXT[repo] = ext

    reqs = install_config.get("reqs_path")
    if isinstance(reqs, list) and reqs:
        MAP_REPO_TO_REQS_PATHS[repo] = [
            str(p) for p in reqs if isinstance(p, str) and p.strip()
        ]

    env_yml = install_config.get("env_yml_path")
    if isinstance(env_yml, list) and env_yml:
        MAP_REPO_TO_ENV_YML_PATHS[repo] = [
            str(p) for p in env_yml if isinstance(p, str) and p.strip()
        ]

    parser = default_log_parser(language, specs)
    if override or repo not in MAP_REPO_TO_PARSER:
        MAP_REPO_TO_PARSER[repo] = parser
        _register_parser_in_language_map(repo, language, parser)

    return specs


def register_harness_from_rows(
    rows: list[dict[str, Any]],
    *,
    override: bool = True,
) -> RegisterReport:
    """Register all rows that contain ``install_config``."""
    report = RegisterReport(rows_seen=len(rows))
    seen_parsers: set[str] = set()

    for row in rows:
        install_config = parse_install_config(row.get("install_config"))
        if not install_config:
            iid = row.get("instance_id", "?")
            report.skipped.append(str(iid))
            continue

        repo = normalize_repo(str(row.get("repo") or ""))
        version = str(row.get("version") or "0.0")
        language = str(row.get("language") or install_config.get("language") or "python")

        had_parser = repo in MAP_REPO_TO_PARSER
        register_task_harness_specs(
            repo, version, install_config, language, override=override
        )
        report.registered.append(f"{repo}@{version} ({language})")
        if not had_parser and repo not in seen_parsers:
            seen_parsers.add(repo)
            report.parsers_added.append(repo)

    return report


def register_harness_from_jsonl(
    path: str | Path,
    *,
    override: bool = True,
) -> RegisterReport:
    """Load JSONL and register harness specs for every row with ``install_config``."""
    path = Path(path)
    rows = load_jsonl(path)
    report = register_harness_from_rows(rows, override=override)
    report.jsonl_path = str(path.resolve())
    return report
