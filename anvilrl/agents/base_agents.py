from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple, Type, Union

import numpy as np
import torch as T
from gym import Env
from gym.vector import VectorEnv

from anvilrl.buffers.base_buffer import BaseBuffer
from anvilrl.callbacks.base_callback import BaseCallback
from anvilrl.common.enumerations import PopulationInitStrategy, TrainFrequencyType
from anvilrl.common.logging_ import Logger
from anvilrl.common.type_aliases import Log, Observation, Tensor, Trajectories
from anvilrl.common.utils import get_device
from anvilrl.explorers.base_explorer import BaseExplorer
from anvilrl.models.actor_critics import ActorCritic
from anvilrl.settings import (
    BufferSettings,
    CallbackSettings,
    ExplorerSettings,
    LoggerSettings,
    PopulationInitializerSettings,
)
from anvilrl.updaters.evolution import BaseEvolutionUpdater


class BaseDeepAgent(ABC):
    def __init__(
        self,
        env: Env,
        model: ActorCritic,
        action_explorer_class: Type[BaseExplorer] = BaseExplorer,
        explorer_settings: ExplorerSettings = ExplorerSettings(),
        buffer_class: BaseBuffer = BaseBuffer,
        buffer_settings: BufferSettings = BufferSettings(),
        logger_settings: LoggerSettings = LoggerSettings(),
        callbacks: Optional[List[Type[BaseCallback]]] = None,
        callback_settings: Optional[List[CallbackSettings]] = None,
        device: Union[str, T.device] = "auto",
        render: bool = False,
    ) -> None:
        """
        The BaseDeepAgent class is given to handle all the stuff around the actual Deep RL algorithm.
        It's recommended to inherit this class when implementing your own Deep RL agent. You'll need
        to implement the _fit() abstract method and override the __init__ to add updaters along with
        it's settings.

        See the example deep agents already done for guidance and settings.py for settings objects
        that can be used.

        :param env: the gym-like environment to be used
        :param model: the neural network model
        :param action_explorer_class: the explorer class for random search at beginning of training and
            adding noise to actions
        :param explorer settings: settings for the action explorer
        :param buffer_class: the buffer class for storing and sampling trajectories
        :param buffer_settings: settings for the buffer
        :param logger_settings: settings for the logger
        :param callbacks: an optional list of callbacks (e.g. if you want to save the model)
        :param callback_settings: settings for callbacks
        :param device: device to run on, accepts "auto", "cuda" or "cpu"
        :param render: whether to render the environment or not
        """

        self.env = env
        self.model = model
        self.render = render
        explorer_settings = explorer_settings.filter_none()
        self.action_explorer = action_explorer_class(
            action_space=env.action_space, **explorer_settings
        )
        buffer_settings = buffer_settings.filter_none()
        self.buffer = buffer_class(env=env, device=device, **buffer_settings)
        self.step = 0
        self.episode = 0
        self.done = False  # Flag terminate training
        self.logger = Logger(
            tensorboard_log_path=logger_settings.tensorboard_log_path,
            file_handler_level=logger_settings.file_handler_level,
            stream_handler_level=logger_settings.stream_handler_level,
            verbose=logger_settings.verbose,
            num_envs=env.num_envs if isinstance(env, VectorEnv) else 1,
        )
        if callbacks is not None:
            assert len(callbacks) == len(
                callback_settings
            ), "There should be a CallbackSetting object for each callback"
            callback_settings = [setting.filter_none() for setting in callback_settings]
            self.callbacks = [
                callback(self.logger, self.model, **settings)
                for callback, settings in zip(callbacks, callback_settings)
            ]
        else:
            self.callbacks = None

        device = get_device(device)
        self.logger.info(f"Using device {device}")

    def predict(self, observations: Union[Tensor, Dict[str, Tensor]]) -> T.Tensor:
        """Run the agent actor model"""
        self.model.eval()
        return self.model(observations)

    def get_action_distribution(
        self, observations: Union[Tensor, Dict[str, Tensor]]
    ) -> T.distributions.Distribution:
        """Get the policy distribution given an observation"""
        self.model.eval()
        return self.model.get_action_distribution(observations)

    def critic(
        self,
        observations: Union[Tensor, Dict[str, Tensor]],
        actions: Optional[Tensor] = None,
    ) -> T.Tensor:
        """Run the agent critic model"""
        self.model.eval()
        return self.model.critic(observations, actions)

    def step_env(self, observation: Observation, num_steps: int = 1) -> np.ndarray:
        """
        Step the agent in the environment

        :param observation: the starting observation to step from
        :param num_steps: how many steps to take
        :return: the final observation after all steps have been done
        """
        self.model.eval()
        for _ in range(num_steps):
            if self.render:
                self.env.render()
            action = self.action_explorer(self.model, observation, self.step)
            next_observation, reward, done, _ = self.env.step(action)
            self.buffer.add_trajectory(
                observation, action, reward, next_observation, done
            )
            self.logger.debug(
                f"{Trajectories(observation, action, reward, next_observation, done)}"
            )
            # Add reward to current episode log
            self.logger.add_reward(reward)
            # Get indices of episodes that are done, especially useful for vectorized environments
            done_indices = np.where(done)[0]

            if isinstance(self.env, VectorEnv):
                not_done_indices = np.where(~done)[0]
                observation[done_indices] = self.env.reset()[done_indices]
                observation[not_done_indices] = next_observation[not_done_indices]
            else:
                observation = self.env.reset() if done else next_observation

            # For vectorized environments, we keep track of individual episodes as they finish
            # also applies to single environments
            self.logger.episode_dones[done_indices] = True
            # If all environment episodes are done, we write an episode log and reset it.
            if all(self.logger.episode_dones):
                self.logger.write_log(self.step)
                self.logger.reset_episode_log()
                self.episode += 1

            if self.callbacks is not None:
                if not all(
                    [callback.on_step(self.step) for callback in self.callbacks]
                ):
                    self.done = True
                    break
            self.step += 1
        return observation

    @abstractmethod
    def _fit(
        self, batch_size: int, actor_epochs: int = 1, critic_epochs: int = 1
    ) -> Log:
        """
        Train the agent in the environment

        :param batch_size: minibatch size to make a single gradient descent step on
        :param actor_epochs: how many times to update the actor network in each training step
        :param critic_epochs: how many times to update the critic network in each training step
        :return: a Log object with training diagnostic info
        """

    def fit(
        self,
        num_steps: int,
        batch_size: int,
        actor_epochs: int = 1,
        critic_epochs: int = 1,
        train_frequency: Tuple[str, int] = ("step", 1),
    ) -> None:
        """
        Train the agent in the environment

        :param num_steps: total number of environment steps to train over
        :param batch_size: minibatch size to make a single gradient descent step on
        :param actor_epochs: how many times to update the actor network in each training step
        :param critic_epochs: how many times to update the critic network in each training step
        :param train_frequency: the number of steps or episodes to run before running a training step.
            To run every n episodes, use `("episode", n)`.
            To run every n steps, use `("step", n)`.
        """
        train_frequency = (
            TrainFrequencyType(train_frequency[0].lower()),
            train_frequency[1],
        )
        # We can pre-calculate how many training steps to run if train_frequency is in steps rather than episodes
        if train_frequency[0] == TrainFrequencyType.STEP:
            num_steps = num_steps // train_frequency[1]

        observation = self.env.reset()
        for step in range(num_steps):
            # Always fill buffer with enough samples for first training step
            if step == 0:
                observation = self.step_env(
                    observation=observation, num_steps=batch_size
                )
            # Step for number of steps specified
            elif train_frequency[0] == TrainFrequencyType.STEP:
                observation = self.step_env(
                    observation=observation, num_steps=train_frequency[1]
                )
            # Step for number of episodes specified
            elif train_frequency[0] == TrainFrequencyType.EPISODE:
                start_episode = self.episode
                end_episode = start_episode + train_frequency[1]
                while self.episode != end_episode:
                    observation = self.step_env(observation=observation)
                if self.step >= num_steps:
                    break

            if self.done:
                break

            self.model.train()
            train_log = self._fit(
                batch_size=batch_size,
                actor_epochs=actor_epochs,
                critic_epochs=critic_epochs,
            )

            self.logger.add_train_log(train_log)


class BaseEvolutionAgent(ABC):
    """
    The BaseEvolutionAgent class is given to handle all the stuff around the actual random search
    algorithm. It's recommended to inherit this class when implementing your own random search
    agent. You'll need to implement the _fit() abstract method and override the __init__ to add
    any extra hyperparameters.

    See the example random search agents already done for guidance and settings.py for settings
    objects that can be used. It should be noted that random search algorithms are generally
    formatted as maximization algorithms rather than minimization, and this is reflected in the
    implemented `updaters`.

    :param env: the gym vecotrized environment
    :param updater_class: the class to use for the updater handling the actual update algorithm
    :param population_settings: the settings object for population initialization
    :param buffer_class: the buffer class for storing and sampling trajectories
    :param buffer_settings: settings for the buffer
    :param logger_settings: settings for the logger
    :param callbacks: a list of callbacks to be called at certain points in the training process
    :param callbacks_settings: settings for the callbacks
    :param device: device to run on, accepts "auto", "cuda" or "cpu" (needed to pass to buffer,
        can mostly be ignored)
    """

    def __init__(
        self,
        env: VectorEnv,
        updater_class: Type[BaseEvolutionUpdater],
        population_settings: PopulationInitializerSettings = PopulationInitializerSettings(),
        buffer_class: BaseBuffer = BaseBuffer,
        buffer_settings: BufferSettings = BufferSettings(),
        logger_settings: LoggerSettings = LoggerSettings(),
        callbacks: Optional[List[Type[BaseCallback]]] = None,
        callback_settings: Optional[List[CallbackSettings]] = None,
        device: Union[str, T.device] = "auto",
    ) -> None:
        self.env = env
        self.updater = updater_class(env=env)
        self.population_settings = population_settings
        buffer_settings = buffer_settings.filter_none()
        self.buffer = buffer_class(env=env, device=device, **buffer_settings)
        self.step = 0
        self.episode = 0
        self.logger = Logger(
            tensorboard_log_path=logger_settings.tensorboard_log_path,
            file_handler_level=logger_settings.file_handler_level,
            stream_handler_level=logger_settings.stream_handler_level,
            verbose=logger_settings.verbose,
            num_envs=env.num_envs,
        )
        self.population = None

        if callbacks is not None:
            assert len(callbacks) == len(
                callback_settings
            ), "There should be a CallbackSetting object for each callback"
            callback_settings = [setting.filter_none() for setting in callback_settings]
            self.callbacks = [
                callback(self.logger, self.model, **settings)
                for callback, settings in zip(callbacks, callback_settings)
            ]
        else:
            self.callbacks = None

        device = get_device(device)
        self.logger.info(f"Using device {device}")

    def step_env(self, num_steps: int = 1) -> None:
        """
        Step the agent in the environment

        :param num_steps: how many steps to take
        """
        for _ in range(num_steps):
            _, rewards, dones, _ = self.env.step(self.population)
            self.buffer.add_trajectory(
                observation=self.env.observation_space.sample(),
                action=self.population,
                reward=rewards,
                next_observation=self.env.observation_space.sample(),
                done=dones,
            )
            if self.callbacks is not None:
                if not all(
                    [callback.on_step(self.step) for callback in self.callbacks]
                ):
                    self.done = True
                    break
            self.step += 1

    def evaluate_agent(self) -> None:
        """
        Evaluate the agent and write a log
        """
        trajectories = self.buffer.all()
        max_reward = np.max(trajectories.rewards)
        self.logger.add_reward(max_reward)
        self.logger.write_log(self.step)
        self.logger.reset_episode_log()

    @abstractmethod
    def _fit(self) -> Log:
        """
        Update the agent

        :return: a Log object with training diagnostic info
        """

    def fit(
        self,
        num_steps: int,
        train_frequency: Tuple[str, int] = ("step", 1),
    ):
        """
        Train the agent in the environment

        :param num_steps: total number of environment steps to train over
        :param train_frequency: the number of steps or episodes to run before running a training step.
            To run every n episodes, use `("episode", n)`.
            To run every n steps, use `("step", n)`.
        """
        train_frequency = (
            TrainFrequencyType(train_frequency[0].lower()),
            train_frequency[1],
        )
        # We can pre-calculate how many training steps to run if train_frequency is in steps rather than episodes
        if train_frequency[0] == TrainFrequencyType.STEP:
            num_steps = num_steps // train_frequency[1]

        if isinstance(self.population_settings.strategy, str):
            population_init_strategy = PopulationInitStrategy(
                self.population_settings.strategy.lower()
            )
        else:
            population_init_strategy = self.population_settings.strategy
        self.population = self.updater.initialize_population(
            population_init_strategy=population_init_strategy,
            population_std=self.population_settings.population_std,
            starting_point=self.population_settings.starting_point,
        )
        for _ in range(num_steps):
            # Step for number of steps specified
            if train_frequency[0] == TrainFrequencyType.STEP:
                self.step_env(num_steps=train_frequency[1])
            # Step for number of episodes specified
            elif train_frequency[0] == TrainFrequencyType.EPISODE:
                start_episode = self.episode
                end_episode = start_episode + train_frequency[1]
                while self.episode != end_episode:
                    self.step_env()
                if self.step >= num_steps:
                    break

            self.evaluate_agent()
            log = self._fit()
            self.population = self.updater.population
            self.logger.add_train_log(log)
