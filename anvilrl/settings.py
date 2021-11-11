import logging
from dataclasses import dataclass
from typing import Optional, Type, Union

import numpy as np
import torch as T
from torch.optim.optimizer import Optimizer

from anvilrl.common.enumerations import PopulationInitStrategy


@dataclass
class OptimizerSettings:
    """
    Settings for the model optimizers

    :param optimizer_class: class of optimizer algorithm to use
    :param learning_rate: optimizer learning rate
    :param max_grad: maximum gradient for gradient clipping
    """

    optimizer_class: Type[Optimizer] = T.optim.Adam
    learning_rate: float = 1e-3
    max_grad: float = 0.5


@dataclass
class PopulationInitializerSettings:
    """
    Settings for the population initializer

    :param population_init_strategy: strategy for population initialization, accepts 'normal' or 'uniform'
    :param population_std: std for population initialization (only used if strategy is 'normal')
    :param starting_point: central starting point of the population (only used if strategy is 'normal')
    """

    strategy: Union[str, PopulationInitStrategy]
    population_std: Optional[Union[float, np.ndarray]] = 1
    starting_point: Optional[np.ndarray] = None


@dataclass
class ExplorerSettings:
    """
    Settings for the action explorer

    :param start_steps: number of steps at the start to randomly sample actions (encourages exploration)
    :param scale: std of noise to add to actions (not always applicable)
    """

    start_steps: int = 1000
    scale: Optional[float] = None


@dataclass
class BufferSettings:
    """
    Settings for buffers

    :buffer_size: max number of transitions to store at once in each environment
    """

    buffer_size: int = int(1e6)


@dataclass
class CallbackSettings:
    """
    Settings for callbacks, pick which ones apply!

    :param save_freq: how often to save
    :param save_path: path to save to
    :name_prefix: prefix of the model file name
    """

    save_freq: Optional[int] = None
    save_path: Optional[str] = None
    name_prefix: Optional[str] = None


@dataclass
class LoggerSettings:
    """
    Settings for the Logger

    :param tensorboard_log_path: path to store the tensorboard log
    :param file_handler_level: logging level for the file log
    :param stream_handler_level: logging level for the streaming log
    :param verbose: whether to record any logs at all
    """

    tensorboard_log_path: Optional[str] = None
    file_handler_level: int = logging.DEBUG
    stream_handler_level: int = logging.INFO
    verbose: bool = True
