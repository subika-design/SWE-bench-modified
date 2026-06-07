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
    while s.startswith("./"):
        s = s[2:]
    return s.lstrip("/")


def _is_js_test_relpath(rel: str) -> bool:
    low = rel.lower()
    return any(low.endswith(ext) for ext in _JS_TEST_EXTENSIONS)


def _path_likely_rspec(rel: str) -> bool:
    low = rel.lower()
    return low.endswith("_spec.rb") or "/spec/" in low


def _resolve_repo_test_path(repo_root: Path, rel: str) -> str | None:
    root = repo_root.resolve()
    rel = rel.replace("\\", "/").strip().lstrip("/")
    if not rel:
        return None
    if (root / rel).is_file():
        return rel
    if _is_js_test_relpath(rel) and (root / Path(rel).name).is_file():
        return Path(rel).name
    if _path_likely_rspec(rel) and (root / Path(rel).name).is_file():
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


def _local_tag(el: ET.Element) -> str:
    tag = el.tag
    return tag.split("}")[-1] if "}" in tag else tag


def _walk_testcases_with_suite_file(root: ET.Element):
    """Yield ``(testcase, nearest_ancestor_testsuite_file)`` depth-first."""

    def walk_suite(suite: ET.Element, inherited_file: str | None):
        suite_file = suite.attrib.get("file") or inherited_file
        for child in suite:
            tag = _local_tag(child)
            if tag == "testcase":
                yield child, suite_file
            elif tag == "testsuite":
                yield from walk_suite(child, suite_file)

    root_tag = _local_tag(root)
    if root_tag == "testsuites":
        for child in root:
            if _local_tag(child) == "testsuite":
                yield from walk_suite(child, None)
    elif root_tag == "testsuite":
        yield from walk_suite(root, None)


def _is_mocha_style_junit_case(
    case: ET.Element,
    suite_file: str | None,
    repo_root: Path,
) -> bool:
    if not suite_file or case.attrib.get("file"):
        return False
    rel = _junit_file_to_repo_relpath(suite_file, repo_root)
    if not rel or not _is_js_test_relpath(rel):
        return False
    classname = case.attrib.get("classname", "")
    if _rel_from_junit_classname(classname, repo_root):
        return False
    return bool(classname and case.attrib.get("name"))


def _mocha_style_nodeid(case: ET.Element, suite_file: str, repo_root: Path) -> str:
    rel = _junit_file_to_repo_relpath(suite_file, repo_root)
    resolved = _resolve_repo_test_path(repo_root, rel)
    if resolved:
        rel = resolved
    classname = case.attrib.get("classname", "").strip()
    name = case.attrib.get("name", "").strip()
    if classname and classname != name:
        return f"{rel}::{classname}::{name}"
    return f"{rel}::{name}"


def _case_outcome(case: ET.Element) -> str:
    for child in case:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag in ("failure", "error"):
            return TestStatus.FAILED.value if tag == "failure" else TestStatus.ERROR.value
        if tag == "skipped":
            return TestStatus.SKIPPED.value
    return TestStatus.PASSED.value


def _rspec_junit_classname_is_dotted_spec_path(classname: str, rel_s: str) -> bool:
    """
    True when ``rspec_junit_formatter`` puts the spec file in ``classname``.

    That reporter emits either:

    * **Group + example** — ``classname`` is the nested example group (often
      contains ``::`` or spaces), ``name`` is the short example title, and the
      spec path comes from ``testsuite/@file`` or ``testcase/@file``.
    * **Full example in name** — ``classname`` is a dotted spec path such as
      ``spec.rubocop.version_spec``, and ``name`` already contains the full
      ``group example`` text used in rubric keys.
    """
    if not classname:
        return False
    if "::" in classname or " " in classname:
        return False
    cn = classname.replace("\\", ".").strip(".")
    if not cn or "/" in cn:
        return False

    if rel_s:
        rel_norm = rel_s.replace("\\", "/").lstrip("./")
        dotted_rel = rel_norm.removesuffix(".rb").replace("/", ".")
        stem = Path(rel_norm).stem
        if cn == dotted_rel or cn == stem:
            return True

    if cn.startswith("spec.") and cn.endswith("_spec"):
        return True
    return False


def junit_case_to_rspec_nodeid(
    case: ET.Element,
    repo_root: Path,
    *,
    suite_file: str | None = None,
) -> str:
    """Node id aligned with taskgen RSpec labels (``path::group example``)."""
    name = (case.attrib.get("name") or "").strip()
    classname = (case.attrib.get("classname") or "").strip()
    file_a = case.attrib.get("file") or suite_file
    rel_s = ""
    if file_a:
        rel_s = _junit_file_to_repo_relpath(str(file_a), repo_root)
        resolved = _resolve_repo_test_path(repo_root, rel_s)
        if resolved:
            rel_s = resolved
    if rel_s:
        if name and _rspec_junit_classname_is_dotted_spec_path(classname, rel_s):
            return f"{rel_s}::{name}"
        if classname and name and classname != name and not classname.startswith(rel_s):
            return f"{rel_s}::{classname} {name}".strip()
        if name:
            return f"{rel_s}::{name}"
        if classname:
            return f"{rel_s}::{classname}"
        return rel_s
    if classname:
        return f"{classname}::{name}" if name else classname
    return name


def junit_case_to_nodeid(
    case: ET.Element,
    repo_root: Path,
    *,
    suite_file: str | None = None,
) -> str:
    """
    Test label aligned with pr_to_swe_rebench_jsonl / Vitest JUnit output.

    Vitest often sets ``name`` to the full ``suite > nested > case`` chain and
    ``file`` to the repo-relative path, yielding ``path::suite > nested > case``.

    Mocha JUnit reporter puts ``file`` on ancestor ``testsuite`` elements and uses
    ``classname`` for the short test title, yielding
    ``path::short_title::full_title``.
    """
    if _is_mocha_style_junit_case(case, suite_file, repo_root):
        return _mocha_style_nodeid(case, suite_file, repo_root)

    name = case.attrib.get("name", "").strip()
    classname = case.attrib.get("classname", "").strip()
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


def parse_junit_xml_file(
    path: Path,
    repo_root: Path,
    *,
    specs: dict | None = None,
) -> dict[str, str]:
    root, _ = _junit_xml_roots(path)
    if root is None:
        return {}
    use_rspec = specs_use_rspec_junit((specs or {}).get("test_cmd"))
    out: dict[str, str] = {}
    for case, suite_file in _walk_testcases_with_suite_file(root):
        if use_rspec:
            nid = junit_case_to_rspec_nodeid(case, repo_root, suite_file=suite_file)
        else:
            nid = junit_case_to_nodeid(case, repo_root, suite_file=suite_file)
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
MOCHA_JUNIT_REPORTER_PLACEHOLDER = "__MOCHA_JUNIT_REPORTER__"
MOCHA_JUNIT_REPORTER_MODULE = "mocha-junit-reporter"


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


def specs_use_rspec_junit(test_cmd: str | list[str] | None) -> bool:
    """True when ``test_cmd`` runs RSpec with JUnit output (rubric JSONL layout)."""
    if isinstance(test_cmd, list):
        test_cmd = " ".join(str(c) for c in test_cmd)
    low = str(test_cmd or "").lower()
    compact = low.replace(" ", "")
    return "rspec" in low and (
        "rspecjunitformatter" in compact
        or JUNIT_OUT_PLACEHOLDER.lower() in low
    )


def should_use_junit_xml_file(specs: dict) -> bool:
    """True when rubric tasks emit JUnit XML (Vitest, Jest, Mocha, or RSpec)."""
    return (
        should_use_vitest_junit_xml(specs)
        or specs_use_jest_junit(specs.get("test_cmd"))
        or specs_use_mocha_junit(specs.get("test_cmd"))
        or specs_use_rspec_junit(specs.get("test_cmd"))
    )


def specs_use_jest_junit(test_cmd: str | list[str] | None) -> bool:
    if isinstance(test_cmd, list):
        test_cmd = " ".join(str(c) for c in test_cmd)
    low = str(test_cmd or "").lower()
    return "jest" in low and ("jest-junit" in low or "outputfile" in low.replace(" ", ""))


def specs_use_mocha_junit(test_cmd: str | list[str] | None) -> bool:
    """True when ``test_cmd`` runs Mocha with JUnit output (rubric JSONL layout)."""
    if isinstance(test_cmd, list):
        test_cmd = " ".join(str(c) for c in test_cmd)
    low = str(test_cmd or "").lower()
    if "mocha" not in low:
        return False
    compact = low.replace(" ", "")
    return (
        MOCHA_JUNIT_REPORTER_PLACEHOLDER.lower() in low
        or MOCHA_JUNIT_REPORTER_MODULE in low
        or (
            "mochafile" in compact
            and JUNIT_OUT_PLACEHOLDER.lower() in low
        )
    )
