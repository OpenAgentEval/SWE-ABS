"""Basic agent class supporting single or multiple environments."""

import re
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from abc import ABC, abstractmethod
from typing import Dict, Union, Any, Tuple, List
from jinja2 import StrictUndefined, Template
from minisweagent import Environment, Model


# ============ Config & Exception Definitions ============

@dataclass
class AgentConfig:
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


class NonTerminatingException(Exception): ...
class FormatError(NonTerminatingException): ...
class ExecutionTimeoutError(NonTerminatingException): ...
class TerminatingException(Exception): ...
class Submitted(TerminatingException): ...
class LimitsExceeded(TerminatingException): ...
class TaskFailed(TerminatingException): ...


class BaseAgent(ABC):
    """Abstract base class for agents interacting with one or more environments using an LLM model."""

    @abstractmethod
    def __init__(
        self,
        model: Model,
        envs: Union[Environment, Dict[str, Environment]],
        *, 
        config_class: Callable = None,
        **kwargs
    ):
        """
        Initialize the agent with a model, one or more environments, and optional config.
        """
        pass

    @abstractmethod
    def get_env(self, name: str = None) -> Environment:
        """
        Retrieve an environment by name, or return the default one.
        """
        pass

    @abstractmethod
    def add_message(self, role: str, content: str, **kwargs):
        """
        Add a message (e.g., from user/assistant/system) to the message history.
        """
        pass

    @abstractmethod
    def run(self, task: str, **kwargs) -> Tuple[str, str]:
        """
        Run the agent on a given task until completion or termination.
        Returns a tuple of (exit_status, message).
        """
        pass

    @abstractmethod
    def step(self) -> Dict[str, Any]:
        """
        Perform one step: query the model, execute the action, and get the observation.
        """
        pass

    @abstractmethod
    def query(self) -> Dict[str, Any]:
        """
        Query the language model and return the raw response.
        """
        pass

    @abstractmethod
    def get_observation(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the parsed action in the environment and return the resulting observation.
        """
        pass

    @abstractmethod
    def parse_action(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse the model's response to extract the intended action.
        """
        pass

    @abstractmethod
    def execute_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the given action in the appropriate environment and return the output.
        """
        pass

    @abstractmethod
    def has_finished(self, output: Dict[str, Any]):
        """
        Check if the task is completed based on the output.
        Raise appropriate exceptions (e.g., Submitted, TaskFailed) if finished.
        """
        pass

    @abstractmethod
    def render_template(self, template: str, **kwargs) -> str:
        """
        Render a Jinja2 template string using available context (config, model, envs, etc.).
        """
        pass


class DefaultAgent(BaseAgent):
    """
        Supports parsing a command for a specific environment, executing it in the corresponding Docker container, and returning the output.
    """

    def __init__(
        self,
        model: Model,
        envs: Union[Environment, Dict[str, Environment]],
        *, 
        config_class: Callable = AgentConfig,
        **kwargs
    ):
        # super().__init__(model, envs, config_class=config_class, **kwargs)
        # --- backward compatibility ---
        if not isinstance(envs, dict):  # If not a dict, it is a single environment object
            envs = {"default": envs}  # Wrap as {"default": envs}

        if not envs:  # If dict is empty, raise an error
            raise ValueError("`envs` must be a non-empty dict of Environment objects or a single Environment instance.")

        self.model = model
        self.envs: Dict[str, Environment] = envs
        self.config = config_class(**kwargs)
        self.messages: list[dict] = []
        self.extra_template_vars = {}
        self.use_env_name = "default"

    # ---- Environment access helpers ----
    def get_env(self, name: str = None) -> Environment:
        """Get environment by name, or default to the first one."""
        if name is None:
            return next(iter(self.envs.values()))
        if name not in self.envs:
            raise KeyError(f"Environment '{name}' not found.")
        return self.envs[name]

    # ---- Message Handling ----
    def add_message(self, role: str, content: str, **kwargs):
        self.messages.append({"role": role, "content": content, **kwargs})

    # ---- Main loop ----
    def run(self, task: str, **kwargs) -> tuple[str, str]:
        """Run step() until agent is finished. Return exit status & message."""
        self.extra_template_vars |= {"task": task, **kwargs}
        self.messages = []
        self.add_message("system", self.render_template(self.config.system_template))
        self.add_message("user", self.render_template(self.config.instance_template))
        while True:
            try:
                self.step()
            except NonTerminatingException as e:
                self.add_message("user", str(e))
            except TerminatingException as e:
                self.add_message("user", str(e))
                return type(e).__name__, str(e)

    # ---- One iteration ----
    def step(self) -> dict:
        """Query the LM, execute the action, return the observation."""
        return self.get_observation(self.query())

    # ---- Query model ----
    def query(self) -> dict:
        """Query the model and return the response."""
        if 0 < self.config.step_limit <= self.model.n_calls or 0 < self.config.cost_limit <= self.model.cost:
            raise LimitsExceeded()
        response = self.model.query(self.messages)
        self.add_message("assistant", **response)
        return response

    # ---- Execute and observe ----
    def get_observation(self, response: dict) -> dict:
        """Execute the action in the chosen environment and return the observation."""
        output = self.execute_action(self.parse_action(response))
        observation = self.render_template(self.config.action_observation_template, output=output)
        self.add_message("user", observation)
        return output

    # ---- Parse model output ----
    def parse_action(self, response: dict) -> dict:
        """Parse the action from the message. Returns the action."""
        actions = re.findall(r"```bash\s*\n(.*?)\n```", response["content"], re.DOTALL)
        if len(actions) == 1:
            env_name = 'default'
            if '<env>' in actions[0]:
                try:
                    env_name = actions[0].split('<env>')[1].split('</env>')[0]
                    actions[0] = actions[0].split('</env>')[1]
                except:
                    env_name = 'default'

            return {"action": actions[0].strip(), 'env_name':env_name, **response}
        raise FormatError(self.render_template(self.config.format_error_template, actions=actions))

    # ---- Execute command ----
    def execute_action(self, action: dict) -> dict:
        """Execute the action in the specified environment (default first one)."""
        env_name = action.get('env_name', 'default')
        env = self.get_env(env_name)
        self.use_env_name = env_name
        try:
            output = env.execute(action["action"])
        except subprocess.TimeoutExpired as e:
            output = e.output.decode("utf-8", errors="replace") if e.output else ""
            raise ExecutionTimeoutError(
                self.render_template(self.config.timeout_template, action=action, output=output)
            )
        except TimeoutError:
            raise ExecutionTimeoutError(self.render_template(self.config.timeout_template, action=action, output=""))

        self.has_finished(output)
        return output

    # ---- Detect completion ----
    def has_finished(self, output: dict[str, str]):
        """Raises Submitted exception with final output if the agent has finished its task."""
        lines = output.get("output", "").lstrip().splitlines(keepends=True)
        if lines and lines[0].strip() in ["MINI_SWE_AGENT_FINAL_OUTPUT", "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]:
            raise Submitted("".join(lines[1:]))
        elif lines and lines[0].strip() == "GIVE_UP_TASK":
            raise TaskFailed("".join(lines[1:]))

    def render_template(self, template: str, **kwargs) -> str:
        """Merge template vars from config, model, and all envs."""
        env_vars = {}
        # Prefix variables when multiple envs exist
        for name, env in self.envs.items():
            prefix = f"{name}_" if len(self.envs) > 1 else ""
            env_vars |= {f"{prefix}{k}": v for k, v in env.get_template_vars().items()}

        template_vars = (
            asdict(self.config)
            | self.model.get_template_vars()
            | env_vars
        )
        return Template(template, undefined=StrictUndefined).render(
            **kwargs, **template_vars, **self.extra_template_vars
        )


class MultiEnvAgent(DefaultAgent):
    """
        Supports executing commands simultaneously across multiple environments, or executing different commands in different environments based on a special format.
    """

    def __init__(
        self,
        model: Model,
        envs: Union[Environment, Dict[str, Environment]],
        *, 
        config_class: Callable = AgentConfig,
        **kwargs
    ):
        # Call parent class __init__
        super().__init__(model, envs, config_class=config_class, **kwargs)
        # Add a dict to store the output of each environment
        self.current_env_outputs = {}

    # ---- Main loop ----
    def run(self, task: str, **kwargs) -> tuple[str, str]:
        """Run step() until agent is finished. Return exit status & message."""
        # Reset the environment output dict
        self.current_env_outputs = {}
        # Call parent class run method
        return super().run(task, **kwargs)

    # ---- Execute and observe ----
    def get_observation(self, response: dict) -> dict:
        """Execute the actions in the specified environments and return the observations."""
        action_data = self.parse_action(response)
        outputs = self.execute_action(action_data)
        # Create observation for each environment's output
        observations = []
        for env_name, output in outputs.items():
            # Render a separate template for each environment
            env_specific_vars = {
                'env_name': env_name,
                'output': output,
                **self.envs[env_name].get_template_vars()
            }
            observation = self.render_template(self.config.action_observation_template, **env_specific_vars)
            observations.append(f"[Environment: {env_name}]\n{observation}")
            # Save current environment output for use in render_template
            self.current_env_outputs[env_name] = output
        
        combined_observation = "\n\n".join(observations)
        self.add_message("user", combined_observation)
        return outputs

    # ---- Parse model output ----
    def parse_action(self, response: dict) -> dict:
        """Parse actions from the message. Supports multiple environment-specific actions or single action."""
        actions = re.findall(r"```bash\s*\n(.*?)\n```", response["content"], re.DOTALL)

        if not actions:
            raise FormatError("No valid bash code block found in the response.\nStanderd format: ```bash\n<command>\n```")

        action_text = actions[0]
        # Check if there are environment-specific commands for multiple envs
        env_action_pattern = r'<env>(.*?)</env>\s*(.*?)(?=(?:<env>|$))'
        matches = re.findall(env_action_pattern, action_text, re.DOTALL)
        
        if matches:
            # Handle environment-specific commands for multiple envs
            env_actions = []
            for env_name, cmd in matches:
                env_name = env_name.strip()
                # Strip trailing && from the command if present
                cmd = cmd.strip()
                if cmd.endswith('&&'):
                    cmd = cmd[:-2].strip()
                # Also strip trailing ; if present
                if cmd.endswith(';'):
                    cmd = cmd[:-1].strip()

                if env_name == 'All' and cmd:
                    return {"action": cmd, "type": "all", **response}
                
                if env_name and cmd:
                    env_actions.append((env_name, cmd))
            
            if env_actions:
                return {"actions": env_actions, "type": "multiple", **response}
        
        # Default: handle single environment command
        env_name = next(iter(self.envs))
        if '<env>' in action_text:
            try:
                env_name = action_text.split('<env>')[1].split('</env>')[0].strip()
                action_text = action_text.split('</env>')[1].strip()
            except:
                env_name = next(iter(self.envs))
        
        return {"action": action_text.strip(), "env_name": env_name, "type": "single", **response}

    # ---- Execute command ----
    def execute_action(self, action_data: dict) -> dict:
        """Execute actions in the specified environments and return combined outputs."""
        outputs = {}
        
        if action_data["type"] == "multiple":
            # Execute environment-specific commands for multiple envs
            for env_name, cmd in action_data["actions"]:
                try:
                    env = self.get_env(env_name)
                    outputs[env_name] = env.execute(cmd)
                except KeyError:
                    # If environment does not exist, add an error message
                    outputs[env_name] = {"output": f"Error: Environment '{env_name}' not found.", "returncode": -1}
                except subprocess.TimeoutExpired as e:
                    output = e.output.decode("utf-8", errors="replace") if e.output else ""
                    outputs[env_name] = {"output": f"Timeout: {output}", "returncode": -1}
                except TimeoutError:
                    outputs[env_name] = {"output": "Timeout: No output received.", "returncode": -1}
        
        elif action_data["type"] == "all":
            # Execute the same command in all environments
            cmd = action_data["action"]
            for env_name, env in self.envs.items():
                try:
                    outputs[env_name] = env.execute(cmd)
                except subprocess.TimeoutExpired as e:
                    output = e.output.decode("utf-8", errors="replace") if e.output else ""
                    outputs[env_name] = {"output": f"Timeout: {output}", "returncode": -1}
                except TimeoutError:
                    outputs[env_name] = {"output": "Timeout: No output received.", "returncode": -1}
        
        else:  # single
            # Execute single environment command, delegate to parent method
            try:
                env_name = action_data.get('env_name', 'default')
                env = self.get_env(env_name)
                outputs[env_name] = env.execute(action_data["action"])
            except KeyError:
                outputs[env_name] = {"output": f"Error: Environment '{env_name}' not found.", "returncode": -1}
            except subprocess.TimeoutExpired as e:
                output = e.output.decode("utf-8", errors="replace") if e.output else ""
                raise ExecutionTimeoutError(
                    self.render_template(self.config.timeout_template, action=action_data, output=output)
                )
            except TimeoutError:
                raise ExecutionTimeoutError(self.render_template(self.config.timeout_template, action=action_data, output=""))
        
        # Check if any output indicates task completion
        for env_name, output in outputs.items():
            try:
                self.has_finished(output)
            except TerminatingException:
                # If any environment signals task completion, propagate the exception
                raise
        
        return outputs

    # ---- Override render_template to include each environment's output ----
    def render_template(self, template: str, **kwargs) -> str:
        """Merge template vars from config, model, envs, and current environment outputs."""
        env_vars = {}
        # Prefix variables when multiple envs exist
        for name, env in self.envs.items():
            prefix = f"{name}_" if len(self.envs) > 1 else ""
            env_vars |= {f"{prefix}{k}": v for k, v in env.get_template_vars().items()}
            # Add the current environment's output
            if name in self.current_env_outputs:
                env_vars[f"{prefix}output"] = self.current_env_outputs[name]

        template_vars = (
            asdict(self.config)
            | self.model.get_template_vars()
            | env_vars
        )
        return Template(template, undefined=StrictUndefined).render(
            **kwargs, **template_vars, **self.extra_template_vars
        )






