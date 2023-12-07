import asyncio
import logging
import platform
import time
from typing import List, Dict

from superpilot.core.ability import SuperAbilityRegistry, AbilityAction
from superpilot.core.pilot.task.base import TaskPilot, TaskPilotConfiguration
from superpilot.core.context.schema import Context
from superpilot.core.ability.base import AbilityRegistry, Ability
from superpilot.core.plugin.simple import (
    PluginLocation,
    PluginStorageFormat,
    SimplePluginService,
)
import distro
from superpilot.core.planning.base import PromptStrategy
from superpilot.core.planning import strategies
from superpilot.core.planning.schema import (
    LanguageModelResponse,
    ExecutionNature,
    Task, TaskStatus,
)
from superpilot.core.planning.settings import (
    LanguageModelConfiguration,
    LanguageModelClassification,
    PromptStrategiesConfiguration, PromptStrategyConfiguration,
)
from superpilot.core.resource.model_providers import (
    LanguageModelProvider,
    ModelProviderName,
    OpenAIModelName, OpenAIProvider,
)
from superpilot.core.pilot.settings import (
    PilotConfiguration,
    ExecutionAlgo
)
from superpilot.core.resource.model_providers.factory import ModelProviderFactory, ModelConfigFactory
from superpilot.core.environment import Environment, SimpleEnv
from superpilot.core.plugin.utlis import load_class


class SuperTaskPilot(TaskPilot):

    default_configuration = TaskPilotConfiguration(
        location=PluginLocation(
            storage_format=PluginStorageFormat.INSTALLED_PACKAGE,
            storage_route="superpilot.core.flow.simple.SuperTaskPilot",
        ),
        pilot=PilotConfiguration(
            name="super_task_pilot",
            role=(
                "An AI Pilot designed to complete simple tasks with "
            ),
            goals=[
                "Complete simple tasks",
            ],
            cycle_count=0,
            max_task_cycle_count=3,
            creation_time="",
            execution_algo=ExecutionAlgo.PLAN_AND_EXECUTE,
        ),
        execution_nature=ExecutionNature.AUTO,
        prompt_strategy=strategies.NextAbility.default_configuration,
        models={
            LanguageModelClassification.FAST_MODEL: LanguageModelConfiguration(
                model_name=OpenAIModelName.GPT3,
                provider_name=ModelProviderName.OPENAI,
                temperature=0.9,
            ),
            LanguageModelClassification.SMART_MODEL: LanguageModelConfiguration(
                model_name=OpenAIModelName.GPT4_TURBO,
                provider_name=ModelProviderName.OPENAI,
                temperature=0.9,
            ),
        },
    )

    def __init__(
        self,
        ability_registry: AbilityRegistry,
        model_providers: Dict[ModelProviderName, LanguageModelProvider],
        configuration: TaskPilotConfiguration = default_configuration,
        logger: logging.Logger = logging.getLogger(__name__),
    ) -> None:
        self._logger = logger
        self._configuration = configuration
        self._execution_nature = configuration.execution_nature
        self._ability_registry = ability_registry

        self._providers: Dict[LanguageModelClassification, LanguageModelProvider] = {}
        for model, model_config in self._configuration.models.items():
            self._providers[model] = model_providers[model_config.provider_name]

        prompt_config = self._configuration.prompt_strategy.dict()
        location = prompt_config.pop("location", None)
        if location is not None:
            self._prompt_strategy = load_class(location, prompt_config)
        else:
            self._prompt_strategy = strategies.NextAbility(**prompt_config)

        self._task_queue = []
        self._completed_tasks = []
        self._current_task: Task = None
        self._current_observation = None
        self._next_step = None
        self._current_context = Context()

    async def execute(self, objective: str | Task, *args, **kwargs) -> Context:
        """Execute the task."""
        self._logger.debug(f"Executing task: {objective}")
        if isinstance(objective, str):
            task = Task.factory(objective, **kwargs)
        else:
            task = objective

        self._current_task = task
        self._current_context = kwargs.get("context", self._current_context)
        if len(args) > 0:
            kwargs["context"] = args[0]

        while self._current_task != TaskStatus.DONE:
            ability_actions = await self.exec_abilities(self._current_task, **kwargs)

        return self._current_context

    async def exec_abilities(self, task: Task, **kwargs) -> List[AbilityAction]:
        ability_actions = []
        if self._execution_nature == ExecutionNature.PARALLEL:
            tasks = [
                self.perform_ability(task, [ability.dump()], **kwargs)
                for ability in self._ability_registry.abilities()
            ]
            res_list = await asyncio.gather(*tasks)
            for response in res_list:
                ability_actions.append(response)
        elif self._execution_nature == ExecutionNature.AUTO:
            ability_actions.append(await self.perform_ability(
                task, self._ability_registry.dump_abilities(), **kwargs
            ))
        else:
            # Execute for Sequential nature
            for ability in self._ability_registry.abilities():
                # print(res.content)
                ability_actions.append(await self.perform_ability(
                    task, [ability.dump()], **kwargs
                ))
                # TODO add context to task prior actions as ability action.
                # task.
        return ability_actions

    async def perform_ability(
        self, task: Task, ability_schema: List[dict], **kwargs
    ) -> Context:
        if self._execution_nature == ExecutionNature.AUTO:
            response = await self.determine_next_ability(
                task, ability_schema, **kwargs
            )
        else:
            response = await self.determine_exec_ability(
                task, ability_schema, **kwargs
            )
        ability_args = response.content.get("ability_arguments", {})
        ability_action = await self._ability_registry.perform(
            response.content["next_ability"], **ability_args
        )
        await self._update_tasks_and_memory(ability_action)
        if self._current_task.context.status == TaskStatus.DONE:
            self._completed_tasks.append(self._current_task)
        else:
            self._task_queue.append(self._current_task)
        # self._current_task = None  #TODO : Check if this is required
        self._next_step = None
        return ability_action

    async def _update_tasks_and_memory(self, ability_result: AbilityAction):
        self._current_task.context.cycle_count += 1
        self._current_task.context.prior_actions.append(ability_result)
        # TODO: Summarize new knowledge
        # TODO: store knowledge and summaries in memory and in relevant tasks
        # TODO: evaluate whether the task is complete
        # TODO update memory with the facts, insights and knowledge
        # self._current_task.context.add_memory(ability_action.knowledge)

        # update goal status
        # final_response = await self.analyze_goal_status(ability_action)

        self._logger.info(f"Final response: {ability_result}")

        self._current_task.context.status = TaskStatus.DONE
        self._current_task.context.enough_info = True
        self._current_task.context.memories = ability_result.get_memories()
        print("Ability result", ability_result.result)

        # TaskStore.save_task_with_status(self._current_goal, status, stage)

    async def determine_exec_ability(
        self, task: Task, ability_schema: List[dict], **kwargs
    ) -> LanguageModelResponse:
        return await self.chat_with_model(
            self._prompt_strategy,
            task=task,
            ability_schema=ability_schema,
            **kwargs,
        )

    async def determine_next_ability(
        self, task: Task, ability_schema: List[dict], **kwargs
    ) -> LanguageModelResponse:
        return await self.chat_with_model(
            self._prompt_strategy,
            task=task,
            ability_schema=ability_schema,
            **kwargs,
        )

    async def chat_with_model(
        self,
        prompt_strategy: PromptStrategy,
        **kwargs,
    ) -> LanguageModelResponse:
        model_classification = prompt_strategy.model_classification
        model_configuration = self._configuration.models[model_classification].dict()
        self._logger.debug(f"Using model configuration: {model_configuration}")
        print(f"Using model configuration: {model_configuration}")
        del model_configuration["provider_name"]
        provider = self._providers[model_classification]

        template_kwargs = self._make_template_kwargs_for_strategy(prompt_strategy)
        template_kwargs.update(kwargs)
        prompt = prompt_strategy.build_prompt(**template_kwargs)

        self._logger.debug(f"Using prompt:\n{prompt}\n\n")
        print(f"Using prompt:\n{prompt}\n\n")
        response = await provider.create_language_completion(
            model_prompt=prompt.messages,
            functions=prompt.functions,
            **model_configuration,
            completion_parser=prompt_strategy.parse_response_content,
        )
        return LanguageModelResponse.parse_obj(response.dict())

    def _make_template_kwargs_for_strategy(self, strategy: PromptStrategy):
        provider = self._providers[strategy.model_classification]
        template_kwargs = {
            "os_info": get_os_info(),
            "api_budget": provider.get_remaining_budget(),
            "current_time": time.strftime("%c"),
        }
        return template_kwargs

    def __repr__(self):
        return f"SuperTaskPilot({self._configuration})"

    def name(self) -> str:
        """The name of the ability."""
        return self._configuration.pilot.name

    def dump(self) -> dict:
        pilot_config = self._configuration.pilot
        dump = "PilotName: " + pilot_config.name + "\n"
        dump += "PilotRole: " + pilot_config.role + "\n"
        dump += "PilotGoals: " + "\n".join(pilot_config.goals) + "\n"
        return dump

    @classmethod
    def factory(
            cls,
            ability_registry: AbilityRegistry,
            prompt_strategy: PromptStrategyConfiguration = None,
            model_providers: Dict[ModelProviderName, LanguageModelProvider] = None,
            execution_nature: ExecutionNature = None,
            models: Dict[LanguageModelClassification, LanguageModelConfiguration] = None,
            pilot_config: PilotConfiguration = None,
            location: PluginLocation = None,
            logger: logging.Logger = None,
            **kwargs
    ) -> "SuperTaskPilot":
        # Initialize settings
        config = cls.default_configuration.copy()
        if location is not None:
            config.location = location
        if execution_nature is not None:
            config.execution_nature = execution_nature
        if prompt_strategy is not None:
            config.prompt_strategy = prompt_strategy
        if pilot_config is not None:
            config.pilot = pilot_config
        if models is not None:
            config.models = models

        # Use default logger if not provided
        if logger is None:
            logger = logging.getLogger(__name__)

        # Use empty dictionary for model_providers if not provided
        if model_providers is None:
            # Load Model Providers
            open_ai_provider = OpenAIProvider.factory()
            model_providers = {ModelProviderName.OPENAI: open_ai_provider}

        # Create and return SimpleTaskPilot instance
        return cls(
            ability_registry=ability_registry,
            configuration=config, model_providers=model_providers, logger=logger)

    @classmethod
    def create(cls,
               prompt_config=None,
               smart_model_name=OpenAIModelName.GPT4,
               fast_model_name=OpenAIModelName.GPT3,
               smart_model_temp=0.9,
               fast_model_temp=0.9,
               model_providers=None,
               pilot_config=None,
               abilities: List[Ability] = None,
               environment: Environment = None,
               **kwargs
               ):

        models_config = ModelConfigFactory.get_models_config(
            smart_model_name=smart_model_name,
            fast_model_name=fast_model_name,
            smart_model_temp=smart_model_temp,
            fast_model_temp=fast_model_temp,
        )
        if model_providers is None:
            model_providers = ModelProviderFactory.load_providers()

        if environment is None:
            environment = SimpleEnv.create({})

        ability_registry = None
        if abilities is not None:
            allowed_abilities = {}
            for ability in abilities:
                allowed_abilities[ability.name()] = ability.default_configuration
            ability_registry = SuperAbilityRegistry.factory(
                environment, allowed_abilities
            )

        pilot = cls.factory(
            ability_registry=ability_registry,
            prompt_strategy=prompt_config,
            model_providers=model_providers,
            models=models_config,
            pilot_config=pilot_config,
            **kwargs
        )
        return pilot


def get_os_info() -> str:
    os_name = platform.system()
    os_info = (
        platform.platform(terse=True)
        if os_name != "Linux"
        else distro.name(pretty=True)
    )
    return os_info
