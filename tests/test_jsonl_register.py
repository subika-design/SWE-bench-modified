"""Tests for JSONL harness registration."""

import json
from pathlib import Path

import pytest

from swebench.harness.constants import (
    MAP_REPO_TO_EXT,
    MAP_REPO_VERSION_TO_SPECS,
)
from swebench.harness.jsonl_register import (
    install_config_to_harness_specs,
    register_harness_from_rows,
    register_task_harness_specs,
)
from swebench.harness.log_parsers import MAP_REPO_TO_PARSER


@pytest.fixture
def sample_row():
    return {
        "instance_id": "django__django-99999",
        "repo": "django/django",
        "version": "1.7",
        "language": "python",
        "base_commit": "abc123",
        "test_patch": "",
        "FAIL_TO_PASS": "[]",
        "PASS_TO_PASS": "[]",
        "install_config": {
            "python": "3.12",
            "install": "pip install -e .",
            "test_cmd": "./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1",
            "pre_install": ["apt-get update"],
            "pip_packages": ["pip", "wheel"],
            "reqs_path": ["tests/requirements/py3.txt"],
            "eval_commands": ["export LANG=en_US.UTF-8"],
        },
    }


from swebench.harness.test_spec.utils import flatten_commands


def test_flatten_commands_nested_list():
    assert flatten_commands(
        ["cmd1", ["cmd2", "cmd3"], [["cmd4"]]]
    ) == ["cmd1", "cmd2", "cmd3", "cmd4"]


def test_flatten_commands_string():
    assert flatten_commands("pip install -e .") == ["pip install -e ."]


def test_install_config_to_harness_specs_normalizes_install(sample_row):
    specs = install_config_to_harness_specs(
        sample_row["install_config"], language="python"
    )
    assert specs["install"] == ["pip install -e ."]
    assert specs["test_cmd"].startswith("./tests/runtests.py")


def test_register_task_harness_specs(sample_row):
    repo = "testorg/testrepo"
    register_task_harness_specs(
        repo,
        "0.1",
        sample_row["install_config"],
        "python",
    )
    assert MAP_REPO_TO_EXT[repo] == "py"
    assert "0.1" in MAP_REPO_VERSION_TO_SPECS[repo]
    assert repo in MAP_REPO_TO_PARSER


def test_register_harness_from_rows(sample_row):
    report = register_harness_from_rows([sample_row])
    assert report.rows_seen == 1
    assert len(report.registered) == 1
    assert "django/django@1.7" in report.registered[0]
    assert MAP_REPO_TO_EXT["django/django"] == "py"
    assert "1.7" in MAP_REPO_VERSION_TO_SPECS["django/django"]


def test_make_test_spec_after_registration(sample_row):
    register_harness_from_rows([sample_row])
    specs = MAP_REPO_VERSION_TO_SPECS["django/django"]["1.7"]
    assert specs["install"] == ["pip install -e ."]
    assert MAP_REPO_TO_EXT["django/django"] == "py"
    assert "django/django" in MAP_REPO_TO_PARSER


def test_make_repo_script_list_py_flattens_install_list():
    import json
    from pathlib import Path
    from swebench.harness.jsonl_register import install_config_to_harness_specs
    from swebench.harness.test_spec.python import make_repo_script_list_py

    row = json.loads(
        Path("/Users/subika/Downloads/swe-bench-taskgen/output/pandas_python_16tasks.jsonl")
        .read_text()
        .splitlines()[0]
    )
    specs = install_config_to_harness_specs(row["install_config"], language="python")
    scripts = make_repo_script_list_py(
        specs, "pandas-dev/pandas", "/testbed", row["base_commit"], "testbed"
    )
    assert all(isinstance(s, str) for s in scripts)
    assert any("pip install" in s for s in scripts)


def test_make_eval_script_list_py_flattens_install_list():
    import json
    from pathlib import Path
    from swebench.harness.jsonl_register import install_config_to_harness_specs
    from swebench.harness.test_spec.python import make_eval_script_list_py

    row = json.loads(
        Path("/Users/subika/Downloads/swe-bench-taskgen/output/pandas_python_16tasks.jsonl")
        .read_text()
        .splitlines()[0]
    )
    specs = install_config_to_harness_specs(row["install_config"], language="python")
    from swebench.harness.constants import MAP_REPO_VERSION_TO_SPECS

    MAP_REPO_VERSION_TO_SPECS.setdefault(row["repo"], {})[row["version"]] = specs
    scripts = make_eval_script_list_py(
        row, specs, "testbed", "/testbed", row["base_commit"], row["test_patch"]
    )
    assert all(isinstance(s, str) for s in scripts)


def test_register_from_real_jsonl(tmp_path):
    jsonl = tmp_path / "tasks.jsonl"
    row = {
        "instance_id": "pandas-dev__pandas-1",
        "repo": "pandas-dev/pandas",
        "version": "3.1",
        "language": "python",
        "base_commit": "deadbeef",
        "test_patch": "",
        "FAIL_TO_PASS": "[]",
        "PASS_TO_PASS": "[]",
        "install_config": {
            "python": "3.11",
            "install": ["pip install -e ."],
            "test_cmd": "pytest -rA",
        },
    }
    jsonl.write_text(json.dumps(row) + "\n", encoding="utf-8")
    from swebench.harness.jsonl_register import register_harness_from_jsonl

    report = register_harness_from_jsonl(jsonl)
    assert report.rows_seen == 1
    assert MAP_REPO_TO_EXT["pandas-dev/pandas"] == "py"
