"""Benchmark utilities for SWE-bench and SWE-bench Pro."""

import json
from abc import ABC, abstractmethod
from pathlib import Path
from jinja2 import StrictUndefined, Template

from minisweagent import Environment
from minisweagent.utils.constants import (
    BenchMarkType,
    SWEBENCH_DATASET_MAPPING,
    SWEBENCHPRO_DATASET_MAPPING,
    BENCHMARK_WORKDIR_MAPPING,
)
from minisweagent.constants import MAP_REPO_VERSION_TO_SPECS
from minisweagent.environments import get_environment
from minisweagent.utils.parser_utils import get_test_directives

# SWE-bench Pro test command mapping cache
_SWEBENCHPRO_TEST_CMD_CACHE: dict[str, str] | None = None
_SWEBENCHPRO_TEST_CMD_PATH = Path("SWE-ABS/SWE-bench_Pro-os/test_commands_clean.json")


class BenchmarkConfig(ABC):
    """Base class for benchmark configurations."""

    benchmark_type: BenchMarkType
    dataset_mapping: dict

    @abstractmethod
    def get_docker_image_name(self, instance: dict) -> str:
        """Get the Docker image name."""
        pass

    def get_dataset_path(self, subset: str = "verified") -> str:
        """Get the dataset path."""
        if subset in self.dataset_mapping:
            return self.dataset_mapping[subset]
        raise ValueError(f"Unknown subset '{subset}' for {self.benchmark_type}")


class SWEBenchConfig(BenchmarkConfig):
    """SWE-bench configuration"""

    benchmark_type = BenchMarkType.SWEBENCH
    dataset_mapping = SWEBENCH_DATASET_MAPPING

    def get_docker_image_name(self, instance: dict) -> str:
        image_name = instance.get("image_name", None)
        if image_name is None:
            # Docker doesn't allow double underscore, so we replace them with a magic token
            iid = instance["instance_id"]
            id_docker_compatible = iid.replace("__", "_1776_")
            image_name = f"docker.io/swebench/sweb.eval.x86_64.{id_docker_compatible}:latest".lower()
        return image_name


class SWEBenchProConfig(BenchmarkConfig):

    benchmark_type = BenchMarkType.SWEBENCHPRO
    dataset_mapping = SWEBENCHPRO_DATASET_MAPPING

    def get_docker_image_name(self, instance):
        """
        Legacy function for backwards compatibility.
        Convert instance_id and repo_name to Docker Hub image URI.

        Args:
            uid (str): The instance_id (e.g., "instance_django__django-12345-v...")
            dockerhub_username (str): Docker Hub username
            repo_name (str): The repository name (e.g., "NodeBB/NodeBB")

        Returns:
            str: Full Docker Hub image URI
        """
        dockerhub_username = "jefzda"
        uid = instance["instance_id"]
        repo_name = instance.get("repo", "")
        repo_base, repo_name_only = repo_name.lower().split("/")
        hsh = uid.replace("instance_", "")

        if uid == "instance_element-hq__element-web-ec0f940ef0e8e3b61078f145f34dc40d1938e6c5-vnan":
            repo_name_only = 'element-web'  # Keep full name for this one case
        elif 'element-hq' in repo_name.lower() and 'element-web' in repo_name.lower():
            repo_name_only = 'element'
            if hsh.endswith('-vnan'):
                hsh = hsh[:-5]
        # All other repos: strip -vnan suffix
        elif hsh.endswith('-vnan'):
            hsh = hsh[:-5]

        tag = f"{repo_base}.{repo_name_only}-{hsh}"
        if len(tag) > 128:
            tag = tag[:128]

        return f"{dockerhub_username}/sweap-images:{tag}"

# Register all benchmark configurations
_BENCHMARK_CONFIGS: dict[BenchMarkType, BenchmarkConfig] = {
    BenchMarkType.SWEBENCH: SWEBenchConfig(),
    BenchMarkType.SWEBENCHPRO: SWEBenchProConfig(),
}


def get_benchmark_config(benchmark_type: BenchMarkType) -> BenchmarkConfig:
    """Get the benchmark configuration."""
    if benchmark_type not in _BENCHMARK_CONFIGS:
        raise ValueError(f"Unknown benchmark type: {benchmark_type}")
    return _BENCHMARK_CONFIGS[benchmark_type]


def get_docker_image_name(instance: dict, benchmark_type: BenchMarkType) -> str:
    """Convenience function to get the Docker image name."""
    return get_benchmark_config(benchmark_type).get_docker_image_name(instance)


def get_dataset_path(benchmark_type: BenchMarkType, subset: str = "verified") -> str:
    """Convenience function to get the dataset path."""
    return get_benchmark_config(benchmark_type).get_dataset_path(subset)


def get_workdir(benchmark_type: BenchMarkType) -> str:
    """Get the working directory for the benchmark."""
    if benchmark_type not in BENCHMARK_WORKDIR_MAPPING:
        raise ValueError(f"Unknown benchmark type: {benchmark_type}")
    return BENCHMARK_WORKDIR_MAPPING[benchmark_type]


def _load_swebenchpro_test_cmd() -> dict[str, str]:
    """Load the test command mapping for SWE-bench Pro."""
    global _SWEBENCHPRO_TEST_CMD_CACHE
    if _SWEBENCHPRO_TEST_CMD_CACHE is None:
        if _SWEBENCHPRO_TEST_CMD_PATH.exists():
            with open(_SWEBENCHPRO_TEST_CMD_PATH, "r") as f:
                _SWEBENCHPRO_TEST_CMD_CACHE = json.load(f)
        else:
            _SWEBENCHPRO_TEST_CMD_CACHE = {}
    return _SWEBENCHPRO_TEST_CMD_CACHE


def get_test_command(instance: dict, benchmark_type: BenchMarkType) -> str:
    """
        Get the test command.

            Args:
                instance: A dictionary containing instance_id, repo, version, and other fields
                benchmark_type: The benchmark type

            Returns:
                The test command string
    """
    if benchmark_type == BenchMarkType.SWEBENCH:
        # SWE-bench: retrieve from MAP_REPO_VERSION_TO_SPECS
        return MAP_REPO_VERSION_TO_SPECS[instance["repo"]][instance["version"]]["test_cmd"]
    elif benchmark_type == BenchMarkType.SWEBENCHPRO:
        # SWE-bench Pro: retrieve from JSON file
        test_cmd_mapping = _load_swebenchpro_test_cmd()
        instance_id = instance["instance_id"]
        if instance_id not in test_cmd_mapping:
            raise KeyError(
                f"Test command not found for instance '{instance_id}'. "
                f"Please add it to {_SWEBENCHPRO_TEST_CMD_PATH}"
            )
        return test_cmd_mapping[instance_id]
    else:
        raise ValueError(f"Unknown benchmark type: {benchmark_type}")


def build_test_command_with_directives(
    instance: dict,
    benchmark_type: BenchMarkType,
    test_patch_key: str = 'model_test_patch',
) -> str:
    """
        Build a complete test command that includes test directives.

            Args:
                instance: A dictionary containing instance_id, repo, version, and other fields
                benchmark_type: The benchmark type
                directives: List of test files/modules

            Returns:
                The full test command string

            Notes:
                - SWE-bench: test_command and directives are joined with a space
                  e.g.: "pytest test_foo.py test_bar.py"
                - SWE-bench Pro: test_command contains a "$@" placeholder; directives are joined with commas and substituted in
                  e.g.: 'npx mocha --reporter=json "$@"' -> 'npx mocha --reporter=json "test1.js,test2.js"'
    """
    test_command = get_test_command(instance, benchmark_type)
    directives = get_test_directives(instance, test_patch_key)
    if benchmark_type == BenchMarkType.SWEBENCH:
        # SWE-bench: join with spaces
        if directives:
            return " ".join([test_command, *directives])
        return test_command
    elif benchmark_type == BenchMarkType.SWEBENCHPRO:
        # SWE-bench Pro: join directives with commas, replace $@
        directives_str = ",".join(directives)
        # print("directives_str", directives_str)
        # print("test_command", test_command)
        return test_command.replace("$@", directives_str)
    else:
        raise ValueError(f"Unknown benchmark type: {benchmark_type}")


def get_sb_environment(config: dict, instance: dict, benchmark_type: BenchMarkType) -> Environment:
    env_config = config.setdefault("environment", {})
    env_config["environment_class"] = env_config.get("environment_class", "docker")
    image_name = get_docker_image_name(instance, benchmark_type)  

    if env_config["environment_class"] == "docker":
        env_config["image"] = image_name
    elif env_config["environment_class"] == "singularity":
        env_config["image"] = "docker://" + image_name
    env = get_environment(env_config)
    if startup_command := config.get("run", {}).get("env_startup_command"):
        startup_command = Template(startup_command, undefined=StrictUndefined).render(**instance)
        out = env.execute(startup_command)
        if out["returncode"] != 0:
            raise RuntimeError(f"Error executing startup command: {out}")
    return env



if __name__ == "__main__":

    file = 'SWE-ABS/mini-swe-agent/result/model_gen_test/pro_selecet_141/preds.json'


    instance_id = 'instance_element-hq__element-web-4c6b0d35add7ae8d58f71ea1711587e31081444b-vnan'

    with open(file, "r") as f:
        preds = json.load(f)
    
    instance = preds[instance_id]


    directives_str = get_test_directives(instance, test_patch_key='model_test_patch')

    print(directives_str)
    print(instance['model_test_patch'])

