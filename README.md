# SWE-bench (modified)

Fork of [SWE-bench](https://github.com/SWE-bench/SWE-bench) extended to evaluate **custom JSONL tasks** (SWE-rebench / rubric style) in addition to the official HuggingFace datasets. Each JSONL row carries its own `install_config`; the harness registers repo specs at runtime instead of requiring edits to `constants/*.py`.

## Setup

Install [Docker](https://docs.docker.com/engine/install/), then:

```bash
cd SWE-bench-modified
pip install -e ".[datasets]"
```

On Apple Silicon (M-series), use `--namespace ""` on image build and evaluation so images are built locally instead of pulled from DockerHub (Linux-only).

---

## Quick start

Set paths once:

```bash
cd /Users/subika/Turing/SWE-bench   # or this repo root

DATASET="path of tasks jsonl"
PREDICTIONS="path of pred json from the model"
```

### 1 — Prepare images (optional)

`run_evaluation` builds missing images automatically. Use `prepare_images` when you want to pre-build Docker images before a long eval run:

```bash
python -m swebench.harness.prepare_images \
  --dataset_name "$DATASET" \
  --max_workers 1 \
  --namespace ""
```

Build logs go to `logs/build_images/`.

### 2 — Evaluation (model predictions)

Point `--predictions_path` at a JSONL from `run_api` (or any file with `instance_id` + `model_patch` per line):

```bash
python -m swebench.harness.run_evaluation \
  --dataset_name "$DATASET" \
  --predictions_path "$PREDICTIONS" \
  --run_id rubric_eval \
  --max_workers 1 \
  --namespace ""
```

Results: `logs/run_evaluation/<run_id>/` and a summary JSON in the current directory.

### 3 — Gold run (sanity check, no API)

Verify the harness using each row’s gold patch as the prediction:

```bash
python -m swebench.harness.run_evaluation \
  --dataset_name "$DATASET" \
  --predictions_path gold \
  --run_id rubric_gold \
  --max_workers 1 \
  --namespace ""
```

Use `--predictions_path gold` to substitute the dataset patch for every instance. A healthy dataset should resolve most or all gold runs.

---

## How it works

### JSONL as the dataset

Pass a local `.jsonl` file to `--dataset_name`. Each line is one task instance with (at minimum):


| Field                           | Role                                                                               |
| ------------------------------- | ---------------------------------------------------------------------------------- |
| `instance_id`                   | Unique ID, e.g. `pandas-dev__pandas-12345`                                         |
| `repo`                          | GitHub repo, e.g. `pandas-dev/pandas`                                              |
| `version`                       | Version key for spec lookup, e.g. `3.1`                                            |
| `language`                      | Harness language: `python`, `javascript`, `java`, `go`, `c`, `php`, `ruby`, `rust` |
| `base_commit`                   | Git SHA to evaluate at                                                             |
| `test_patch`                    | Patch that adds/updates tests                                                      |
| `patch`                         | Gold fix patch                                                                     |
| `FAIL_TO_PASS` / `PASS_TO_PASS` | JSON arrays of test labels to grade against                                        |
| `install_config`                | Install + test recipe (see below)                                                  |


When `--dataset_name` ends with `.jsonl`, registration runs automatically before build/eval. You can also pass a separate path via `--register-from-jsonl` / `--task-install-config-jsonl`.

### Runtime registration (`jsonl_register.py`)

For each row with an `install_config`, the harness mutates in-memory maps used by upstream SWE-bench:

1. `**install_config` → `MAP_REPO_VERSION_TO_SPECS[repo][version]**` — install commands, test command, docker options.
2. `**language` → `MAP_REPO_TO_EXT[repo]**` — selects the language Dockerfile template (`py`, `js`, `java`, …).
3. **Log parser** — chosen heuristically from `language` + `test_cmd` (pytest, django, vitest, jest, maven, gradle, cargo, phpunit, …) and registered in `MAP_REPO_TO_PARSER`.

Rows without `install_config` are skipped at registration time. Official SWE-bench repos still use the hardcoded specs in `swebench/harness/constants/`; JSONL tasks overlay or add to those maps at runtime.

### `install_config` keys the harness reads


| Key                                               | Purpose                                                           |
| ------------------------------------------------- | ----------------------------------------------------------------- |
| `install`                                         | Repo install command(s)                                           |
| `test_cmd`                                        | Command run inside the container after applying `test_patch`      |
| `pre_install` / `post_install`                    | Extra shell steps before/after install                            |
| `python`, `packages`, `pip_packages`, `reqs_path` | Python env setup                                                  |
| `eval_commands`                                   | Shell exports/commands injected into the eval script              |
| `docker_specs`                                    | Per-instance Docker options (e.g. `java_version`, `node_version`) |
| `apt-pkgs`                                        | Extra apt packages in the env image                               |


These are normalized by `install_config_to_harness_specs()` in `swebench/harness/jsonl_register.py`.

### How Dockerfiles are chosen

Evaluation uses a three-layer image stack per instance:

```
base image  →  env image  →  instance image
(language)     (deps)         (repo @ base_commit)
```

**Language → template.** `MAP_REPO_TO_EXT[repo]` picks one of eight templates in `swebench/harness/dockerfiles/` (`python`, `javascript`, `java`, `go`, `c`, `php`, `ruby`, `rust`). The `language` field on each JSONL row drives this at registration time.

**Base image** — OS + toolchain (conda for Python, Node for JS, JDK for Java, etc.). If `install_config.docker_specs` is non-empty, a hash of those specs is appended to the base image tag so different tool versions get distinct bases.

**Env image** — runs the env script (apt packages, conda env, npm install, etc.) derived from `MAP_REPO_VERSION_TO_SPECS`. Rebuilt when the env script or `docker_specs` change.

**Instance image** — clones the repo at `base_commit`. One image per `instance_id`.

`TestSpec` in `swebench/harness/test_spec/test_spec.py` assembles the three Dockerfiles and image keys from the registered spec + instance fields.

### Eval flow (one instance)

1. Start container from the instance image.
2. Run the **eval script**: apply `test_patch`, run `test_cmd`, capture stdout (and optionally JUnit XML).
3. Apply the **model patch** (or gold patch), reset tests, run `test_cmd` again.
4. **Grade** (`swebench/harness/grading.py`): parse logs into a test→status map, compare against `FAIL_TO_PASS` / `PASS_TO_PASS`. An instance is **resolved** when all F2P tests pass and all P2P tests still pass.

For Vitest/Jest tasks that emit JUnit XML, grading prefers the XML file over stdout (`swebench/harness/log_parsers/junit_xml.py`). Test labels in `FAIL_TO_PASS` must match the parser’s output format (e.g. `path/to/test.js::suite > case name` for Vitest).

### Logs and outputs


| Path                            | Contents                                                 |
| ------------------------------- | -------------------------------------------------------- |
| `logs/build_images/`            | Docker build logs                                        |
| `logs/run_evaluation/<run_id>/` | Per-instance eval logs, `test_output.txt`, `report.json` |
| `*.gold.json` / eval summary    | Aggregate resolved/unresolved counts                     |


---

## Upstream docs

For official SWE-bench datasets, inference, collection, and cloud eval (Modal, sb-cli), see the [SWE-bench docs](https://swebench.com/SWE-bench/) and `docs/` in this repo.

## License

MIT — see `LICENSE`.