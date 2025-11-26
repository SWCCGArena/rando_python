"""
Database Models for Rando Cal Bot

Tracks:
- Global high scores across all players
- Per-deck route scores (Astrogator meta-game)
- Per-opponent stats and achievements
- Game history log
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Float,
    UniqueConstraint, Index, create_engine
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class GlobalStats(Base):
    """
    Global high scores across all players.

    Tracks records like:
    - Fastest game time
    - Highest force remaining
    - Most damage in single battle
    - Top astrogation score
    """
    __tablename__ = 'global_stats'

    id = Column(Integer, primary_key=True)
    stat_type = Column(String(50), nullable=False, unique=True)
    value = Column(Integer, default=0)
    player_name = Column(String(100))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<GlobalStats({self.stat_type}={self.value} by {self.player_name})>"


class DeckStats(Base):
    """
    Per-deck high scores for the Astrogator meta-game.

    Each deck has a "best route score" that players compete to beat.
    """
    __tablename__ = 'deck_stats'

    id = Column(Integer, primary_key=True)
    deck_name = Column(String(200), nullable=False, unique=True)
    best_score = Column(Integer, default=0)
    best_player = Column(String(100))
    games_played = Column(Integer, default=0)
    total_score = Column(Integer, default=0)  # Sum of all route scores
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<DeckStats({self.deck_name}: {self.best_score} by {self.best_player})>"


class PlayerStats(Base):
    """
    Per-opponent statistics and cumulative scores.

    Tracks everything about a specific opponent across all games.
    """
    __tablename__ = 'player_stats'

    id = Column(Integer, primary_key=True)
    player_name = Column(String(100), nullable=False, unique=True)

    # Win/loss record
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    games_played = Column(Integer, default=0)

    # Astrogator cumulative score
    total_ast_score = Column(Integer, default=0)
    best_route_score = Column(Integer, default=0)

    # Personal bests
    best_damage = Column(Integer, default=0)
    best_force_remaining = Column(Integer, default=0)
    best_time_seconds = Column(Integer, default=0)

    # Achievement tracking
    achievement_count = Column(Integer, default=0)

    # Timestamps
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Index for quick lookup
    __table_args__ = (
        Index('idx_player_name', 'player_name'),
    )

    def __repr__(self):
        return f"<PlayerStats({self.player_name}: {self.wins}W/{self.losses}L)>"

    @property
    def win_rate(self) -> float:
        """Calculate win rate as percentage"""
        if self.games_played == 0:
            return 0.0
        return (self.wins / self.games_played) * 100


class PlayerDeckStats(Base):
    """
    Per-player-per-deck statistics.

    Tracks each player's best score on each deck they've played.
    This matches the C# behavior where {deckName}_score is stored per player.
    """
    __tablename__ = 'player_deck_stats'

    id = Column(Integer, primary_key=True)
    player_name = Column(String(100), nullable=False)
    deck_name = Column(String(200), nullable=False)
    best_score = Column(Integer, default=0)
    games_played = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('player_name', 'deck_name', name='uq_player_deck'),
        Index('idx_player_deck_player', 'player_name'),
        Index('idx_player_deck_deck', 'deck_name'),
    )

    def __repr__(self):
        return f"<PlayerDeckStats({self.player_name} on {self.deck_name}: {self.best_score})>"


class Achievement(Base):
    """
    Achievements unlocked by players.

    Stored per-player, tracks when each achievement was unlocked.
    """
    __tablename__ = 'achievements'

    id = Column(Integer, primary_key=True)
    player_name = Column(String(100), nullable=False)
    achievement_key = Column(String(100), nullable=False)
    unlocked_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('player_name', 'achievement_key', name='uq_player_achievement'),
        Index('idx_achievement_player', 'player_name'),
    )

    def __repr__(self):
        return f"<Achievement({self.player_name}: {self.achievement_key})>"


class GameHistory(Base):
    """
    Log of all games played.

    Provides historical data for analysis and leaderboards.
    """
    __tablename__ = 'game_history'

    id = Column(Integer, primary_key=True)
    opponent_name = Column(String(100), nullable=False)
    deck_name = Column(String(200))
    my_side = Column(String(10))  # 'dark' or 'light'

    # Game outcome
    won = Column(Boolean, default=False)
    route_score = Column(Integer, default=0)

    # Game metrics
    damage_dealt = Column(Integer, default=0)
    force_remaining = Column(Integer, default=0)
    turns = Column(Integer, default=0)
    duration_seconds = Column(Integer, default=0)

    # Achievements unlocked during this game
    achievements_unlocked = Column(Integer, default=0)

    # Timestamp
    played_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_game_opponent', 'opponent_name'),
        Index('idx_game_date', 'played_at'),
    )

    def __repr__(self):
        result = "Won" if self.won else "Lost"
        return f"<GameHistory({result} vs {self.opponent_name}, score={self.route_score})>"


class ChatMessage(Base):
    """
    Log of chat messages sent (for debugging and analysis).

    Helps track personality system behavior.
    """
    __tablename__ = 'chat_messages'

    id = Column(Integer, primary_key=True)
    game_id = Column(String(50))
    opponent_name = Column(String(100))
    message_type = Column(String(50))  # 'welcome', 'turn', 'damage', 'achievement', 'end'
    message_text = Column(String(1000))
    turn_number = Column(Integer)
    route_score = Column(Integer)
    sent_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_chat_game', 'game_id'),
    )

    def __repr__(self):
        return f"<ChatMessage({self.message_type}: {self.message_text[:30]}...)>"
