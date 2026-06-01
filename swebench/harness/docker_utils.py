from __future__ import annotations

import docker
import docker.errors
import io
import os
import signal
import tarfile
import threading
import time
import traceback
from pathlib import Path

from docker.models.containers import Container

HEREDOC_DELIMITER = "EOF_1399519320"  # different from dataset HEREDOC_DELIMITERs!


def copy_to_container(container: Container, src: Path, dst: Path):
    """
    Copy a file from local to a docker container

    Args:
        container (Container): Docker container to copy to
        src (Path): Source file path
        dst (Path): Destination file path in the container
    """
    # Check if destination path is valid
    if os.path.dirname(dst) == "":
        raise ValueError(
            f"Destination path parent directory cannot be empty!, dst: {dst}"
        )

    # temporary tar file
    tar_path = src.with_suffix(".tar")
    with tarfile.open(tar_path, "w") as tar:
        tar.add(
            src, arcname=dst.name
        )  # use destination name, so after `put_archive`, name is correct

    # get bytes for put_archive cmd
    with open(tar_path, "rb") as tar_file:
        data = tar_file.read()

    # Make directory if necessary
    container.exec_run(f"mkdir -p {dst.parent}")

    # Send tar file to container and extract
    container.put_archive(os.path.dirname(dst), data)

    # clean up in locally and in container
    tar_path.unlink()


def copy_surefire_reports_from_container(
    container: Container,
    junit_roots: list[str],
    host_dir: Path,
    *,
    workdir: str = "/testbed",
) -> bool:
    """
    Copy Maven Surefire report directories from a container into ``host_dir``,
    preserving paths (e.g. ``gson/target/surefire-reports/TEST-*.xml``).
    """
    host_dir.mkdir(parents=True, exist_ok=True)
    copied_any = False
    for rel in junit_roots:
        rel = str(rel or "").strip().strip("/")
        if not rel:
            continue
        src = f"{workdir}/{rel}"
        check = container.exec_run(f"test -d {src}")
        if check.exit_code != 0:
            continue
        try:
            data, _stat = container.get_archive(src)
            stream = io.BytesIO()
            for chunk in data:
                stream.write(chunk)
            stream.seek(0)
            with tarfile.open(fileobj=stream, mode="r:*") as tar:
                tar.extractall(path=host_dir)
            copied_any = True
        except Exception:
            continue
    return copied_any


def copy_vitest_junit_from_container(
    container: Container,
    container_path: str,
    host_path: Path,
    *,
    workdir: str = "/testbed",
) -> bool:
    """Copy a single Vitest/Jest JUnit XML file from the container to the host."""
    path = str(container_path or "").strip()
    if not path:
        path = f"{workdir}/__JUNIT_OUT__"
    check = container.exec_run(f"test -f {path}")
    if check.exit_code != 0:
        return False
    host_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data, _stat = container.get_archive(path)
        stream = io.BytesIO()
        for chunk in data:
            stream.write(chunk)
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode="r:*") as tar:
            member = tar.getmembers()[0]
            extracted = tar.extractfile(member)
            if extracted is None:
                return False
            host_path.write_bytes(extracted.read())
        return host_path.is_file() and host_path.stat().st_size > 0
    except Exception:
        result = container.exec_run(f"cat {path}")
        if result.exit_code != 0:
            return False
        host_path.write_bytes(result.output)
        return host_path.stat().st_size > 0


def copy_junit_xml_from_container(
    container: Container,
    test_cmd: str | list[str] | None,
    host_file: Path,
    host_reports_dir: Path | None = None,
    *,
    workdir: str = "/testbed",
) -> bool:
    """
    Copy JUnit XML produced by Vitest or jest-junit from a container to the host.

    jest-junit often writes under ``/testbed/junit/`` unless
    ``JEST_JUNIT_OUTPUT_DIR`` / ``JEST_JUNIT_OUTPUT_NAME`` are set; try several paths.
    """
    from swebench.harness.log_parsers.junit_xml import infer_vitest_junit_container_path

    primary = infer_vitest_junit_container_path(test_cmd)
    candidates = [
        primary,
        f"{workdir}/__JUNIT_OUT__",
        f"{workdir}/junit/junit.xml",
        f"{workdir}/junit.xml",
    ]
    seen: set[str] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if copy_vitest_junit_from_container(
            container, path, host_file, workdir=workdir
        ):
            return True

    find = container.exec_run(
        f"find {workdir}/junit -name '*.xml' -type f 2>/dev/null | head -1",
    )
    if find.exit_code == 0:
        found = find.output.decode("utf-8").strip()
        if found and found not in seen:
            if copy_vitest_junit_from_container(
                container, found, host_file, workdir=workdir
            ):
                return True

    if host_reports_dir is not None and copy_surefire_reports_from_container(
        container, ["junit"], host_reports_dir, workdir=workdir
    ):
        xmls = sorted(host_reports_dir.rglob("*.xml"))
        if len(xmls) == 1:
            host_file.parent.mkdir(parents=True, exist_ok=True)
            host_file.write_bytes(xmls[0].read_bytes())
            return host_file.is_file() and host_file.stat().st_size > 0
        return bool(xmls)

    return False


def write_to_container(container: Container, data: str, dst: Path):
    """
    Write a string to a file in a docker container
    """
    # echo with heredoc to file
    command = f"cat <<'{HEREDOC_DELIMITER}' > {dst}\n{data}\n{HEREDOC_DELIMITER}"
    container.exec_run(command)


def remove_image(client, image_id, logger=None):
    """
    Remove a Docker image by ID.

    Args:
        client (docker.DockerClient): Docker client.
        image_id (str): Image ID.
        rm_image (bool): Whether to remove the image.
        logger (logging.Logger): Logger to use for output. If None, print to stdout.
    """
    if not logger:
        # if logger is None, print to stdout
        log_info = print
        log_error = print
        raise_error = True
    elif logger == "quiet":
        # if logger is "quiet", don't print anything
        log_info = lambda x: None
        log_error = lambda x: None
        raise_error = True
    else:
        # if logger is a logger object, use it
        log_error = logger.info
        log_info = logger.info
        raise_error = False
    try:
        log_info(f"Attempting to remove image {image_id}...")
        client.images.remove(image_id, force=True)
        log_info(f"Image {image_id} removed.")
    except docker.errors.ImageNotFound:
        log_info(f"Image {image_id} not found, removing has no effect.")
    except Exception as e:
        if raise_error:
            raise e
        log_error(f"Failed to remove image {image_id}: {e}\n{traceback.format_exc()}")


def cleanup_container(client, container, logger):
    """
    Stop and remove a Docker container.
    Performs this forcefully if the container cannot be stopped with the python API.

    Args:
        client (docker.DockerClient): Docker client.
        container (docker.models.containers.Container): Container to remove.
        logger (logging.Logger): Logger to use for output. If None, print to stdout
    """
    if not container:
        return

    container_id = container.id

    if not logger:
        # if logger is None, print to stdout
        log_error = print
        log_info = print
        raise_error = True
    elif logger == "quiet":
        # if logger is "quiet", don't print anything
        log_info = lambda x: None
        log_error = lambda x: None
        raise_error = True
    else:
        # if logger is a logger object, use it
        log_error = logger.info
        log_info = logger.info
        raise_error = False

    # Attempt to stop the container
    try:
        if container:
            log_info(f"Attempting to stop container {container.name}...")
            container.stop(timeout=15)
    except Exception as e:
        log_error(
            f"Failed to stop container {container.name}: {e}. Trying to forcefully kill..."
        )
        try:
            # Get the PID of the container
            container_info = client.api.inspect_container(container_id)
            pid = container_info["State"].get("Pid", 0)

            # If container PID found, forcefully kill the container
            if pid > 0:
                log_info(
                    f"Forcefully killing container {container.name} with PID {pid}..."
                )
                os.kill(pid, signal.SIGKILL)
            else:
                log_error(f"PID for container {container.name}: {pid} - not killing.")
        except Exception as e2:
            if raise_error:
                raise e2
            log_error(
                f"Failed to forcefully kill container {container.name}: {e2}\n"
                f"{traceback.format_exc()}"
            )

    # Attempt to remove the container
    try:
        log_info(f"Attempting to remove container {container.name}...")
        container.remove(force=True)
        log_info(f"Container {container.name} removed.")
    except Exception as e:
        if raise_error:
            raise e
        log_error(
            f"Failed to remove container {container.name}: {e}\n"
            f"{traceback.format_exc()}"
        )


def exec_run_with_timeout(container, cmd, timeout: int | None = 60):
    """
    Run a command in a container with a timeout.

    Args:
        container (docker.Container): Container to run the command in.
        cmd (str): Command to run.
        timeout (int): Timeout in seconds.
    """
    # Local variables to store the result of executing the command
    exec_result = b""
    exec_id = None
    exception = None
    timed_out = False

    # Wrapper function to run the command
    def run_command():
        nonlocal exec_result, exec_id, exception
        try:
            exec_id = container.client.api.exec_create(container.id, cmd)["Id"]
            exec_stream = container.client.api.exec_start(exec_id, stream=True)
            for chunk in exec_stream:
                exec_result += chunk
        except Exception as e:
            exception = e

    # Start the command in a separate thread
    thread = threading.Thread(target=run_command)
    start_time = time.time()
    thread.start()
    thread.join(timeout)

    if exception:
        raise exception

    # If the thread is still alive, the command timed out
    if thread.is_alive():
        if exec_id is not None:
            exec_pid = container.client.api.exec_inspect(exec_id)["Pid"]
            container.exec_run(f"kill -TERM {exec_pid}", detach=True)
        timed_out = True
    end_time = time.time()
    return exec_result.decode(), timed_out, end_time - start_time


def find_dependent_images(client: docker.DockerClient, image_name: str):
    """
    Find all images that are built upon `image_name` image

    Args:
        client (docker.DockerClient): Docker client.
        image_name (str): Name of the base image.
    """
    dependent_images = []

    # Get all local images
    all_images = client.images.list()

    # Get the ID of the base image
    try:
        base_image = client.images.get(image_name)
        base_image_id = base_image.id
    except docker.errors.ImageNotFound:
        print(f"Base image {image_name} not found.")
        return []

    for image in all_images:
        # Skip the base image itself
        if image.id == base_image_id:
            continue

        # Check if the base image is in this image's history
        history = image.history()
        for layer in history:
            if layer["Id"] == base_image_id:
                # If found, add this image to the dependent images list
                tags = image.tags
                dependent_images.append(tags[0] if tags else image.id)
                break

    return dependent_images


def list_images(client: docker.DockerClient):
    """
    List all images from the Docker client.
    """
    # don't use this in multi-threaded context
    return {tag for i in client.images.list(all=True) for tag in i.tags}


def clean_images(
    client: docker.DockerClient, prior_images: set, cache_level: str, clean: bool
):
    """
    Clean Docker images based on cache level and clean flag.

    Args:
        client (docker.DockerClient): Docker client.
        prior_images (set): Set of images that existed before the current run.
        cache (str): Cache level to use.
        clean (bool): Whether to clean; remove images that are higher in the cache hierarchy than the current
            cache level. E.g. if cache_level is set to env, remove all previously built instances images. if
            clean is false, previously built instances images will not be removed, but instance images built
            in the current run will be removed.
    """
    images = list_images(client)
    removed = 0
    print("Cleaning cached images...")
    for image_name in images:
        if should_remove(image_name, cache_level, clean, prior_images):
            try:
                remove_image(client, image_name, "quiet")
                removed += 1
            except Exception as e:
                print(f"Error removing image {image_name}: {e}")
                continue
    print(f"Removed {removed} images.")


def should_remove(image_name: str, cache_level: str, clean: bool, prior_images: set):
    """
    Determine if an image should be removed based on cache level and clean flag.
    """
    existed_before = image_name in prior_images
    if "/" in image_name:
        image_name = image_name.rsplit("/", 1)[-1]
    if image_name.startswith("sweb.base"):
        if cache_level in {"none"} and (clean or not existed_before):
            return True
    elif image_name.startswith("sweb.env"):
        if cache_level in {"none", "base"} and (clean or not existed_before):
            return True
    elif image_name.startswith("sweb.eval"):
        if cache_level in {"none", "base", "env"} and (clean or not existed_before):
            return True
    return False
