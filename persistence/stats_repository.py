"""
Stats Repository - Data Access Layer

Provides high-level methods for managing game statistics,
player records, achievements, and deck scores.
"""

import logging
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from .models import GlobalStats, DeckStats, PlayerStats, PlayerDeckStats, Achievement, GameHistory, ChatMessage
from .database import session_scope, get_session

logger = logging.getLogger(__name__)


class StatsRepository:
    """
    Repository for all game statistics operations.

    Provides methods for:
    - Player stats (wins/losses, personal bests)
    - Deck stats (Astrogator route scores)
    - Global records
    - Achievements
    - Game history
    """

    def __init__(self, session: Session = None):
        """
        Initialize repository.

        Args:
            session: Optional SQLAlchemy session. If None, creates new sessions per operation.
        """
        self._session = session

    def _get_session(self) -> Session:
        """Get session (uses provided or creates new)"""
        return self._session if self._session else get_session()

    # =========================================================================
    # Player Stats
    # =========================================================================

    def get_or_create_player(self, player_name: str) -> PlayerStats:
        """Get player stats, creating if doesn't exist"""
        with session_scope() as session:
            player = session.query(PlayerStats).filter_by(player_name=player_name).first()

            if not player:
                player = PlayerStats(player_name=player_name)
                session.add(player)
                session.commit()
                logger.info(f"Created new player record: {player_name}")

            # Refresh to get the committed data
            session.refresh(player)
            # Detach from session to use outside context
            session.expunge(player)
            return player

    def get_player_stats(self, player_name: str) -> Optional[PlayerStats]:
        """Get player stats (returns None if not found)"""
        with session_scope() as session:
            player = session.query(PlayerStats).filter_by(player_name=player_name).first()
            if player:
                session.expunge(player)
            return player

    def record_game_result(self, player_name: str, won: bool, route_score: int = 0,
                          damage: int = 0, force_remaining: int = 0,
                          time_seconds: int = 0) -> PlayerStats:
        """
        Record a game result and update player stats.

        Args:
            player_name: Opponent's name
            won: Whether the player (opponent) won
            route_score: Astrogator route score
            damage: Total damage dealt
            force_remaining: Force pile at end
            time_seconds: Game duration

        Returns:
            Updated PlayerStats
        """
        with session_scope() as session:
            player = session.query(PlayerStats).filter_by(player_name=player_name).first()

            if not player:
                player = PlayerStats(
                    player_name=player_name,
                    games_played=0,
                    wins=0,
                    losses=0,
                    total_ast_score=0,
                    best_route_score=0,
                    best_damage=0,
                    best_force_remaining=0,
                    best_time_seconds=0
                )
                session.add(player)

            # Update win/loss
            player.games_played += 1
            if won:
                player.wins += 1
            else:
                player.losses += 1

            # Update Astrogator score
            player.total_ast_score += route_score
            if route_score > player.best_route_score:
                player.best_route_score = route_score

            # Update personal bests
            if damage > player.best_damage:
                player.best_damage = damage
            if force_remaining > player.best_force_remaining:
                player.best_force_remaining = force_remaining
            if time_seconds > 0 and (player.best_time_seconds == 0 or time_seconds < player.best_time_seconds):
                player.best_time_seconds = time_seconds

            player.last_seen = datetime.utcnow()

            session.commit()
            session.refresh(player)
            session.expunge(player)

            logger.info(f"Updated stats for {player_name}: {player.wins}W/{player.losses}L, ast_score={player.total_ast_score}")
            return player

    def get_top_players(self, limit: int = 10, by: str = 'ast_score') -> List[PlayerStats]:
        """
        Get top players by various metrics.

        Args:
            limit: Number of players to return
            by: Sort field ('ast_score', 'wins', 'games', 'win_rate')

        Returns:
            List of PlayerStats
        """
        with session_scope() as session:
            query = session.query(PlayerStats)

            if by == 'ast_score':
                query = query.order_by(desc(PlayerStats.total_ast_score))
            elif by == 'wins':
                query = query.order_by(desc(PlayerStats.wins))
            elif by == 'games':
                query = query.order_by(desc(PlayerStats.games_played))
            else:
                query = query.order_by(desc(PlayerStats.total_ast_score))

            players = query.limit(limit).all()
            for p in players:
                session.expunge(p)
            return players

    # =========================================================================
    # Deck Stats (Astrogator Routes)
    # =========================================================================

    def get_or_create_deck(self, deck_name: str) -> DeckStats:
        """Get deck stats, creating if doesn't exist"""
        with session_scope() as session:
            deck = session.query(DeckStats).filter_by(deck_name=deck_name).first()

            if not deck:
                deck = DeckStats(deck_name=deck_name)
                session.add(deck)
                session.commit()
                logger.info(f"Created new deck record: {deck_name}")

            session.refresh(deck)
            session.expunge(deck)
            return deck

    def get_deck_stats(self, deck_name: str) -> Optional[DeckStats]:
        """Get deck stats (returns None if not found)"""
        with session_scope() as session:
            deck = session.query(DeckStats).filter_by(deck_name=deck_name).first()
            if deck:
                session.expunge(deck)
            return deck

    def update_deck_score(self, deck_name: str, player_name: str, score: int) -> Tuple[DeckStats, bool]:
        """
        Update deck score if it's a new record.

        Args:
            deck_name: Name of the deck
            player_name: Player who achieved the score
            score: Route score

        Returns:
            Tuple of (DeckStats, is_new_record)
        """
        with session_scope() as session:
            deck = session.query(DeckStats).filter_by(deck_name=deck_name).first()

            if not deck:
                deck = DeckStats(
                    deck_name=deck_name,
                    games_played=0,
                    total_score=0,
                    best_score=0,
                    best_score_player=None,
                    best_score_date=None
                )
                session.add(deck)

            deck.games_played += 1
            deck.total_score += score

            is_new_record = score > deck.best_score
            if is_new_record:
                old_record = deck.best_score
                old_holder = deck.best_player
                deck.best_score = score
                deck.best_player = player_name
                logger.info(f"New deck record for '{deck_name}': {score} by {player_name} (was {old_record} by {old_holder})")

            session.commit()
            session.refresh(deck)
            session.expunge(deck)

            return deck, is_new_record

    # =========================================================================
    # Player-Deck Stats (per-player-per-deck scores)
    # =========================================================================

    def get_player_deck_stats(self, player_name: str, deck_name: str) -> Optional[PlayerDeckStats]:
        """Get a player's stats for a specific deck"""
        with session_scope() as session:
            stats = session.query(PlayerDeckStats).filter_by(
                player_name=player_name,
                deck_name=deck_name
            ).first()
            if stats:
                session.expunge(stats)
            return stats

    def update_player_deck_score(self, player_name: str, deck_name: str, score: int) -> Tuple[PlayerDeckStats, bool]:
        """
        Update a player's score on a specific deck.

        Args:
            player_name: Player's name
            deck_name: Deck name
            score: Route score achieved

        Returns:
            Tuple of (PlayerDeckStats, is_new_personal_record)
        """
        with session_scope() as session:
            stats = session.query(PlayerDeckStats).filter_by(
                player_name=player_name,
                deck_name=deck_name
            ).first()

            if not stats:
                stats = PlayerDeckStats(
                    player_name=player_name,
                    deck_name=deck_name,
                    best_score=0,
                    games_played=0
                )
                session.add(stats)

            stats.games_played += 1
            is_new_record = score > stats.best_score

            if is_new_record:
                old_score = stats.best_score
                stats.best_score = score
                logger.info(f"New personal deck record for {player_name} on '{deck_name}': {score} (was {old_score})")

            session.commit()
            session.refresh(stats)
            session.expunge(stats)

            return stats, is_new_record

    # =========================================================================
    # Global Records
    # =========================================================================

    def get_global_record(self, stat_type: str) -> Optional[GlobalStats]:
        """Get a global record"""
        with session_scope() as session:
            record = session.query(GlobalStats).filter_by(stat_type=stat_type).first()
            if record:
                session.expunge(record)
            return record

    def check_and_update_global_record(self, stat_type: str, value: int, player_name: str,
                                       higher_is_better: bool = True) -> Tuple[bool, Optional[str]]:
        """
        Check if value beats global record and update if so.

        Args:
            stat_type: Type of record ('damage', 'force', 'ast_score', 'time')
            value: The value to check
            player_name: Player who achieved the value
            higher_is_better: True if higher values are better

        Returns:
            Tuple of (is_new_record, previous_holder_name)
        """
        with session_scope() as session:
            record = session.query(GlobalStats).filter_by(stat_type=stat_type).first()

            if not record:
                # First record ever
                record = GlobalStats(stat_type=stat_type, value=value, player_name=player_name)
                session.add(record)
                session.commit()
                logger.info(f"First global record for {stat_type}: {value} by {player_name}")
                return True, None

            # Check if new record
            is_new_record = False
            if higher_is_better:
                is_new_record = value > record.value
            else:
                is_new_record = value < record.value and value > 0

            previous_holder = record.player_name

            if is_new_record:
                record.value = value
                record.player_name = player_name
                record.updated_at = datetime.utcnow()
                session.commit()
                logger.info(f"New global record for {stat_type}: {value} by {player_name} (was {record.value} by {previous_holder})")
                return True, previous_holder

            return False, None

    def get_all_global_records(self) -> Dict[str, GlobalStats]:
        """Get all global records as a dictionary"""
        with session_scope() as session:
            records = session.query(GlobalStats).all()
            result = {}
            for r in records:
                session.expunge(r)
                result[r.stat_type] = r
            return result

    # =========================================================================
    # Achievements
    # =========================================================================

    def has_achievement(self, player_name: str, achievement_key: str) -> bool:
        """Check if player has unlocked an achievement"""
        with session_scope() as session:
            exists = session.query(Achievement).filter_by(
                player_name=player_name,
                achievement_key=achievement_key
            ).first() is not None
            return exists

    def unlock_achievement(self, player_name: str, achievement_key: str) -> Tuple[bool, int]:
        """
        Unlock an achievement for a player.

        Args:
            player_name: Player's name
            achievement_key: Achievement identifier

        Returns:
            Tuple of (newly_unlocked, total_achievement_count)
        """
        with session_scope() as session:
            # Check if already has it
            existing = session.query(Achievement).filter_by(
                player_name=player_name,
                achievement_key=achievement_key
            ).first()

            if existing:
                # Already unlocked
                count = session.query(Achievement).filter_by(player_name=player_name).count()
                return False, count

            # Unlock it
            achievement = Achievement(player_name=player_name, achievement_key=achievement_key)
            session.add(achievement)

            # Update player's achievement count
            player = session.query(PlayerStats).filter_by(player_name=player_name).first()
            if player:
                player.achievement_count += 1

            session.commit()

            count = session.query(Achievement).filter_by(player_name=player_name).count()
            logger.info(f"Achievement unlocked for {player_name}: {achievement_key} ({count} total)")

            return True, count

    def get_player_achievements(self, player_name: str) -> List[Achievement]:
        """Get all achievements for a player"""
        with session_scope() as session:
            achievements = session.query(Achievement).filter_by(player_name=player_name).all()
            for a in achievements:
                session.expunge(a)
            return achievements

    def get_achievement_count(self, player_name: str) -> int:
        """Get count of achievements for a player"""
        with session_scope() as session:
            return session.query(Achievement).filter_by(player_name=player_name).count()

    # =========================================================================
    # Game History
    # =========================================================================

    def record_game(self, opponent_name: str, deck_name: str, my_side: str,
                   won: bool, route_score: int = 0, damage: int = 0,
                   force_remaining: int = 0, turns: int = 0,
                   duration_seconds: int = 0, achievements_unlocked: int = 0) -> GameHistory:
        """Record a game to history"""
        with session_scope() as session:
            game = GameHistory(
                opponent_name=opponent_name,
                deck_name=deck_name,
                my_side=my_side,
                won=won,
                route_score=route_score,
                damage_dealt=damage,
                force_remaining=force_remaining,
                turns=turns,
                duration_seconds=duration_seconds,
                achievements_unlocked=achievements_unlocked
            )
            session.add(game)
            session.commit()
            session.refresh(game)
            session.expunge(game)

            logger.info(f"Recorded game: {'Won' if won else 'Lost'} vs {opponent_name}, score={route_score}")
            return game

    def get_recent_games(self, limit: int = 10) -> List[GameHistory]:
        """Get most recent games"""
        with session_scope() as session:
            games = session.query(GameHistory).order_by(
                desc(GameHistory.played_at)
            ).limit(limit).all()
            for g in games:
                session.expunge(g)
            return games

    def get_games_vs_player(self, opponent_name: str, limit: int = 10) -> List[GameHistory]:
        """Get games against a specific opponent"""
        with session_scope() as session:
            games = session.query(GameHistory).filter_by(
                opponent_name=opponent_name
            ).order_by(desc(GameHistory.played_at)).limit(limit).all()
            for g in games:
                session.expunge(g)
            return games

    # =========================================================================
    # Chat Message Logging
    # =========================================================================

    def log_chat_message(self, game_id: str, opponent_name: str, message_type: str,
                        message_text: str, turn_number: int = 0, route_score: int = 0) -> None:
        """Log a chat message for debugging/analysis"""
        with session_scope() as session:
            msg = ChatMessage(
                game_id=game_id,
                opponent_name=opponent_name,
                message_type=message_type,
                message_text=message_text[:1000],  # Truncate if too long
                turn_number=turn_number,
                route_score=route_score
            )
            session.add(msg)
            session.commit()

    # =========================================================================
    # Summary Methods
    # =========================================================================

    def get_overall_stats(self) -> Dict:
        """Get overall bot statistics"""
        with session_scope() as session:
            total_games = session.query(func.count(GameHistory.id)).scalar() or 0
            total_wins = session.query(func.count(GameHistory.id)).filter(
                GameHistory.won == True
            ).scalar() or 0
            total_players = session.query(func.count(PlayerStats.id)).scalar() or 0
            total_achievements = session.query(func.count(Achievement.id)).scalar() or 0

            return {
                'total_games': total_games,
                'total_wins': total_wins,
                'total_losses': total_games - total_wins,
                'win_rate': (total_wins / total_games * 100) if total_games > 0 else 0,
                'unique_players': total_players,
                'total_achievements_awarded': total_achievements,
            }
