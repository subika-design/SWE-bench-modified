"""Tests for Vitest JUnit grading (rubric JSONL / axios)."""

import xml.etree.ElementTree as ET
from pathlib import Path

from swebench.harness.constants import TestStatus
from swebench.harness.log_parsers import MAP_REPO_TO_PARSER
from swebench.harness.jsonl_register import (
    default_log_parser,
    register_task_harness_specs,
)
from swebench.harness.log_parsers.javascript import parse_log_javascript_jsonl
from swebench.harness.log_parsers.junit_xml import (
    junit_case_to_nodeid,
    parse_junit_xml_file,
    should_use_vitest_junit_xml,
)
from swebench.harness.grading import get_logs_eval
from swebench.harness.test_spec.test_spec import TestSpec


def test_vitest_junit_nodeid_classname_only_no_file_attr(tmp_path: Path):
    """Vitest JUnit: classname is the file path; no ``file`` attribute on testcase."""
    case = ET.Element(
        "testcase",
        {
            "name": (
                "Prototype Pollution Protection > resolveConfig params and "
                "paramsSerializer gadget > should not inherit polluted params via resolveConfig"
            ),
            "classname": "tests/unit/prototypePollution.test.js",
        },
    )
    nid = junit_case_to_nodeid(case, tmp_path / "repo")
    assert nid == (
        "tests/unit/prototypePollution.test.js::Prototype Pollution Protection > "
        "resolveConfig params and paramsSerializer gadget > "
        "should not inherit polluted params via resolveConfig"
    )


def test_vitest_junit_nodeid_file_and_suite_name(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    test_file = repo / "tests/unit/foo.test.js"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("// test\n", encoding="utf-8")

    case = ET.Element(
        "testcase",
        {
            "name": "outer > inner > should work",
            "classname": "tests/unit/foo.test.js",
            "file": str(test_file),
        },
    )
    nid = junit_case_to_nodeid(case, repo)
    assert nid == "tests/unit/foo.test.js::outer > inner > should work"


def test_parse_junit_xml_file_outcomes(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    xml = tmp_path / "out.xml"
    xml.write_text(
        '<?xml version="1.0"?>'
        '<testsuites><testsuite>'
        '<testcase name="t1" classname="tests/a.test.js" file="tests/a.test.js"/>'
        '<testcase name="t2" classname="tests/a.test.js" file="tests/a.test.js">'
        "<failure/></testcase>"
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    (repo / "tests").mkdir()
    (repo / "tests/a.test.js").write_text("", encoding="utf-8")

    status = parse_junit_xml_file(xml, repo)
    assert status["tests/a.test.js::t1"] == TestStatus.PASSED.value
    assert status["tests/a.test.js::t2"] == TestStatus.FAILED.value


def test_should_use_junit_xml_file():
    from swebench.harness.log_parsers.junit_xml import should_use_junit_xml_file

    assert should_use_junit_xml_file(
        {"test_cmd": "cd /testbed && npx vitest run --reporter=junit --outputFile=__JUNIT_OUT__ x.js"}
    )
    assert should_use_junit_xml_file(
        {
            "test_cmd": (
                "cd /testbed && npx jest --ci --reporters=jest-junit "
                "--outputFile=__JUNIT_OUT__ t.js"
            )
        }
    )
    assert not should_use_junit_xml_file({"test_cmd": "npx mocha -R tap"})


def test_should_use_vitest_junit_xml():
    assert should_use_vitest_junit_xml(
        {"test_cmd": "cd /testbed && npx vitest run --reporter=junit --outputFile=__JUNIT_OUT__ x.js"}
    )
    assert not should_use_vitest_junit_xml({"test_cmd": "npx mocha -R tap"})


def test_jsonl_register_overrides_axios_parser():
    repo = "axios/axios"
    register_task_harness_specs(
        repo,
        "10922",
        {
            "install": "npm ci",
            "test_cmd": "cd /testbed && npx vitest run --reporter=junit --outputFile=__JUNIT_OUT__ t.js",
        },
        "javascript",
        override=True,
    )
    from swebench.harness.log_parsers.javascript import parse_log_javascript_jsonl

    assert MAP_REPO_TO_PARSER[repo] is parse_log_javascript_jsonl


def test_default_log_parser_javascript_vitest():
    parser = default_log_parser(
        "javascript",
        {"test_cmd": "npx vitest run --reporter=junit --outputFile=__JUNIT_OUT__"},
    )
    assert parser.__name__ == "parse_log_javascript_jsonl"


def test_parse_real_vitest_junit_prototype_pollution_keys():
    xml = Path(
        "/Users/subika/Downloads/swe-bench-taskgen/SWE-bench-modified/logs/run_evaluation/"
        "rubric_gold/gold/axios__axios-10922/vitest-junit.xml"
    )
    if not xml.is_file():
        return
    exp = (
        "tests/unit/prototypePollution.test.js::Prototype Pollution Protection > "
        "resolveConfig params and paramsSerializer gadget > "
        "should not inherit polluted params via resolveConfig"
    )
    m = parse_junit_xml_file(xml, Path("/testbed"))
    assert exp in m
    assert m[exp] == TestStatus.PASSED.value


def test_get_logs_eval_reads_jest_junit_reports_dir(tmp_path: Path):
    register_task_harness_specs(
        "isomorphic-git/isomorphic-git",
        "2313",
        {
            "test_cmd": (
                "cd /testbed && npx jest --ci --forceExit --reporters=default "
                "--reporters=jest-junit --outputFile=__JUNIT_OUT__ __tests__/t.js"
            ),
        },
        "javascript",
        override=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "__tests__").mkdir()
    (repo / "__tests__" / "t.js").write_text("", encoding="utf-8")

    reports = tmp_path / "jest-junit-reports" / "junit"
    reports.mkdir(parents=True)
    (reports / "results.xml").write_text(
        '<?xml version="1.0"?><testsuites><testsuite>'
        '<testcase name="walk can run" classname="__tests__/t.js" '
        'file="__tests__/t.js"/>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    log = tmp_path / "test_output.txt"
    log.write_text(
        ">>>>> Start Test Output\nPASS __tests__/t.js\n>>>>> End Test Output\n",
        encoding="utf-8",
    )
    spec = TestSpec(
        instance_id="isomorphic-git__isomorphic-git-2313",
        repo="isomorphic-git/isomorphic-git",
        version="2313",
        env_script_list=[],
        repo_script_list=[],
        eval_script_list=[],
        arch="x86_64",
        FAIL_TO_PASS=[],
        PASS_TO_PASS=[],
        language="js",
        docker_specs={},
        namespace=None,
    )
    status_map, ok = get_logs_eval(spec, str(log))
    assert ok
    assert status_map["__tests__/t.js::walk can run"] == TestStatus.PASSED.value


def test_get_logs_eval_reads_jest_junit_file(tmp_path: Path):
    register_task_harness_specs(
        "isomorphic-git/isomorphic-git",
        "2313",
        {
            "test_cmd": (
                "cd /testbed && npx jest --ci --forceExit --reporters=default "
                "--reporters=jest-junit --outputFile=__JUNIT_OUT__ __tests__/t.js"
            ),
        },
        "javascript",
        override=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "__tests__").mkdir()
    (repo / "__tests__" / "t.js").write_text("", encoding="utf-8")

    junit_xml = tmp_path / "vitest-junit.xml"
    junit_xml.write_text(
        '<?xml version="1.0"?><testsuites><testsuite>'
        '<testcase name="walk can run" classname="__tests__/t.js" '
        'file="__tests__/t.js"/>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    log = tmp_path / "test_output.txt"
    log.write_text(
        ">>>>> Start Test Output\n"
        "PASS __tests__/t.js\n"
        ">>>>> End Test Output\n",
        encoding="utf-8",
    )
    spec = TestSpec(
        instance_id="isomorphic-git__isomorphic-git-2313",
        repo="isomorphic-git/isomorphic-git",
        version="2313",
        env_script_list=[],
        repo_script_list=[],
        eval_script_list=[],
        arch="x86_64",
        FAIL_TO_PASS=[],
        PASS_TO_PASS=[],
        language="js",
        docker_specs={},
        namespace=None,
    )
    status_map, ok = get_logs_eval(spec, str(log))
    assert ok
    assert status_map["__tests__/t.js::walk can run"] == TestStatus.PASSED.value


def test_get_logs_eval_reads_vitest_junit_file(tmp_path: Path):
    register_task_harness_specs(
        "axios/axios",
        "10922",
        {
            "test_cmd": (
                "cd /testbed && npx vitest run --reporter=junit "
                "--outputFile=__JUNIT_OUT__ tests/foo.test.js"
            ),
        },
        "javascript",
        override=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tests").mkdir()
    (repo / "tests/foo.test.js").write_text("", encoding="utf-8")

    junit_xml = tmp_path / "vitest-junit.xml"
    junit_xml.write_text(
        '<?xml version="1.0"?><testsuites><testsuite>'
        '<testcase name="case" classname="tests/foo.test.js" file="tests/foo.test.js"/>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    log = tmp_path / "test_output.txt"
    log.write_text(
        ">>>>> Start Test Output\n"
        "JUNIT report written to /testbed/__JUNIT_OUT__\n"
        ">>>>> End Test Output\n",
        encoding="utf-8",
    )
    spec = TestSpec(
        instance_id="axios__axios-10922",
        repo="axios/axios",
        version="10922",
        env_script_list=[],
        repo_script_list=[],
        eval_script_list=[],
        arch="x86_64",
        FAIL_TO_PASS=[],
        PASS_TO_PASS=[],
        language="js",
        docker_specs={},
        namespace=None,
    )
    status_map, ok = get_logs_eval(spec, str(log))
    assert ok
    assert status_map["tests/foo.test.js::case"] == TestStatus.PASSED.value
