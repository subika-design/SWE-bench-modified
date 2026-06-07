"""Normalize rubric / JUnit test nodeids for cross-repo grading."""

from __future__ import annotations

from swebench.harness.constants import TestStatus


def normalize_test_nodeid(nodeid: str) -> str:
    """
    Canonical form for comparing rubric labels to JUnit parser output.

    - Strip leading/trailing whitespace on each ``::`` segment (Jest often
      emits trailing spaces in ``name``).
    - Collapse duplicated file-path prefixes produced when rubric keys repeat
      ``classname`` even though Jest already puts the file path there, e.g.
      ``src/foo.test.js::src/foo.test.js::case`` → ``src/foo.test.js::case``.
    """
    if not nodeid:
        return nodeid
    parts = [p.strip() for p in nodeid.split("::")]
    parts = [p for p in parts if p]
    if parts:
        parts[0] = parts[0].replace("\\", "/").strip().lstrip("./")
    if len(parts) >= 3 and parts[0] == parts[1]:
        parts = [parts[0], *parts[2:]]
    return "::".join(parts)


def _nodeid_parts(nodeid: str) -> tuple[str, str]:
    norm = normalize_test_nodeid(nodeid)
    if "::" not in norm:
        return "", norm
    path, name = norm.split("::", 1)
    return path, name


def build_status_lookup(status_map: dict[str, str]) -> dict[str, str]:
    """Expand a raw JUnit status map with normalized alias keys."""
    lookup: dict[str, str] = {}
    for key, status in status_map.items():
        lookup[key] = status
        norm = normalize_test_nodeid(key)
        lookup[norm] = status
    return lookup


def resolve_test_status(
    rubric_key: str,
    status_map: dict[str, str],
    *,
    lookup: dict[str, str] | None = None,
) -> str | None:
    """
    Resolve rubric ``FAIL_TO_PASS`` / ``PASS_TO_PASS`` key against eval results.

    Matching order:
    1. Exact key
    2. Normalized key (whitespace + duplicate path prefix)
    3. Unique prefix match on the test-name segment (truncated rubric keys)
    """
    if rubric_key in status_map:
        return status_map[rubric_key]

    table = lookup if lookup is not None else build_status_lookup(status_map)
    norm_rubric = normalize_test_nodeid(rubric_key)
    if norm_rubric in table:
        return table[norm_rubric]

    rubric_path, rubric_name = _nodeid_parts(norm_rubric)
    if not rubric_name:
        return None

    prefix_candidates: list[str] = []
    for key, status in table.items():
        key_path, key_name = _nodeid_parts(key)
        if rubric_path and key_path != rubric_path:
            continue
        if key_name.startswith(rubric_name):
            prefix_candidates.append(status)

    if len(prefix_candidates) == 1:
        return prefix_candidates[0]

    return None


def test_passed_normalized(case: str, status_map: dict[str, str], lookup: dict[str, str]) -> bool:
    status = resolve_test_status(case, status_map, lookup=lookup)
    return status in (TestStatus.PASSED.value, TestStatus.XFAIL.value)


def test_failed_normalized(case: str, status_map: dict[str, str], lookup: dict[str, str]) -> bool:
    status = resolve_test_status(case, status_map, lookup=lookup)
    return status is None or status in (
        TestStatus.FAILED.value,
        TestStatus.ERROR.value,
    )
