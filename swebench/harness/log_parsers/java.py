import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from swebench.harness.constants import MAP_REPO_VERSION_TO_SPECS, TestStatus
from swebench.harness.test_spec.test_spec import TestSpec


def parse_log_maven(log: str, test_spec: TestSpec) -> dict[str, str]:
    """
    Parser for test logs generated with 'mvn test'.
    Annoyingly maven will not print the tests that have succeeded. For this log
    parser to work, each test must be run individually, and then we look for
    BUILD (SUCCESS|FAILURE) in the logs.

    Handles race conditions where multiple test commands appear before their
    BUILD results due to concurrent output from shell tracing and Maven.

    Args:
        log (str): log content
    Returns:
        dict: test case to test status mapping
    """
    test_status_map = {}
    pending_tests: list[str] = []
    unmatched_results: list[str] = []

    # Get the test name from the command used to execute the test.
    # Assumes we run evaluation with set -x
    test_name_pattern = r"^.*-Dtest=(\S+).*$"
    result_pattern = r"^.*BUILD (SUCCESS|FAILURE)$"

    for line in log.split("\n"):
        test_name_match = re.match(test_name_pattern, line.strip())
        if test_name_match:
            pending_tests.append(test_name_match.groups()[0])

        result_match = re.match(result_pattern, line.strip())
        if result_match:
            status = result_match.groups()[0]
            if pending_tests:
                test_name = pending_tests.pop(0)
                if status == "SUCCESS":
                    test_status_map[test_name] = TestStatus.PASSED.value
                elif status == "FAILURE":
                    test_status_map[test_name] = TestStatus.FAILED.value
            else:
                # Track unmatched results for later matching
                unmatched_results.append(status)

    # Match any remaining pending tests with unmatched results (FIFO order)
    # This handles cases where BUILD results appear after other output
    while pending_tests and unmatched_results:
        test_name = pending_tests.pop(0)
        status = unmatched_results.pop(0)
        if status == "SUCCESS":
            test_status_map[test_name] = TestStatus.PASSED.value
        elif status == "FAILURE":
            test_status_map[test_name] = TestStatus.FAILED.value

    # Warn if there are still pending tests without results
    if pending_tests:
        print(
            f"[WARNING] Maven log parser: {len(pending_tests)} test(s) had no BUILD result: "
            f"{pending_tests}"
        )

    return test_status_map


def _junit_case_test_key(case: ET.Element) -> str:
    """SWE-rebench / bulk-Maven key: ``fqcn > methodName``."""
    name = (case.attrib.get("name") or "").strip()
    classname = (case.attrib.get("classname") or "").strip()
    if classname:
        return f"{classname} > {name}" if name else classname
    return name or classname


def _junit_case_to_test_status(case: ET.Element) -> str:
    for child in case:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "failure":
            return TestStatus.FAILED.value
        if tag == "error":
            return TestStatus.ERROR.value
        if tag == "skipped":
            return TestStatus.SKIPPED.value
    return TestStatus.PASSED.value


def _parse_maven_surefire_xml(path: Path) -> dict[str, str]:
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return {}
    root = tree.getroot()
    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if tag == "testsuites":
        suites = list(root)
    else:
        suites = [root]

    out: dict[str, str] = {}
    for suite in suites:
        st = suite.tag.split("}")[-1] if "}" in suite.tag else suite.tag
        if st != "testsuite":
            continue
        for case in suite:
            ct = case.tag.split("}")[-1] if "}" in case.tag else case.tag
            if ct != "testcase":
                continue
            key = _junit_case_test_key(case)
            if not key:
                continue
            out[key] = _junit_case_to_test_status(case)
    return out


def parse_maven_surefire_dirs(reports_root: Path) -> dict[str, str]:
    """
    Parse all ``TEST-*.xml`` files under ``reports_root`` (Maven Surefire layout).

    Keys use ``classname > method`` to match SWE-rebench JSONL ``FAIL_TO_PASS`` labels.
    """
    if not reports_root.is_dir():
        return {}
    out: dict[str, str] = {}
    for xml_path in sorted(reports_root.rglob("TEST-*.xml")):
        for key, status in _parse_maven_surefire_xml(xml_path).items():
            out[key] = status
    return out


def infer_maven_junit_roots_from_test_cmd(test_cmd: str | list[str] | None) -> list[str]:
    """Derive ``module/target/surefire-reports`` paths from ``mvn -pl a,b,c test``."""
    if isinstance(test_cmd, list):
        cmd_str = " ".join(str(c) for c in test_cmd)
    else:
        cmd_str = str(test_cmd or "")
    match = re.search(r"-pl\s+([^\s]+)", cmd_str)
    if not match:
        return ["target/surefire-reports"]
    modules = [m.strip() for m in match.group(1).split(",") if m.strip()]
    if not modules:
        return ["target/surefire-reports"]
    return [f"{mod}/target/surefire-reports" for mod in modules]


def get_maven_junit_roots(test_spec: TestSpec) -> list[str]:
    specs = MAP_REPO_VERSION_TO_SPECS.get(test_spec.repo, {}).get(test_spec.version, {})
    roots = specs.get("maven_junit_roots")
    if isinstance(roots, list) and roots:
        return [str(r).strip().strip("/") for r in roots if str(r).strip()]
    return infer_maven_junit_roots_from_test_cmd(specs.get("test_cmd"))


def _test_cmd_str(test_cmd: Any) -> str:
    if isinstance(test_cmd, list):
        return " ".join(str(c) for c in test_cmd)
    return str(test_cmd or "")


def should_use_maven_surefire(
    specs: dict[str, Any], surefire_reports_dir: Path | None = None
) -> bool:
    """
    Use Surefire XML when explicitly configured, or for bulk ``mvn -pl … test`` runs
    (not per-test ``-Dtest=`` invocations).
    """
    if specs.get("maven_junit_roots"):
        return True
    cmd_str = _test_cmd_str(specs.get("test_cmd"))
    if "-pl " in cmd_str and cmd_str.count("-Dtest=") <= 1:
        return True
    if surefire_reports_dir and surefire_reports_dir.is_dir():
        return any(surefire_reports_dir.rglob("TEST-*.xml"))
    return False


def parse_log_ant(log: str, test_spec: TestSpec) -> dict[str, str]:
    test_status_map = {}

    pattern = r"^\s*\[junit\]\s+\[(PASS|FAIL|ERR)\]\s+(.*)$"

    for line in log.split("\n"):
        match = re.match(pattern, line.strip())
        if match:
            status, test_name = match.groups()
            if status == "PASS":
                test_status_map[test_name] = TestStatus.PASSED.value
            elif status in ["FAIL", "ERR"]:
                test_status_map[test_name] = TestStatus.FAILED.value

    return test_status_map


def parse_log_gradle_custom(log: str, test_spec: TestSpec) -> dict[str, str]:
    """
    Parser for test logs generated with 'gradle test'. Assumes that the
    pre-install script to update the gradle config has run.

    Handles race conditions where test name and status appear on different lines
    due to interleaved log output from concurrent processes.
    """
    test_status_map = {}

    # Pattern for normal case: test name and status on the same line
    # e.g., "com.example.Test > testMethod PASSED"
    # [^>] ensures we don't match lines starting with > (shell prompts, etc.)
    full_pattern = r"^([^>].+)\s+(PASSED|FAILED)$"

    # Pattern for test name without status (race condition case)
    # e.g., "com.example.Test > testMethod" followed by warnings, then "PASSED"
    # Must also start with [^>] for consistency
    test_name_pattern = r"^([^>]\S*\s+>\s+\S+)$"

    # Pattern for standalone status line
    status_only_pattern = r"^(PASSED|FAILED)$"

    pending_test_name = None

    for line in log.split("\n"):
        stripped = line.strip()

        # Check for full match (test name + status on same line)
        match = re.match(full_pattern, stripped)
        if match:
            test_name, status = match.groups()
            if status == "PASSED":
                test_status_map[test_name] = TestStatus.PASSED.value
            elif status == "FAILED":
                test_status_map[test_name] = TestStatus.FAILED.value
            pending_test_name = None
            continue

        # Check for test name without status
        test_name_match = re.match(test_name_pattern, stripped)
        if test_name_match:
            pending_test_name = test_name_match.group(1)
            continue

        # Check for standalone status (applies to pending test name)
        if pending_test_name:
            status_match = re.match(status_only_pattern, stripped)
            if status_match:
                status = status_match.group(1)
                if status == "PASSED":
                    test_status_map[pending_test_name] = TestStatus.PASSED.value
                elif status == "FAILED":
                    test_status_map[pending_test_name] = TestStatus.FAILED.value
                pending_test_name = None

    # Warn if there's a pending test without a result
    if pending_test_name:
        print(
            f"[WARNING] Gradle log parser: test had no status result: {pending_test_name}"
        )

    return test_status_map


MAP_REPO_TO_PARSER_JAVA = {
    "google/gson": parse_log_maven,
    "apache/druid": parse_log_maven,
    "javaparser/javaparser": parse_log_maven,
    "projectlombok/lombok": parse_log_ant,
    "apache/lucene": parse_log_gradle_custom,
    "reactivex/rxjava": parse_log_gradle_custom,
}
