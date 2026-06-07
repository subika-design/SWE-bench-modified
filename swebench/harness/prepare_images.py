import docker
import resource

from argparse import ArgumentParser
from pathlib import Path

from swebench.harness.constants import KEY_INSTANCE_ID
from swebench.harness.docker_build import build_instance_images
from swebench.harness.docker_utils import list_images
from swebench.harness.jsonl_register import register_harness_from_jsonl
from swebench.harness.test_spec.test_spec import make_test_spec
from swebench.harness.utils import (
    load_swebench_dataset,
    optional_str,
    resolve_harness_arch,
    str2bool,
)


def filter_dataset_to_build(
    dataset: list,
    instance_ids: list | None,
    client: docker.DockerClient,
    force_rebuild: bool,
    namespace: str = None,
    tag: str = None,
    env_image_tag: str = None,
    arch: str = "x86_64",
):
    """
    Filter the dataset to only include instances that need to be built.

    Args:
        dataset (list): List of instances (usually all of SWE-bench dev/test split)
        instance_ids (list): List of instance IDs to build.
        client (docker.DockerClient): Docker client.
        force_rebuild (bool): Whether to force rebuild all images.
    """
    # Get existing images
    existing_images = list_images(client)
    data_to_build = []

    if instance_ids is None:
        instance_ids = [instance[KEY_INSTANCE_ID] for instance in dataset]

    # Check if all instance IDs are in the dataset
    not_in_dataset = set(instance_ids).difference(
        set([instance[KEY_INSTANCE_ID] for instance in dataset])
    )
    if not_in_dataset:
        raise ValueError(f"Instance IDs not found in dataset: {not_in_dataset}")

    for instance in dataset:
        if instance[KEY_INSTANCE_ID] not in instance_ids:
            # Skip instances not in the list
            continue

        # Check if the instance needs to be built (based on force_rebuild flag and existing images)
        spec = make_test_spec(
            instance,
            namespace=namespace,
            instance_image_tag=tag,
            env_image_tag=env_image_tag,
            arch=arch,
        )
        if force_rebuild:
            data_to_build.append(instance)
        elif spec.instance_image_key not in existing_images:
            data_to_build.append(instance)

    return data_to_build


def main(
    dataset_name,
    split,
    instance_ids,
    max_workers,
    force_rebuild,
    open_file_limit,
    namespace,
    tag,
    env_image_tag,
    register_from_jsonl: str | None = None,
    arch: str = "auto",
):
    """
    Build Docker images for the specified instances.

    Args:
        instance_ids (list): List of instance IDs to build.
        max_workers (int): Number of workers for parallel processing.
        force_rebuild (bool): Whether to force rebuild all images.
        open_file_limit (int): Open file limit.
        register_from_jsonl (str | None): JSONL path to register install_config specs
            before building. Defaults to ``dataset_name`` when it is a ``.jsonl`` file.
    """
    if force_rebuild and namespace is not None:
        raise ValueError("Cannot force rebuild and use a namespace at the same time.")

    jsonl_path = register_from_jsonl
    if not jsonl_path and str(dataset_name).endswith(".jsonl"):
        jsonl_path = dataset_name
    if jsonl_path and Path(jsonl_path).exists():
        report = register_harness_from_jsonl(jsonl_path)
        print(report.summary())

    arch = resolve_harness_arch(arch)
    print(f"Using Docker image arch: {arch}")

    # Set open file limit
    resource.setrlimit(resource.RLIMIT_NOFILE, (open_file_limit, open_file_limit))
    client = docker.from_env()

    # Filter out instances that were not specified
    dataset = load_swebench_dataset(dataset_name, split)
    dataset = filter_dataset_to_build(
        dataset,
        instance_ids,
        client,
        force_rebuild,
        namespace,
        tag,
        env_image_tag,
        arch=arch,
    )

    if len(dataset) == 0:
        print("All images exist. Nothing left to build.")
        return 0

    # Build images for remaining instances
    successful, failed = build_instance_images(
        client=client,
        dataset=dataset,
        force_rebuild=force_rebuild,
        max_workers=max_workers,
        namespace=namespace,
        tag=tag,
        env_image_tag=env_image_tag,
        arch=arch,
    )
    print(f"Successfully built {len(successful)} images")
    print(f"Failed to build {len(failed)} images")


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument(
        "--dataset_name",
        type=str,
        default="SWE-bench/SWE-bench_Lite",
        help="Name of the dataset to use",
    )
    parser.add_argument("--split", type=str, default="test", help="Split to use")
    parser.add_argument(
        "--instance_ids",
        nargs="+",
        type=str,
        help="Instance IDs to run (space separated)",
    )
    parser.add_argument(
        "--max_workers", type=int, default=4, help="Max workers for parallel processing"
    )
    parser.add_argument(
        "--force_rebuild", type=str2bool, default=False, help="Force rebuild images"
    )
    parser.add_argument(
        "--open_file_limit", type=int, default=8192, help="Open file limit"
    )
    parser.add_argument(
        "--namespace",
        type=optional_str,
        default=None,
        help="Namespace to use for the images (default: None)",
    )
    parser.add_argument(
        "--tag", type=str, default=None, help="Tag to use for the images"
    )
    parser.add_argument(
        "--env_image_tag", type=str, default=None, help="Environment image tag to use"
    )
    parser.add_argument(
        "--register-from-jsonl",
        "--task-install-config-jsonl",
        dest="register_from_jsonl",
        type=str,
        default=None,
        help=(
            "Optional JSONL path to register install_config specs before building. "
            "Defaults to --dataset_name when it is a .jsonl file."
        ),
    )
    parser.add_argument(
        "--arch",
        type=str,
        default="auto",
        choices=["auto", "x86_64", "arm64"],
        help=(
            "Docker image CPU arch (auto detects host: arm64 on aarch64, "
            "x86_64 on amd64)."
        ),
    )
    args = parser.parse_args()
    main(**vars(args))
