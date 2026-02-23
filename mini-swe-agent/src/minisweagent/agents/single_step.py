import re
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass

from jinja2 import StrictUndefined, Template

from minisweagent import Environment, Model


@dataclass
class SingleStepConfig:
    # The default settings are the bare minimum to run the agent. Take a look at the config files for improved settings.
    system_template: str = "You are a helpful assistant that can do anything."
    instance_template: str = (
        "Your task: {{task}}. Please reply with a single shell command in triple backticks. "
        "To finish, the first line of the output of the shell command must be 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT'."
    )
    timeout_template: str = (
        "The last command <command>{{action['action']}}</command> timed out and has been killed.\n"
        "The output of the command was:\n <output>\n{{output}}\n</output>\n"
        "Please try another command and make sure to avoid those requiring interactive input."
    )
    format_error_template: str = "Please always provide EXACTLY ONE action in triple backticks."
    action_observation_template: str = "Observation: {{output}}"
    step_limit: int = 0
    cost_limit: float = 3.0


class SingleStepAgent:
    def __init__(self, model: Model, **kwargs):
        self.model = model
        self.config = SingleStepConfig(**kwargs)

    def render_template(self, template: str, **kwargs) -> str:
        template_vars = asdict(self.config) | self.model.get_template_vars()
        return Template(template, undefined=StrictUndefined).render(
            **kwargs, **template_vars, **self.extra_template_vars
        )

    def add_message(self, role: str, content: str, **kwargs):
        self.messages.append({"role": role, "content": content, **kwargs})

    def run(self, **kwargs) -> tuple[str, str]:
        """Run step() until agent is finished. Return exit status & message"""
        self.extra_template_vars = {**kwargs}
        self.messages = []
        self.add_message("system", self.render_template(self.config.system_template))
        self.add_message("user", self.render_template(self.config.instance_template))
        response = self.query()
        self.add_message("assist", **response)
        return response['content']

    def query(self) -> dict:
        """Query the model and return the response."""
        response = self.model.query(self.messages)
        return response

