"""Tests for RSpec JUnit grading and eval_commands in common eval scripts."""

import xml.etree.ElementTree as ET
from pathlib import Path

from swebench.harness.constants import TestStatus
from swebench.harness.grading import get_logs_eval
from swebench.harness.jsonl_register import default_log_parser, register_task_harness_specs
from swebench.harness.log_parsers.junit_xml import (
    junit_case_to_rspec_nodeid,
    parse_junit_xml_file,
    should_use_junit_xml_file,
    specs_use_rspec_junit,
)
from swebench.harness.test_key_normalization import resolve_test_status
from swebench.harness.test_spec.test_spec import TestSpec
from swebench.harness.test_spec.utils import make_eval_script_list_common


def test_specs_use_rspec_junit():
    cmd = (
        "bundle exec rspec spec/foo_spec.rb "
        "--format RspecJunitFormatter --out __JUNIT_OUT__"
    )
    assert specs_use_rspec_junit(cmd)
    assert not specs_use_rspec_junit("bundle exec rspec spec/foo_spec.rb")


def test_rspec_junit_nodeid_rspec_junit_formatter_file_on_testcase(tmp_path: Path):
    """Regression: rspec_junit_formatter puts dotted path in classname, full label in name."""
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = repo / "spec" / "rubocop" / "version_spec.rb"
    spec.parent.mkdir(parents=True)
    spec.write_text("RSpec.describe 'x' do\nend\n", encoding="utf-8")

    case = ET.Element(
        "testcase",
        {
            "classname": "spec.rubocop.version_spec",
            "name": (
                "RuboCop::Version.rubydex_indicator when UseProjectIndex is false "
                'is expected to eq ""'
            ),
            "file": "./spec/rubocop/version_spec.rb",
            "time": "0.04",
        },
    )
    nid = junit_case_to_rspec_nodeid(case, repo)
    assert nid == (
        "spec/rubocop/version_spec.rb::RuboCop::Version.rubydex_indicator "
        'when UseProjectIndex is false is expected to eq ""'
    )


def test_resolve_rubric_key_rspec_junit_formatter_shape(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = repo / "spec" / "rubocop" / "config_obsoletion_spec.rb"
    spec.parent.mkdir(parents=True)
    spec.write_text("", encoding="utf-8")
    xml = tmp_path / "junit.xml"
    xml.write_text(
        """<?xml version="1.0"?>
<testsuite name="rspec" tests="1">
  <testcase classname="spec.rubocop.config_obsoletion_spec"
            name="RuboCop::ConfigObsoletion#validate when the configuration includes parameters renamed for consistency prints a warning message and does not raise"
            file="./spec/rubocop/config_obsoletion_spec.rb"
            time="0.01"/>
</testsuite>""",
        encoding="utf-8",
    )
    specs = {
        "test_cmd": (
            "bundle exec rspec spec/rubocop/config_obsoletion_spec.rb "
            "--format RspecJunitFormatter --out __JUNIT_OUT__"
        )
    }
    status_map = parse_junit_xml_file(xml, repo, specs=specs)
    rubric_key = (
        "spec/rubocop/config_obsoletion_spec.rb::RuboCop::ConfigObsoletion#validate "
        "when the configuration includes parameters renamed for consistency "
        "prints a warning message and does not raise"
    )
    assert resolve_test_status(rubric_key, status_map) == TestStatus.PASSED.value


def test_rspec_junit_nodeid_uses_spec_file(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = repo / "spec" / "rubocop" / "version_spec.rb"
    spec.parent.mkdir(parents=True)
    spec.write_text("RSpec.describe 'x' do\nend\n", encoding="utf-8")

    case = ET.Element(
        "testcase",
        {
            "classname": "RuboCop::Version.rubydex_indicator when UseProjectIndex is false",
            "name": 'is expected to eq ""',
            "time": "0.01",
        },
    )
    suite_file = str(spec)
    nid = junit_case_to_rspec_nodeid(case, repo, suite_file=suite_file)
    assert nid == (
        "spec/rubocop/version_spec.rb::RuboCop::Version.rubydex_indicator "
        'when UseProjectIndex is false is expected to eq ""'
    )


def test_parse_rspec_junit_xml_file(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = repo / "spec" / "rubocop" / "version_spec.rb"
    spec.parent.mkdir(parents=True)
    spec.write_text("", encoding="utf-8")
    xml = tmp_path / "junit.xml"
    xml.write_text(
        f"""<?xml version="1.0"?>
<testsuites>
  <testsuite name="RuboCop::Version" tests="1" file="{spec}">
    <testcase classname="RuboCop::Version.rubydex_indicator when UseProjectIndex is false"
              name='is expected to eq ""'
              time="0.01"/>
  </testsuite>
</testsuites>""",
        encoding="utf-8",
    )
    specs = {
        "test_cmd": (
            "bundle exec rspec spec/rubocop/version_spec.rb "
            "--format RspecJunitFormatter --out __JUNIT_OUT__"
        )
    }
    status = parse_junit_xml_file(xml, repo, specs=specs)
    assert len(status) == 1
    key = next(iter(status))
    assert key.startswith("spec/rubocop/version_spec.rb::")
    assert status[key] == TestStatus.PASSED.value


def test_resolve_rubric_key_with_leading_dot_slash(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = repo / "spec" / "rubocop" / "version_spec.rb"
    spec.parent.mkdir(parents=True)
    spec.write_text("", encoding="utf-8")
    xml = tmp_path / "junit.xml"
    xml.write_text(
        f"""<?xml version="1.0"?>
<testsuites>
  <testsuite name="RuboCop::Version" tests="1" file="{spec}">
    <testcase classname="RuboCop::Version.rubydex_indicator when UseProjectIndex is false"
              name='is expected to eq ""'
              time="0.01"/>
  </testsuite>
</testsuites>""",
        encoding="utf-8",
    )
    specs = {
        "test_cmd": (
            "bundle exec rspec --format RspecJunitFormatter --out __JUNIT_OUT__"
        )
    }
    status_map = parse_junit_xml_file(xml, repo, specs=specs)
    rubric_key = (
        "./spec/rubocop/version_spec.rb::RuboCop::Version.rubydex_indicator "
        'when UseProjectIndex is false is expected to eq ""'
    )
    assert resolve_test_status(rubric_key, status_map) == TestStatus.PASSED.value


def test_make_eval_script_list_common_includes_eval_commands():
    instance = {"repo": "rubocop/rubocop", "version": "15173"}
    specs = {
        "test_cmd": "bundle exec rspec spec/foo_spec.rb",
        "eval_commands": ["bundle check >/dev/null 2>&1 || bundle install"],
    }
    register_task_harness_specs(
        "rubocop/rubocop", "15173", specs, "ruby", override=True
    )
    cmds = make_eval_script_list_common(
        instance,
        specs,
        "testbed",
        "/testbed",
        "abc123",
        "diff --git a/spec/foo_spec.rb b/spec/foo_spec.rb\n",
    )
    assert "bundle check >/dev/null 2>&1 || bundle install" in cmds
    start = cmds.index(": '>>>>> Start Test Output'")
    assert cmds.index("bundle check >/dev/null 2>&1 || bundle install") < start


def test_should_use_junit_xml_file_for_rspec():
    specs = {
        "test_cmd": (
            "bundle exec rspec spec/foo_spec.rb "
            "--format RspecJunitFormatter --out __JUNIT_OUT__"
        )
    }
    assert should_use_junit_xml_file(specs)


def test_default_log_parser_ruby_rspec_junit():
    specs = {
        "test_cmd": (
            "bundle exec rspec --format RspecJunitFormatter --out __JUNIT_OUT__"
        )
    }
    parser = default_log_parser("ruby", specs)
    assert parser is not None


def test_get_logs_eval_prefers_rspec_junit_file(tmp_path: Path):
    register_task_harness_specs(
        "rubocop/rubocop",
        "15173",
        {
            "test_cmd": (
                "bundle exec rspec spec/foo_spec.rb "
                "--format RspecJunitFormatter --out __JUNIT_OUT__"
            ),
        },
        "ruby",
        override=True,
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    junit = log_dir / "__JUNIT_OUT__"
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = repo / "spec" / "foo_spec.rb"
    spec.parent.mkdir(parents=True)
    spec.write_text("", encoding="utf-8")
    junit.write_text(
        """<?xml version="1.0"?>
<testsuites>
  <testsuite name="Foo" tests="1" file="/testbed/spec/foo_spec.rb">
    <testcase classname="Foo" name="works" time="0.01"/>
  </testsuite>
</testsuites>""",
        encoding="utf-8",
    )
    log_fp = log_dir / "test_output.txt"
    log_fp.write_text(
        ">>>>> Start Test Output\n"
        "bundle exec rspec\n"
        ">>>>> End Test Output\n",
        encoding="utf-8",
    )
    test_spec = TestSpec(
        instance_id="rubocop__rubocop-15173",
        repo="rubocop/rubocop",
        version="15173",
        env_script_list=[],
        repo_script_list=[],
        eval_script_list=[],
        arch="x86_64",
        FAIL_TO_PASS=[],
        PASS_TO_PASS=[],
        language="ruby",
        docker_specs={},
        namespace=None,
    )
    status_map, ok = get_logs_eval(test_spec, str(log_fp))
    assert ok
    assert "spec/foo_spec.rb::Foo works" in status_map
