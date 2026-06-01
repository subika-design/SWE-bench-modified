from pathlib import Path

from swebench.harness.log_parsers.java import (
    infer_maven_junit_roots_from_test_cmd,
    parse_log_gradle_custom,
    parse_log_maven,
    parse_maven_surefire_dirs,
    should_use_maven_surefire,
)
from swebench.harness.constants import TestStatus


class TestParseLogGradleCustom:
    """Tests for parse_log_gradle_custom used by Apache Lucene and RxJava."""

    def test_parse_pass_and_fail(self):
        """Test parsing normal output with passing and failing tests."""
        log = """com.example.Test > testOne PASSED
com.example.Test > testTwo FAILED
"""
        result = parse_log_gradle_custom(log, test_spec=None)

        assert len(result) == 2
        assert result["com.example.Test > testOne"] == TestStatus.PASSED.value
        assert result["com.example.Test > testTwo"] == TestStatus.FAILED.value

    def test_ignores_non_test_lines(self):
        """Test that task lines and other noise are ignored."""
        log = """> Task :test
WARNING: Some warning
com.example.Test > testMethod PASSED
BUILD SUCCESSFUL
"""
        result = parse_log_gradle_custom(log, test_spec=None)

        assert result == {"com.example.Test > testMethod": TestStatus.PASSED.value}

    def test_interleaved_logs_race_condition(self):
        """Test parsing when warnings split test name from status."""
        log = """com.example.Test > testOne PASSED
com.example.Test > testTwo
WARNING: interleaved output
PASSED
com.example.Test > testThree
FAILED
"""
        result = parse_log_gradle_custom(log, test_spec=None)

        assert len(result) == 3
        assert result["com.example.Test > testOne"] == TestStatus.PASSED.value
        assert result["com.example.Test > testTwo"] == TestStatus.PASSED.value
        assert result["com.example.Test > testThree"] == TestStatus.FAILED.value


class TestParseLogMaven:
    """Tests for parse_log_maven used by Gson, Druid, JavaParser."""

    def test_parse_sequential_output(self):
        """Test parsing when commands and results are sequential."""
        log = """+ mvnd test -B -Dtest=com.example.Test#testOne
[INFO] BUILD SUCCESS
+ mvnd test -B -Dtest=com.example.Test#testTwo
[INFO] BUILD FAILURE
"""
        result = parse_log_maven(log, test_spec=None)

        assert len(result) == 2
        assert result["com.example.Test#testOne"] == TestStatus.PASSED.value
        assert result["com.example.Test#testTwo"] == TestStatus.FAILED.value

    def test_interleaved_commands_race_condition(self):
        """Test parsing when multiple commands appear before their results."""
        log = """+ mvnd test -B -Dtest=com.example.Test#testOne
+ mvnd test -B -Dtest=com.example.Test#testTwo
[INFO] BUILD SUCCESS
[INFO] BUILD SUCCESS
+ mvnd test -B -Dtest=com.example.Test#testThree
[INFO] BUILD FAILURE
"""
        result = parse_log_maven(log, test_spec=None)

        assert len(result) == 3
        assert result["com.example.Test#testOne"] == TestStatus.PASSED.value
        assert result["com.example.Test#testTwo"] == TestStatus.PASSED.value
        assert result["com.example.Test#testThree"] == TestStatus.FAILED.value

    def test_delayed_build_result_after_marker(self):
        """Test when BUILD SUCCESS appears after end marker due to buffering."""
        log = """+ mvnd test -B -Dtest=com.example.Test#testOne
[INFO] BUILD SUCCESS
+ mvnd test -B -Dtest=com.example.Test#testTwo
+ : '>>>>> End Test Output'
+ git checkout somefile.java
[INFO] BUILD SUCCESS
"""
        result = parse_log_maven(log, test_spec=None)

        assert len(result) == 2
        assert result["com.example.Test#testOne"] == TestStatus.PASSED.value
        assert result["com.example.Test#testTwo"] == TestStatus.PASSED.value


class TestParseMavenSurefire:
    SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="com.google.gson.VersionExclusionStrategyTest" tests="2" failures="1" errors="0" skipped="0">
  <testcase name="testPasses" classname="com.google.gson.VersionExclusionStrategyTest" time="0.001"/>
  <testcase name="testFails" classname="com.google.gson.VersionExclusionStrategyTest" time="0.002">
    <failure message="expected true">stack trace</failure>
  </testcase>
</testsuite>
"""

    def test_parse_surefire_xml_file(self, tmp_path: Path):
        xml_path = tmp_path / "TEST-com.google.gson.VersionExclusionStrategyTest.xml"
        xml_path.write_text(self.SAMPLE_XML, encoding="utf-8")
        result = parse_maven_surefire_dirs(tmp_path)
        assert result["com.google.gson.VersionExclusionStrategyTest > testPasses"] == (
            TestStatus.PASSED.value
        )
        assert result["com.google.gson.VersionExclusionStrategyTest > testFails"] == (
            TestStatus.FAILED.value
        )

    def test_infer_junit_roots_from_pl(self):
        cmd = "mvn -q test -pl gson,test-shrinker -am"
        assert infer_maven_junit_roots_from_test_cmd(cmd) == [
            "gson/target/surefire-reports",
            "test-shrinker/target/surefire-reports",
        ]

    def test_should_use_surefire_for_bulk_mvn(self):
        specs = {"test_cmd": "mvn test -pl gson -am"}
        assert should_use_maven_surefire(specs) is True
        specs_per_test = {
            "test_cmd": [
                "mvnd test -Dtest=com.foo.Bar#m1",
                "mvnd test -Dtest=com.foo.Bar#m2",
            ]
        }
        assert should_use_maven_surefire(specs_per_test) is False
