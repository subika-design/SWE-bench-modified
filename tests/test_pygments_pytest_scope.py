"""Pygments data-file pytest scoping in harness eval scripts."""

from __future__ import annotations

from swebench.harness.test_spec.python import (
    get_test_directives,
    make_eval_script_list_py,
    pytest_cmd_has_scoped_paths,
)

_PYGMENTS_TEST_PATCH = """diff --git a/tests/examplefiles/pddl/example-domain.pddl b/tests/examplefiles/pddl/example-domain.pddl
new file mode 100644
--- /dev/null
+++ b/tests/examplefiles/pddl/example-domain.pddl
@@ -0,0 +1 @@
+(define (domain example-domain))
diff --git a/tests/examplefiles/pddl/example-domain.pddl.output b/tests/examplefiles/pddl/example-domain.pddl.output
new file mode 100644
--- /dev/null
+++ b/tests/examplefiles/pddl/example-domain.pddl.output
@@ -0,0 +1 @@
+'x'
diff --git a/tests/examplefiles/pddl/example-problem.pddl b/tests/examplefiles/pddl/example-problem.pddl
new file mode 100644
--- /dev/null
+++ b/tests/examplefiles/pddl/example-problem.pddl
@@ -0,0 +1 @@
+(define (problem example-problem))
"""


def test_get_test_directives_filters_pygments_output_files():
    instance = {
        "repo": "pygments/pygments",
        "test_patch": _PYGMENTS_TEST_PATCH,
    }
    directives = get_test_directives(instance)
    assert "tests/examplefiles/pddl/example-domain.pddl" in directives
    assert "tests/examplefiles/pddl/example-problem.pddl" in directives
    assert "tests/examplefiles/pddl/example-domain.pddl.output" not in directives


def test_pytest_cmd_has_scoped_paths_pygments():
    cmd = (
        "pytest -rA tests/examplefiles/pddl/example-domain.pddl "
        "tests/examplefiles/pddl/example-problem.pddl"
    )
    assert pytest_cmd_has_scoped_paths(cmd)
    assert not pytest_cmd_has_scoped_paths("pytest -rA")


def test_make_eval_script_list_py_uses_scoped_cmd_without_directives():
    from swebench.harness.jsonl_register import register_task_harness_specs

    instance = {
        "repo": "pygments/pygments",
        "version": "2799",
        "base_commit": "abc123",
        "test_patch": _PYGMENTS_TEST_PATCH,
    }
    specs = {
        "install": "pip install -e .",
        "test_cmd": (
            "pytest -rA tests/examplefiles/pddl/example-domain.pddl "
            "tests/examplefiles/pddl/example-problem.pddl"
        ),
    }
    register_task_harness_specs("pygments/pygments", "2799", specs, "python", override=True)
    cmds = make_eval_script_list_py(
        instance, specs, "testbed", "/testbed", "abc123", _PYGMENTS_TEST_PATCH
    )
    start = cmds.index(": '>>>>> Start Test Output'")
    test_line = cmds[start + 1]
    assert "example-domain.pddl" in test_line
    assert "example-problem.pddl" in test_line
    assert ".output" not in test_line
    assert test_line.count("pytest -rA") == 1


def test_make_eval_script_list_py_unscoped_still_appends_filtered_directives():
    from swebench.harness.jsonl_register import register_task_harness_specs

    instance = {
        "repo": "pygments/pygments",
        "version": "2799",
        "base_commit": "abc123",
        "test_patch": _PYGMENTS_TEST_PATCH,
    }
    specs = {"install": "pip install -e .", "test_cmd": "pytest -rA"}
    register_task_harness_specs("pygments/pygments", "2799", specs, "python", override=True)
    cmds = make_eval_script_list_py(
        instance, specs, "testbed", "/testbed", "abc123", _PYGMENTS_TEST_PATCH
    )
    start = cmds.index(": '>>>>> Start Test Output'")
    test_line = cmds[start + 1]
    assert "example-domain.pddl" in test_line
    assert ".output" not in test_line
