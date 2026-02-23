import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from minisweagent import Agent, __version__


def _get_class_name_with_module(obj: Any) -> str:
    """Get the full class name with module path."""
    return f"{obj.__class__.__module__}.{obj.__class__.__name__}"


def _asdict(obj: Any) -> dict:
    """Convert config objects to dicts."""
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)  # type: ignore[arg-type]
    return obj  # let's try our luck


def save_traj(
    args,
    agent: Agent | None,
    path: Path,
    # judge_res: str | None = None,
    # judge_messages: list | None = None,
    *,
    print_path: bool = True,
    exit_status: str | None = None,
    result: str | None = None,
    extra_info: dict | None = None,
    print_fct: Callable = print,
    **kwargs,
):
    """Save the trajectory of the agent to a file.

    Args:
        agent: The agent to save the trajectory of.
        path: The path to save the trajectory to.
        print_path: Whether to print confirmation of path to the terminal.
        exit_status: The exit status of the agent.
        result: The result/submission of the agent.
        extra_info: Extra information to save (will be merged into the info dict).
        **kwargs: Additional information to save (will be merged into top level)

    """
    data = {
        "info": {
            "exit_status": exit_status,
            "submission": result,
            "model_stats": {
                "instance_cost": 0.0,
                "api_calls": 0,
            },
            "mini_version": __version__,
        },
        "messages": [],
        "trajectory_format": "mini-swe-agent-1",
    } | kwargs
    if agent is not None:
        data["info"]["model_stats"]["instance_cost"] = agent.model.cost
        data["info"]["model_stats"]["api_calls"] = agent.model.n_calls
        data["messages"] = agent.messages
        data["info"]["config"] = {
            "agent": _asdict(agent.config),
            "model": _asdict(agent.model.config),
            # "environment": _asdict(agent.env.config),
            "agent_type": _get_class_name_with_module(agent),
            "model_type": _get_class_name_with_module(agent.model),
            # "environment_type": _get_class_name_with_module(agent.env),
        }
    # if hasattr(args, 'judge_model_mutatation') and args.judge_model_mutatation:
    #     data["info"]["judge_res"] = judge_res
    #     data["judge_messages"] = judge_messages
    if extra_info:
        data["info"].update(extra_info)

    if kwargs.get("target_mutatation_patch",{}):
        data['target_mutatation_patch'] = kwargs['target_mutatation_patch']

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    if print_path:
        print_fct(f"Saved trajectory to '{path}'")



def save_test_traj(instance:dict, result, subset, save_file:Path, **kwargs):

    save_data = {
        "subset":subset,
        "model_test_patch":result,
        **instance
    }
    save_data['model_patch'] = save_data['patch']
    save_data = {
        save_data['instance_id']:save_data
    }


    save_file.parent.mkdir(parents=True, exist_ok=True) 
    save_file.write_text(json.dumps(save_data, indent=2))


def save_mutation_traj(instance:dict, result, subset, save_file:Path, **kwargs):

    save_data = {
        "subset":subset,
        "model_patch":result,
        **instance
    }
    save_data['model_name_or_path'] = 'gpt5' 
    save_data = {
        save_data['instance_id']:save_data
    }

    save_file.parent.mkdir(parents=True, exist_ok=True) 
    save_file.write_text(json.dumps(save_data, indent=2))