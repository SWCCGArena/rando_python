"""
Chat Command Handler

Processes incoming chat messages and responds to "rando" commands.
Based on Unity's AICommandHandler.cs implementation.
"""

import logging
from typing import Optional, TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from engine.client import GEMPClient
    from engine.models import ChatMessage
    from persistence.stats_repository import StatsRepository

logger = logging.getLogger(__name__)


class CommandHandler:
    """
    Handles chat commands directed at the bot.

    Commands must start with "rando " prefix.
    Only the current opponent can issue admin commands.
    """

    def __init__(self, client: 'GEMPClient', stats_repo: 'StatsRepository' = None,
                 bot_username: str = 'rando_cal'):
        """
        Initialize command handler.

        Args:
            client: GEMP client for sending responses
            stats_repo: Stats repository for leaderboards
            bot_username: Bot's username (to ignore own messages)
        """
        self.client = client
        self.stats_repo = stats_repo
        self.bot_username = bot_username.lower()

        # Game context (set when game starts)
        self.game_id: Optional[str] = None
        self.opponent_name: Optional[str] = None

        # Chat tracking
        self.last_chat_msg_id: int = 0

        logger.info("CommandHandler initialized")

    def reset_for_game(self, game_id: str, opponent_name: str, initial_msg_id: int = 0):
        """Reset state for new game"""
        self.game_id = game_id
        self.opponent_name = opponent_name
        self.last_chat_msg_id = initial_msg_id
        logger.info(f"CommandHandler reset for game {game_id} vs {opponent_name} (last_msg_id={initial_msg_id})")

    def poll_and_handle_commands(self) -> None:
        """
        Poll for new chat messages and handle any commands.

        Call this periodically from the game loop.
        """
        if not self.game_id:
            return

        # Get new messages
        messages, new_last_id = self.client.get_chat_messages(
            self.game_id, self.last_chat_msg_id
        )
        self.last_chat_msg_id = new_last_id

        # Process each message
        for msg in messages:
            self._handle_message(msg.from_user, msg.message)

    def _handle_message(self, username: str, message: str) -> None:
        """
        Handle a single chat message.

        Args:
            username: Who sent the message
            message: The message content
        """
        # Ignore messages from ourselves or system
        if username.lower() == self.bot_username or username.lower() == 'system':
            return

        # Normalize message
        msg_lower = message.lower().strip()

        # Check for easter eggs first
        if 'global thermonuclear war' in msg_lower:
            self._send_response("Wouldn't you prefer a good game of SWCCG?")
            return

        if msg_lower in ('good luck', 'good luck!', 'good luck.'):
            self._send_response("In my experience, there is no such thing as luck.")
            return

        # Only process "rando " commands
        if not msg_lower.startswith('rando '):
            return

        # Extract the command part
        command = msg_lower[6:].strip()  # Remove "rando " prefix

        logger.info(f"Processing command from {username}: 'rando {command}'")

        # Route to appropriate handler
        if command == 'help':
            self._cmd_help()
        elif command == 'scores':
            self._cmd_scores(username)
        elif command == 'stats':
            self._cmd_stats(username)
        else:
            # Unknown command - don't spam, just log
            logger.debug(f"Unknown command: {command}")

    def _send_response(self, message: str) -> bool:
        """Send a chat response"""
        if not self.game_id:
            return False
        return self.client.post_chat_message(self.game_id, message)

    def _verify_opponent(self, username: str, command: str) -> bool:
        """
        Verify the user is the current opponent (for admin commands).

        Returns True if allowed, False otherwise (sends rejection message).
        """
        if not self.opponent_name:
            return False

        if username.lower() != self.opponent_name.lower():
            self._send_response(
                f"Sorry, only {self.opponent_name} is allowed to issue the '{command}' command."
            )
            return False
        return True

    # =========================================================================
    # Command Implementations
    # =========================================================================

    def _cmd_help(self) -> None:
        """Handle 'rando help' command"""
        help_text = (
            "'rando scores' -- shows current leaderboards and your statistics, "
            "'rando stats' -- shows your personal game statistics"
        )
        self._send_response(help_text)

    def _cmd_scores(self, username: str) -> None:
        """Handle 'rando scores' command - show leaderboards"""
        if not self.stats_repo:
            self._send_response("Stats not available.")
            return

        # Get global stats
        global_stats = self.stats_repo.get_global_stats()

        # Get player stats
        player_stats = self.stats_repo.get_player_stats(username)

        # Build response
        lines = []

        # Global records
        if global_stats:
            lines.append(f"Total games: {global_stats.total_games}")
            if global_stats.best_route_player:
                lines.append(
                    f"Best route: {global_stats.best_route_score} by {global_stats.best_route_player}"
                )
            if global_stats.best_damage_player:
                lines.append(
                    f"Best damage: {global_stats.best_damage} by {global_stats.best_damage_player}"
                )

        # Player stats
        if player_stats:
            win_rate = 0
            if player_stats.games_played > 0:
                win_rate = int(100 * player_stats.wins / player_stats.games_played)
            lines.append(
                f"Your record: {player_stats.wins}W-{player_stats.losses}L ({win_rate}%)"
            )
            if player_stats.best_route_score > 0:
                lines.append(f"Your best route: {player_stats.best_route_score}")
        else:
            lines.append(f"{username}: No games recorded yet!")

        self._send_response(" | ".join(lines))

    def _cmd_stats(self, username: str) -> None:
        """Handle 'rando stats' command - show personal statistics"""
        if not self.stats_repo:
            self._send_response("Stats not available.")
            return

        player_stats = self.stats_repo.get_player_stats(username)

        if not player_stats:
            self._send_response(f"{username}: No games recorded yet! Play a game to get started.")
            return

        # Calculate win rate
        win_rate = 0
        if player_stats.games_played > 0:
            win_rate = int(100 * player_stats.wins / player_stats.games_played)

        response = (
            f"{username} - Games: {player_stats.games_played}, "
            f"Wins: {player_stats.wins}, Losses: {player_stats.losses} ({win_rate}%), "
            f"Best Route: {player_stats.best_route_score}, "
            f"Best Damage: {player_stats.best_damage}, "
            f"Total Astrogation Score: {player_stats.total_ast_score}"
        )
        self._send_response(response)
