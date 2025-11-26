"""
Persistence Module

SQLite database for tracking:
- Player stats and achievements
- Deck route scores (Astrogator meta-game)
- Global records
- Game history
"""

from .models import (
    GlobalStats,
    DeckStats,
    PlayerStats,
    Achievement,
    GameHistory,
    ChatMessage,
)
from .database import init_db, get_session, session_scope, close_db
from .stats_repository import StatsRepository

__all__ = [
    # Models
    'GlobalStats',
    'DeckStats',
    'PlayerStats',
    'Achievement',
    'GameHistory',
    'ChatMessage',
    # Database
    'init_db',
    'get_session',
    'session_scope',
    'close_db',
    # Repository
    'StatsRepository',
]
