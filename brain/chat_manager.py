"""
Chat Manager

Coordinates chat message generation and sending:
- Throttling to prevent spam
- Event-based message triggers
- Integration with personality brain and achievements
"""

import logging
import time
from typing import Optional, List, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from engine.client import GEMPClient
    from engine.board_state import BoardState
    from persistence.stats_repository import StatsRepository
    from .astrogator_brain import AstrogatorBrain
    from .achievements import AchievementTracker

logger = logging.getLogger(__name__)


class ChatManager:
    """
    Manages chat message generation and delivery.

    Handles:
    - Welcome messages on game start
    - Turn-by-turn route score commentary
    - Battle damage messages with record tracking
    - Achievement notifications
    - Game end summaries
    """

    # Minimum seconds between chat messages (throttling)
    MIN_CHAT_INTERVAL = 2.0

    def __init__(self, brain: 'AstrogatorBrain',
                 stats_repo: 'StatsRepository',
                 client: 'GEMPClient',
                 achievement_tracker: 'AchievementTracker' = None):
        """
        Initialize chat manager.

        Args:
            brain: Personality brain for message generation
            stats_repo: Stats repository for persistence
            client: GEMP client for sending messages
            achievement_tracker: Optional achievement tracker
        """
        self.brain = brain
        self.stats_repo = stats_repo
        self.client = client
        self.achievement_tracker = achievement_tracker

        # State tracking
        self.game_id: Optional[str] = None
        self.opponent_name: Optional[str] = None
        self.deck_name: Optional[str] = None
        self.opponent_side: Optional[str] = None
        self.my_side: Optional[str] = None

        # Turn tracking
        self.current_turn: int = 0
        self.last_route_score: Optional[int] = None
        self.reported_turns: set = set()

        # Battle tracking
        self.damage_this_battle_reported: bool = False
        self.highest_damage_this_game: int = 0

        # Throttling
        self.last_chat_time: float = 0
        self.messages_queued: List[str] = []

        # Game timing
        self.game_start_time: Optional[datetime] = None

        logger.info("ChatManager initialized")

    def reset_for_game(self, game_id: str, opponent_name: str, deck_name: str,
                       my_side: str, opponent_side: str):
        """Reset state for new game"""
        self.game_id = game_id
        self.opponent_name = opponent_name
        self.deck_name = deck_name
        self.my_side = my_side
        self.opponent_side = opponent_side

        self.current_turn = 0
        self.last_route_score = None
        self.reported_turns.clear()

        self.damage_this_battle_reported = False
        self.highest_damage_this_game = 0

        self.messages_queued.clear()

        self.game_start_time = datetime.utcnow()

        # Reset achievement tracker
        if self.achievement_tracker:
            self.achievement_tracker.reset_for_game()

        # Reset brain state
        if self.brain:
            self.brain.on_game_start(opponent_name, deck_name, my_side)

        logger.info(f"ChatManager reset for game {game_id} vs {opponent_name}")

    # =========================================================================
    # Message Sending
    # =========================================================================

    def _send_chat(self, message: str, message_type: str = 'general') -> bool:
        """
        Send a chat message with throttling.

        Returns True if sent, False if throttled.
        """
        if not message:
            return False

        # Check throttle
        now = time.time()
        if now - self.last_chat_time < self.MIN_CHAT_INTERVAL:
            logger.debug(f"Chat throttled: {message[:50]}...")
            self.messages_queued.append(message)
            return False

        # Send message
        try:
            success = self.client.post_chat_message(self.game_id, message)
            self.last_chat_time = now

            # Log to database
            if self.stats_repo and self.game_id:
                self.stats_repo.log_chat_message(
                    game_id=self.game_id,
                    opponent_name=self.opponent_name or "unknown",
                    message_type=message_type,
                    message_text=message,
                    turn_number=self.current_turn,
                    route_score=self.last_route_score or 0
                )

            if success:
                logger.debug(f"Chat sent: {message[:50]}...")
            return success
        except Exception as e:
            logger.error(f"Failed to send chat: {e}")
            return False

    def send_queued_messages(self):
        """Send any queued messages (called periodically)"""
        if not self.messages_queued:
            return

        now = time.time()
        if now - self.last_chat_time >= self.MIN_CHAT_INTERVAL:
            message = self.messages_queued.pop(0)
            self._send_chat(message)

    # =========================================================================
    # Game Lifecycle Events
    # =========================================================================

    def on_game_start(self):
        """Send welcome message when game starts"""
        if not self.brain or not self.opponent_name:
            return

        message = self.brain.get_welcome_message(
            opponent_name=self.opponent_name,
            deck_name=self.deck_name or "Unknown Deck",
            opponent_side=self.opponent_side
        )

        self._send_chat(message, message_type='welcome')

    def on_turn_start(self, turn_number: int, board_state: 'BoardState'):
        """
        Called at start of each turn.

        Generates route score commentary.
        """
        if turn_number in self.reported_turns:
            return

        self.current_turn = turn_number
        self.reported_turns.add(turn_number)

        # Reset battle damage flag for new turn
        self.damage_this_battle_reported = False

        # Check board for achievements
        if self.achievement_tracker and self.opponent_name:
            achievement_msgs = self.achievement_tracker.check_board_for_achievements(
                board_state, self.opponent_name
            )
            for msg in achievement_msgs:
                self._send_chat(msg, message_type='achievement')

        # Get turn message from brain
        if self.brain and turn_number >= 2:
            message = self.brain.get_turn_message(turn_number, board_state)
            if message:
                # Calculate and store route score
                self.last_route_score = self.brain.calculate_route_score(board_state)
                self._send_chat(message, message_type='turn')

    def on_card_deployed(self, card_title: str, blueprint_id: str, zone: str, owner: str,
                         board_state: 'BoardState'):
        """
        Called when a card is deployed to the board.

        Immediately checks for achievements triggered by this card.

        Args:
            card_title: Title of the deployed card
            blueprint_id: Blueprint ID of the card
            zone: Zone the card was deployed to
            owner: Owner of the card
            board_state: Current board state
        """
        if not self.achievement_tracker or not self.opponent_name:
            return

        # Check achievements based on current board state
        achievement_msgs = self.achievement_tracker.check_board_for_achievements(
            board_state, self.opponent_name
        )
        for msg in achievement_msgs:
            self._send_chat(msg, message_type='achievement')
            logger.info(f"🏆 Achievement triggered by {card_title}: {msg[:50]}...")

    def on_battle_damage(self, damage: int, board_state: 'BoardState'):
        """
        Called when battle damage is dealt.

        Generates damage commentary and checks records.
        """
        if damage <= 0:
            return

        # Track highest damage this game (for game-end stats)
        if damage > self.highest_damage_this_game:
            self.highest_damage_this_game = damage

        # Check for damage achievement
        if self.achievement_tracker and self.opponent_name:
            ach_msg = self.achievement_tracker.record_damage(damage, self.opponent_name)
            if ach_msg:
                self._send_chat(ach_msg, message_type='achievement')

        # Determine if this is a record
        is_new_global = False
        is_new_personal = False
        previous_holder = None
        previous_record = None

        if self.stats_repo and self.opponent_name:
            # Check and update global record (saves immediately if new record)
            is_new_global, previous_holder = self.stats_repo.check_and_update_global_record(
                'damage', damage, self.opponent_name
            )

            # Check and update personal record (saves immediately if new record)
            is_new_personal, previous_record = self.stats_repo.check_and_update_personal_damage(
                self.opponent_name, damage
            )

        # Get damage message
        if self.brain and not self.damage_this_battle_reported:
            message = self.brain.get_damage_message(
                damage=damage,
                is_new_global_record=is_new_global,
                is_new_personal_record=is_new_personal,
                previous_holder=previous_holder,
                previous_record=previous_record,
                current_player=self.opponent_name
            )
            if message:
                self._send_chat(message, message_type='damage')
                self.damage_this_battle_reported = True

    def on_game_end(self, won: bool, board_state: 'BoardState' = None):
        """
        Called when game ends.

        Generates end game message and updates stats.
        """
        if not self.brain:
            return

        # Calculate final route score
        route_score = 0
        force_remaining = 0
        if board_state:
            route_score = self.brain.calculate_route_score(board_state)
            force_remaining = board_state.force_pile

        # Calculate game duration
        duration_seconds = 0
        if self.game_start_time:
            duration_seconds = int((datetime.utcnow() - self.game_start_time).total_seconds())

        # Update stats
        is_new_deck_record = False
        previous_holder = None
        previous_score = 0
        new_total_score = 0
        is_new_top_astrogator = False

        if self.stats_repo and self.opponent_name:
            # Get effective deck name - try multiple sources if not set
            effective_deck_name = self.deck_name
            if not effective_deck_name:
                # Try to recover from persisted table state (survives restarts)
                try:
                    from engine.table_manager import _load_table_state
                    table_state = _load_table_state()
                    if table_state and table_state.get('deck_name'):
                        effective_deck_name = table_state['deck_name']
                        logger.info(f"📊 Recovered deck_name from table state: {effective_deck_name}")
                except Exception as e:
                    logger.warning(f"Could not load table state for deck_name: {e}")
            effective_deck_name = effective_deck_name or "Unknown"

            # Always update player stats and game history (regardless of who won)
            player = self.stats_repo.record_game_result(
                player_name=self.opponent_name,
                won=won,
                route_score=route_score if won else 0,  # Only count route score if opponent won
                damage=self.highest_damage_this_game,
                force_remaining=force_remaining,
                time_seconds=duration_seconds
            )
            new_total_score = player.total_ast_score

            # Record game to history
            self.stats_repo.record_game(
                opponent_name=self.opponent_name,
                deck_name=effective_deck_name,
                my_side=self.my_side or "unknown",
                won=won,
                route_score=route_score if won else 0,
                damage=self.highest_damage_this_game,
                force_remaining=force_remaining,
                turns=self.current_turn,
                duration_seconds=duration_seconds
            )

            # Route score / deck records only apply when opponent won (beat the bot's deck)
            if won:
                # Update deck stats (global high score for this deck)
                logger.info(f"📊 Recording deck stats for '{effective_deck_name}' - player: {self.opponent_name}, score: {route_score}")
                deck_stats, is_new_deck_record = self.stats_repo.update_deck_score(
                    effective_deck_name, self.opponent_name, route_score
                )
                if not is_new_deck_record and deck_stats:
                    previous_holder = deck_stats.best_player
                    previous_score = deck_stats.best_score

                # Update player's score on this specific deck
                self.stats_repo.update_player_deck_score(
                    self.opponent_name, effective_deck_name, route_score
                )
                logger.info(f"📊 Deck stats recorded successfully")

                # Check global astrogation record
                is_new_top_astrogator, _ = self.stats_repo.check_and_update_global_record(
                    'ast_score', new_total_score, self.opponent_name
                )

            # Check game end achievements
            if self.achievement_tracker:
                ach_msgs = self.achievement_tracker.check_game_end_achievements(
                    opponent_name=self.opponent_name,
                    won=won,
                    route_score=route_score,
                    turns=self.current_turn,
                    force_remaining=force_remaining,
                    player_stats=player
                )
                for msg in ach_msgs:
                    self._send_chat(msg, message_type='achievement')

        # Generate end game message
        message = self.brain.get_game_end_message(
            won=won,
            route_score=route_score,
            deck_name=self.deck_name,
            is_new_deck_record=is_new_deck_record,
            previous_holder=previous_holder,
            previous_score=previous_score,
            new_total_score=new_total_score,
            is_new_top_astrogator=is_new_top_astrogator
        )

        self._send_chat(message, message_type='end')

        # Notify brain
        # Note: brain.on_game_end expects won=True to mean BOT won (per interface.py)
        # but this method receives won=True meaning PLAYER won, so we invert
        if self.brain:
            bot_won = not won
            self.brain.on_game_end(bot_won, board_state)

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def get_current_stats(self) -> dict:
        """Get current game stats for display"""
        return {
            'opponent': self.opponent_name,
            'deck': self.deck_name,
            'turn': self.current_turn,
            'last_score': self.last_route_score,
            'highest_damage': self.highest_damage_this_game,
            'game_time': str(datetime.utcnow() - self.game_start_time) if self.game_start_time else None
        }
