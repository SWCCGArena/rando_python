"""
Brain Package

The brain is responsible for decision-making logic separate from the game engine.
This allows for swappable AI implementations, personality systems, and testing.
"""

from .interface import (
    Brain,
    BrainContext,
    BrainDecision,
    DecisionRequest,
    DecisionOption,
    DecisionType,
    BoardState,
    GameHistory,
    CardInfo,
    LocationState,
    ZoneState,
)

from .static_brain import StaticBrain
from .command_handler import CommandHandler

__all__ = [
    'Brain',
    'BrainContext',
    'BrainDecision',
    'DecisionRequest',
    'DecisionOption',
    'DecisionType',
    'BoardState',
    'GameHistory',
    'CardInfo',
    'LocationState',
    'ZoneState',
    'StaticBrain',
    'CommandHandler',
]
