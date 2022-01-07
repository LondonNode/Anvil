from typing import List, Optional, Type, Union

import numpy as np
import torch as T
from gym.vector.vector_env import VectorEnv

from anvilrl.agents.base_agents import BaseAgent
from anvilrl.buffers import RolloutBuffer
from anvilrl.buffers.base_buffer import BaseBuffer
from anvilrl.callbacks.base_callback import BaseCallback
from anvilrl.common.type_aliases import Log
from anvilrl.explorers.base_explorer import BaseExplorer
from anvilrl.models import ActorCritic, Dummy
from anvilrl.settings import (
    BufferSettings,
    CallbackSettings,
    CrossoverSettings,
    ExplorerSettings,
    LoggerSettings,
    MutationSettings,
    PopulationSettings,
    SelectionSettings,
)
from anvilrl.signal_processing import (
    crossover_operators,
    mutation_operators,
    selection_operators,
)
from anvilrl.updaters.evolution import BaseEvolutionUpdater, GeneticUpdater


def default_model(env: VectorEnv):
    """
    Returns a default model for the given environment.
    """
    actor = Dummy(space=env.single_action_space)
    critic = Dummy(space=env.single_action_space)

    return ActorCritic(
        actor=actor,
        critic=critic,
        population_settings=PopulationSettings(
            actor_population_size=env.num_envs, actor_distribution="uniform"
        ),
    )


class GA(BaseAgent):
    """
    Genetic Algorithm
    https://www.geeksforgeeks.org/genetic-algorithms/

    :param env: the gym-like environment to be used, should be a VectorEnv
    :param model: the neural network model
    :param updater_class: the updater class to be used
    :param selection_operator: the selection operator to be used
    :param selection_settings: the selection settings to be used
    :param crossover_operator: the crossover operator to be used
    :param crossover_settings: the crossover settings to be used
    :param mutation_operator: the mutation operator to be used
    :param mutation_settings: the mutation settings to be used
    :param elitism: the elitism ratio
    :param buffer_class: the buffer class for storing and sampling trajectories
    :param buffer_settings: settings for the buffer
    :param action_explorer_class: the explorer class for random search at beginning of training and
        adding noise to actions
    :param explorer settings: settings for the action explorer
    :param callbacks: an optional list of callbacks (e.g. if you want to save the model)
    :param callback_settings: settings for callbacks
    :param logger_settings: settings for the logger
    :param device: device to run on, accepts "auto", "cuda" or "cpu"
    :param render: whether to render the environment or not
    :param seed: optional seed for the random number generator
    """

    def __init__(
        self,
        env: VectorEnv,
        model: Optional[ActorCritic] = None,
        updater_class: Type[BaseEvolutionUpdater] = GeneticUpdater,
        selection_operator: selection_operators = selection_operators.roulette_selection,
        selection_settings: SelectionSettings = SelectionSettings(),
        crossover_operator: crossover_operators = crossover_operators.one_point_crossover,
        crossover_settings: CrossoverSettings = CrossoverSettings(),
        mutation_operator: mutation_operators = mutation_operators.uniform_mutation,
        mutation_settings: MutationSettings = MutationSettings(),
        elitism: float = 0.1,
        buffer_class: Type[BaseBuffer] = RolloutBuffer,
        buffer_settings: BufferSettings = BufferSettings(),
        action_explorer_class: Type[BaseExplorer] = BaseExplorer,
        explorer_settings: ExplorerSettings = ExplorerSettings(start_steps=0),
        callbacks: Optional[List[Type[BaseCallback]]] = None,
        callback_settings: Optional[List[CallbackSettings]] = None,
        logger_settings: LoggerSettings = LoggerSettings(),
        device: Union[T.device, str] = "auto",
        render: bool = False,
        seed: Optional[int] = None,
    ) -> None:
        model = model if model is not None else default_model(env)
        super().__init__(
            env=env,
            model=model,
            action_explorer_class=action_explorer_class,
            explorer_settings=explorer_settings,
            buffer_class=buffer_class,
            buffer_settings=buffer_settings,
            logger_settings=logger_settings,
            callbacks=callbacks,
            callback_settings=callback_settings,
            device=device,
            render=render,
            seed=seed,
        )

        self.updater = updater_class(self.model)

        self.selection_operator = selection_operator
        self.selection_settings = selection_settings.filter_none()
        self.crossover_operator = crossover_operator
        self.crossover_settings = crossover_settings.filter_none()
        self.mutation_operator = mutation_operator
        self.mutation_settings = mutation_settings.filter_none()
        self.elitism = elitism

    def _fit(
        self, batch_size: int, actor_epochs: int = 1, critic_epochs: int = 1
    ) -> Log:
        divergences = np.zeros(actor_epochs)
        entropies = np.zeros(actor_epochs)

        trajectories = self.buffer.sample(batch_size, flatten_env=False)
        rewards = trajectories.rewards.squeeze()
        if rewards.ndim > 1:
            rewards = rewards.sum(dim=-1)
        for i in range(actor_epochs):
            log = self.updater(
                rewards=rewards,
                selection_operator=self.selection_operator,
                crossover_operator=self.crossover_operator,
                mutation_operator=self.mutation_operator,
                selection_settings=self.selection_settings,
                crossover_settings=self.crossover_settings,
                mutation_settings=self.mutation_settings,
                elitism=self.elitism,
            )
            divergences[i] = log.divergence
            entropies[i] = log.entropy
        self.buffer.reset()

        return Log(divergence=divergences.sum(), entropy=entropies.mean())
