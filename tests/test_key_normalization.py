"""Tests for rubric/JUnit test-key normalization and grading lookup."""

from swebench.harness.constants import TestStatus
from swebench.harness.grading import get_eval_tests_report
from swebench.harness.log_parsers.junit_xml import parse_junit_xml_file
from swebench.harness.run_evaluation import patch_apply_output_ok
from swebench.harness.test_key_normalization import (
    normalize_test_nodeid,
    resolve_test_status,
)


def test_normalize_collapses_duplicate_file_prefix():
    raw = (
        "src/realm/__tests__/realmReducer-test.js::"
        "src/realm/__tests__/realmReducer-test.js::"
        "realmReducer EVENT type `realm_user`, op `update` User is deactivated"
    )
    assert normalize_test_nodeid(raw) == (
        "src/realm/__tests__/realmReducer-test.js::"
        "realmReducer EVENT type `realm_user`, op `update` User is deactivated"
    )


def test_normalize_strips_whitespace_segments():
    assert (
        normalize_test_nodeid("src/foo.test.js::bar baz ")
        == "src/foo.test.js::bar baz"
    )
    assert (
        normalize_test_nodeid("src/storage/__tests__/storage-test.js:: `eg` round-trips")
        == "src/storage/__tests__/storage-test.js::`eg` round-trips"
    )


def test_resolve_duplicate_rubric_key_against_jest_junit(tmp_path):
    repo = tmp_path / "testbed"
    test_file = repo / "src/realm/__tests__/realmReducer-test.js"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("// test\n", encoding="utf-8")

    xml = tmp_path / "junit.xml"
    xml.write_text(
        '<?xml version="1.0"?><testsuites><testsuite>'
        '<testcase classname="src/realm/__tests__/realmReducer-test.js" '
        'name="realmReducer EVENT type `realm_user`, op `update` User is deactivated" '
        f'file="{test_file}"/>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    status_map = parse_junit_xml_file(xml, repo)
    rubric_key = (
        "src/realm/__tests__/realmReducer-test.js::"
        "src/realm/__tests__/realmReducer-test.js::"
        "realmReducer EVENT type `realm_user`, op `update` User is deactivated"
    )
    assert resolve_test_status(rubric_key, status_map) == TestStatus.PASSED.value


def test_resolve_trailing_space_in_jest_name(tmp_path):
    repo = tmp_path / "testbed"
    test_file = repo / "src/unread/__tests__/unreadModel-test.js"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("// test\n", encoding="utf-8")

    xml = tmp_path / "junit.xml"
    xml.write_text(
        '<?xml version="1.0"?><testsuites><testsuite>'
        '<testcase classname="src/unread/__tests__/unreadModel-test.js" '
        'name="stream substate REGISTER_COMPLETE received data from '
        '&quot;unread_msgs.streams&quot; key replaces the current state " '
        f'file="{test_file}"/>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    status_map = parse_junit_xml_file(xml, repo)
    rubric_key = (
        "src/unread/__tests__/unreadModel-test.js::"
        'stream substate REGISTER_COMPLETE received data from "unread_msgs.streams" '
        "key replaces the current state"
    )
    assert resolve_test_status(rubric_key, status_map) == TestStatus.PASSED.value


def test_get_eval_tests_report_uses_normalized_keys():
    eval_map = {
        "src/foo.test.js::case one": TestStatus.PASSED.value,
    }
    rubric = {
        "FAIL_TO_PASS": ["src/foo.test.js::src/foo.test.js::case one"],
        "PASS_TO_PASS": [],
    }
    report = get_eval_tests_report(eval_map, rubric)
    assert report["FAIL_TO_PASS"]["success"] == rubric["FAIL_TO_PASS"]
    assert report["FAIL_TO_PASS"]["failure"] == []


def test_inject_jest_test_timeout():
    from swebench.harness.test_spec.javascript import _inject_jest_test_timeout

    cmd = "cd /testbed && npx jest --ci __tests__/t.js"
    assert "--testTimeout=120000" in _inject_jest_test_timeout(cmd)
    assert _inject_jest_test_timeout(cmd + " --testTimeout=90000") == cmd + " --testTimeout=90000"


def test_patch_apply_output_ok_rejects_reverse():
    assert patch_apply_output_ok("Applied patch foo cleanly.")
    assert not patch_apply_output_ok(
        "Reversed (or previously applied) patch detected!  Assuming -R.\n"
        "patching file lib/foo.js"
    )
