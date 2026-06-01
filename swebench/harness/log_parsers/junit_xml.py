"""Parse JUnit XML into SWE-bench test status maps (Vitest/Jest rubric JSONL)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from swebench.harness.constants import TestStatus
from swebench.harness.test_spec.test_spec import TestSpec

_JS_TEST_EXTENSIONS = (
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
)


def _iter_junit_elements(parent: ET.Element, local_name: str):
    want = local_name
    for el in parent.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == want:
            yield el


def _junit_xml_roots(path: Path) -> tuple[ET.Element | None, list[ET.Element]]:
    if not path.is_file():
        return None, []
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return None, []
    root = tree.getroot()
    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if tag == "testsuites":
        return root, list(_iter_junit_elements(root, "testsuite"))
    if tag == "testsuite":
        return root, [root]
    return root, []


def _junit_file_to_repo_relpath(rel_s: str, repo_root: Path) -> str:
    s = rel_s.replace("\\", "/").strip()
    if not s:
        return s
    for marker in ("/w/repo/", "/testbed/"):
        pos = s.find(marker)
        if pos != -1:
            return s[pos + len(marker) :]
    root_ps = repo_root.resolve().as_posix().rstrip("/")
    if root_ps and s.startswith(root_ps + "/"):
        return s[len(root_ps) + 1 :]
    if s.startswith("/"):
        try:
            return Path(s).resolve().relative_to(repo_root.resolve()).as_posix()
        except (ValueError, OSError):
            pass
    return s.lstrip("/")


def _is_js_test_relpath(rel: str) -> bool:
    low = rel.lower()
    return any(low.endswith(ext) for ext in _JS_TEST_EXTENSIONS)


def _resolve_repo_test_path(repo_root: Path, rel: str) -> str | None:
    root = repo_root.resolve()
    rel = rel.replace("\\", "/").strip().lstrip("/")
    if not rel:
        return None
    if (root / rel).is_file():
        return rel
    if _is_js_test_relpath(rel) and (root / Path(rel).name).is_file():
        return Path(rel).name
    return None


def _rel_from_junit_classname(classname: str, repo_root: Path) -> str | None:
    """
    Vitest/Jest JUnit often sets ``classname`` to a repo-relative test file path
    (e.g. ``tests/unit/foo.test.js``), not a dotted Java-style FQCN.
    """
    cn = classname.replace("\\", "/").strip().lstrip("/")
    if not cn:
        return None
    if "/" in cn and _is_js_test_relpath(cn):
        resolved = _resolve_repo_test_path(repo_root, cn)
        return resolved or cn
    resolved = _resolve_repo_test_path(repo_root, cn)
    if resolved:
        return resolved
    return None


def _resolve_dotted_pytest_classname(classname: str, repo_root: Path) -> str | None:
    """Pygments-style data-file tests: ``tests.snippets.foo.bar.txt`` → ``tests/snippets/foo/bar.txt``."""
    if not classname or "/" in classname.replace("\\", "/"):
        return None
    parts = classname.split(".")
    if len(parts) < 2:
        return None
    root = repo_root.resolve()
    for split in range(len(parts) - 1, 0, -1):
        rel = "/".join(parts[:split]) + "/" + ".".join(parts[split:])
        if (root / rel).is_file():
            return rel
    rel = "/".join(parts)
    if (root / rel).is_file():
        return rel
    return None


def _classname_to_pytest_prefix(classname: str, repo_root: Path) -> tuple[str, str]:
    root = repo_root.resolve()
    parts = classname.split(".")
    for i in range(len(parts), 0, -1):
        mod = ".".join(parts[:i])
        for suffix in (".py",) + _JS_TEST_EXTENSIONS:
            rel = mod.replace(".", "/") + suffix
            resolved = _resolve_repo_test_path(root, rel)
            if resolved:
                qual = ".".join(parts[i:])
                return resolved, qual.replace(".", "::") if qual else ""
    resolved = _resolve_dotted_pytest_classname(classname, repo_root)
    if resolved:
        return resolved, ""
    rel = classname.replace(".", "/") + ".py"
    resolved = _resolve_repo_test_path(root, rel)
    return resolved or rel, ""


def _case_outcome(case: ET.Element) -> str:
    for child in case:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag in ("failure", "error"):
            return TestStatus.FAILED.value if tag == "failure" else TestStatus.ERROR.value
        if tag == "skipped":
            return TestStatus.SKIPPED.value
    return TestStatus.PASSED.value


def junit_case_to_nodeid(case: ET.Element, repo_root: Path) -> str:
    """
    Test label aligned with pr_to_swe_rebench_jsonl / Vitest JUnit output.

    Vitest often sets ``name`` to the full ``suite > nested > case`` chain and
    ``file`` to the repo-relative path, yielding ``path::suite > nested > case``.
    """
    name = case.attrib.get("name", "")
    classname = case.attrib.get("classname", "")
    file_a = case.attrib.get("file")
    rel_s = ""
    qual = ""
    if file_a:
        fp = Path(file_a)
        try:
            rel = fp.resolve().relative_to(repo_root.resolve())
        except ValueError:
            rel = Path(file_a)
        rel_s = _junit_file_to_repo_relpath(rel.as_posix(), repo_root)
        resolved = _resolve_repo_test_path(repo_root, rel_s)
        if resolved:
            rel_s = resolved
        mod_suffix = rel_s
        for ext in (".py",) + _JS_TEST_EXTENSIONS:
            if mod_suffix.endswith(ext):
                mod_suffix = mod_suffix[: -len(ext)].replace("/", ".")
                break
        else:
            mod_suffix = mod_suffix.replace("/", ".")
        if classname.startswith(mod_suffix + "."):
            rest = classname[len(mod_suffix) + 1 :]
            if rest:
                qual = rest.replace(".", "::")
    if not rel_s and classname:
        rel_from_cn = _rel_from_junit_classname(classname, repo_root)
        if rel_from_cn:
            rel_s = rel_from_cn
        if not rel_s:
            rel_s, qual = _classname_to_pytest_prefix(classname, repo_root)
    if rel_s:
        if qual:
            return f"{rel_s}::{qual}::{name}"
        return f"{rel_s}::{name}"
    return f"{classname}::{name}" if classname else name


def parse_junit_xml_file(path: Path, repo_root: Path) -> dict[str, str]:
    root, _ = _junit_xml_roots(path)
    if root is None:
        return {}
    out: dict[str, str] = {}
    for case in _iter_junit_elements(root, "testcase"):
        nid = junit_case_to_nodeid(case, repo_root)
        out[nid] = _case_outcome(case)
    return out


def parse_junit_xml_dir(reports_root: Path, repo_root: Path) -> dict[str, str]:
    if not reports_root.is_dir():
        return {}
    out: dict[str, str] = {}
    for xml_path in sorted(reports_root.rglob("*.xml")):
        for key, status in parse_junit_xml_file(xml_path, repo_root).items():
            out[key] = status
    return out


JUNIT_OUT_PLACEHOLDER = "__JUNIT_OUT__"


def infer_vitest_junit_container_path(test_cmd: str | list[str] | None) -> str:
    """Container path for Vitest/Jest JUnit output (default rubric layout)."""
    cmd = test_cmd
    if isinstance(cmd, list):
        cmd = " ".join(str(c) for c in cmd)
    cmd = str(cmd or "")
    if JUNIT_OUT_PLACEHOLDER in cmd:
        return "/testbed/__JUNIT_OUT__"
    m = re.search(r"--outputFile[=\s]+(\S+)", cmd)
    if m:
        path = m.group(1).strip().strip("'\"")
        if not path.startswith("/"):
            return f"/testbed/{path.lstrip('/')}"
        return path
    return "/testbed/__JUNIT_OUT__"


def junit_path_from_test_log(log_content: str, log_dir: Path) -> Path | None:
    """Resolve a host-side JUnit file path from eval log text or log directory."""
    for pattern in (
        r"JUNIT report written to\s+(\S+)",
        r"junit report written to\s+(\S+)",
    ):
        m = re.search(pattern, log_content, re.I)
        if m:
            raw = m.group(1).strip().strip("'\"")
            name = Path(raw).name
            for candidate in (
                log_dir / name,
                log_dir / "vitest-junit.xml",  # LOG_VITEST_JUNIT
                log_dir / "surefire-reports" / name,
            ):
                if candidate.is_file():
                    return candidate
    for candidate in (
        log_dir / "vitest-junit.xml",
        log_dir / "__JUNIT_OUT__",
        log_dir / "surefire-reports" / "junit.xml",
    ):
        if candidate.is_file():
            return candidate
    sf = log_dir / "surefire-reports"
    if sf.is_dir():
        xmls = sorted(sf.rglob("*.xml"))
        if len(xmls) == 1:
            return xmls[0]
    return None


def should_use_vitest_junit_xml(specs: dict) -> bool:
    cmd = specs.get("test_cmd")
    if isinstance(cmd, list):
        cmd = " ".join(str(c) for c in cmd)
    low = str(cmd or "").lower()
    return "vitest" in low and (
        "junit" in low or "outputfile" in low.replace(" ", "")
    )


def should_use_junit_xml_file(specs: dict) -> bool:
    """True when rubric tasks emit JUnit XML (Vitest or Jest+jest-junit)."""
    return should_use_vitest_junit_xml(specs) or specs_use_jest_junit(
        specs.get("test_cmd")
    )


def specs_use_jest_junit(test_cmd: str | list[str] | None) -> bool:
    if isinstance(test_cmd, list):
        test_cmd = " ".join(str(c) for c in test_cmd)
    low = str(test_cmd or "").lower()
    return "jest" in low and ("jest-junit" in low or "outputfile" in low.replace(" ", ""))
