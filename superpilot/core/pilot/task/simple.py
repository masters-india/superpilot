import logging
import platform
import time
from abc import ABC
from typing import Dict

from superpilot.core.pilot.task.base import TaskPilot, TaskPilotConfiguration
from superpilot.core.plugin.simple import PluginLocation, PluginStorageFormat
import distro
from superpilot.core.planning.base import PromptStrategy
from superpilot.core.planning.strategies.simple import SimplePrompt
from superpilot.core.planning.schema import (
    LanguageModelResponse,
    ExecutionNature,
    Task,
)
from superpilot.core.planning.settings import (
    LanguageModelConfiguration,
    LanguageModelClassification,
    PromptStrategyConfiguration,
)
from superpilot.core.resource.model_providers import (
    LanguageModelProvider,
    ModelProviderName,
    OpenAIModelName,
    OpenAIProvider,
)


class SimpleTaskPilot(TaskPilot, ABC):
    """A class representing a pilot step."""

    default_configuration = TaskPilotConfiguration(
        location=PluginLocation(
            storage_format=PluginStorageFormat.INSTALLED_PACKAGE,
            storage_route="superpilot.core.flow.simple.SuperTaskPilot",
        ),
        execution_nature=ExecutionNature.SIMPLE,
        prompt_strategy=SimplePrompt.default_configuration,
        models={
            LanguageModelClassification.FAST_MODEL: LanguageModelConfiguration(
                model_name=OpenAIModelName.GPT3,
                provider_name=ModelProviderName.OPENAI,
                temperature=0.9,
            ),
            LanguageModelClassification.SMART_MODEL: LanguageModelConfiguration(
                model_name=OpenAIModelName.GPT4,
                provider_name=ModelProviderName.OPENAI,
                temperature=0.9,
            ),
        },
    )

    def __init__(
        self,
        configuration: TaskPilotConfiguration = default_configuration,
        model_providers: Dict[ModelProviderName, LanguageModelProvider] = None,
        logger: logging.Logger = logging.getLogger(__name__),
    ) -> None:
        self._logger = logger
        self._configuration = configuration
        self._execution_nature = configuration.execution_nature

        self._providers: Dict[LanguageModelClassification, LanguageModelProvider] = {}
        for model, model_config in self._configuration.models.items():
            self._providers[model] = model_providers[model_config.provider_name]

        self._prompt_strategy = SimplePrompt(
            **self._configuration.prompt_strategy.dict()
        )

    async def execute(self, objective: str, *args, **kwargs) -> LanguageModelResponse:
        """Execute the task."""
        self._logger.debug(f"Executing task: {objective}")
        task = Task.factory(objective, **kwargs)
        context_res = await self.exec_task(task, **kwargs)
        return context_res

    async def exec_task(self, task: Task, **kwargs) -> LanguageModelResponse:
        template_kwargs = task.generate_kwargs()
        template_kwargs.update(kwargs)
        return await self.chat_with_model(
            self._prompt_strategy,
            **template_kwargs,
        )

    async def chat_with_model(
        self,
        prompt_strategy: PromptStrategy,
        **kwargs,
    ) -> LanguageModelResponse:
        model_classification = prompt_strategy.model_classification
        model_configuration = self._configuration.models[model_classification].dict()
        self._logger.debug(f"Using model configuration: {model_configuration}")
        del model_configuration["provider_name"]
        provider = self._providers[model_classification]

        template_kwargs = self._make_template_kwargs_for_strategy(prompt_strategy)
        template_kwargs.update(kwargs)
        prompt = prompt_strategy.build_prompt(**template_kwargs)

        self._logger.debug(f"Using prompt:\n{prompt}\n\n")
        response = await provider.create_language_completion(
            model_prompt=prompt.messages,
            functions=prompt.functions,
            function_call=prompt.get_function_call(),
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
        return f"{self.__class__.__name__}()"

    @classmethod
    def factory(
        cls,
        prompt_strategy: PromptStrategyConfiguration = None,
        model_providers: Dict[ModelProviderName, LanguageModelProvider] = None,
        execution_nature: ExecutionNature = None,
        models: Dict[LanguageModelClassification, LanguageModelConfiguration] = None,
        location: PluginLocation = None,
        logger: logging.Logger = None,
    ) -> "SimpleTaskPilot":
        # Initialize settings
        config = cls.default_configuration
        if location is not None:
            config.location = location
        if execution_nature is not None:
            config.execution_nature = execution_nature
        if prompt_strategy is not None:
            config.prompt_strategy = prompt_strategy
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
        return cls(configuration=config, model_providers=model_providers, logger=logger)


def get_os_info() -> str:
    os_name = platform.system()
    os_info = (
        platform.platform(terse=True)
        if os_name != "Linux"
        else distro.name(pretty=True)
    )
    return os_info
