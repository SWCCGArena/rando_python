"""
Network Coordinator

Central coordinator for all GEMP server network requests.
Enforces smart rate limiting based on noLongDelay flag and tracks metrics.

All network calls should go through this component to:
1. Apply appropriate delays based on decision type
2. Track request metrics for monitoring
3. Log request counts and response times
"""

import time
import logging
from typing import Optional, List, TYPE_CHECKING
from collections import deque

if TYPE_CHECKING:
    from .client import GEMPClient

logger = logging.getLogger(__name__)


class NetworkCoordinator:
    """
    Central coordinator for all GEMP server network requests.

    Uses noLongDelay flag to determine appropriate response timing:
    - noLongDelay=true: Quick response expected (1 second delay)
    - noLongDelay=false: Player should "think" (3 second delay)
    - Background requests: Longer delays (30 seconds for hall/cardInfo)
    """

    # Default delay settings (matching web client behavior)
    # Can be overridden by config
    DEFAULT_DELAY_QUICK = 1.0        # When noLongDelay=true (quick response expected)
    DEFAULT_DELAY_NORMAL = 3.0       # When noLongDelay=false (player should "think")
    DEFAULT_DELAY_BACKGROUND = 30.0  # For background requests (hall, cardInfo)
    DEFAULT_DELAY_MIN = 0.25         # Absolute minimum between any requests

    def __init__(self, client: 'GEMPClient', config=None):
        """
        Initialize the network coordinator.

        Args:
            client: The GEMPClient to wrap
            config: Optional config object with network delay settings
        """
        self.client = client
        self.config = config
        self.last_request_time = 0.0

        # Use config values if available, otherwise defaults
        if config:
            self.DELAY_QUICK = getattr(config, 'NETWORK_DELAY_QUICK', self.DEFAULT_DELAY_QUICK)
            self.DELAY_NORMAL = getattr(config, 'NETWORK_DELAY_NORMAL', self.DEFAULT_DELAY_NORMAL)
            self.DELAY_BACKGROUND = getattr(config, 'NETWORK_DELAY_BACKGROUND', self.DEFAULT_DELAY_BACKGROUND)
            self.DELAY_MIN = getattr(config, 'NETWORK_DELAY_MIN', self.DEFAULT_DELAY_MIN)
        else:
            self.DELAY_QUICK = self.DEFAULT_DELAY_QUICK
            self.DELAY_NORMAL = self.DEFAULT_DELAY_NORMAL
            self.DELAY_BACKGROUND = self.DEFAULT_DELAY_BACKGROUND
            self.DELAY_MIN = self.DEFAULT_DELAY_MIN

        # Metrics tracking
        self.total_requests = 0
        self.total_response_time = 0.0
        self.request_history = deque(maxlen=100)  # Last 100 requests

        logger.info(f"NetworkCoordinator initialized (delays: quick={self.DELAY_QUICK}s, normal={self.DELAY_NORMAL}s, bg={self.DELAY_BACKGROUND}s)")

    def _apply_delay(self, delay_type: str, no_long_delay: bool = None):
        """
        Apply appropriate delay before making a request.

        Args:
            delay_type: Type of delay ('decision', 'background', 'minimal')
            no_long_delay: For decisions, whether quick response is expected
        """
        if delay_type == 'decision':
            # Use noLongDelay to determine response speed
            delay = self.DELAY_QUICK if no_long_delay else self.DELAY_NORMAL
        elif delay_type == 'background':
            delay = self.DELAY_BACKGROUND
        else:
            delay = self.DELAY_MIN

        elapsed = time.time() - self.last_request_time
        if elapsed < delay:
            wait_time = delay - elapsed
            logger.debug(f"⏳ Delay ({delay_type}): waiting {wait_time:.2f}s")
            time.sleep(wait_time)

    def _record_request(self, endpoint: str, duration: float, success: bool):
        """
        Record request metrics for monitoring.

        Args:
            endpoint: The endpoint that was called
            duration: How long the request took in seconds
            success: Whether the request succeeded
        """
        self.total_requests += 1
        self.total_response_time += duration
        self.request_history.append({
            'time': time.time(),
            'endpoint': endpoint,
            'duration': duration,
            'success': success
        })

        # Log summary every 20 requests
        if self.total_requests % 20 == 0:
            avg = self.total_response_time / self.total_requests
            logger.info(f"📊 Network: {self.total_requests} requests, avg {avg:.3f}s")

    def get_metrics(self) -> dict:
        """
        Get current metrics for admin UI or monitoring.

        Returns:
            Dict with request counts and response times
        """
        return {
            'total_requests': self.total_requests,
            'avg_response_time': self.total_response_time / max(1, self.total_requests),
            'recent_requests': list(self.request_history)[-10:]
        }

    # =========================================================================
    # Wrapped client methods
    # =========================================================================

    def get_game_update(self, game_id: str, channel_number: int) -> Optional[str]:
        """
        Get game update from server.

        Game updates use minimal delay since the server drives the pace.

        Args:
            game_id: The game ID
            channel_number: Current channel number

        Returns:
            Update XML or None on error
        """
        self._apply_delay('minimal')
        start = time.time()
        result = self.client.get_game_update(game_id, channel_number)
        self._record_request('game/update', time.time() - start, result is not None)
        self.last_request_time = time.time()
        return result

    def post_decision(self, game_id: str, channel_number: int,
                      decision_id: str, decision_value: str,
                      no_long_delay: bool = False) -> Optional[str]:
        """
        Post a decision response to the server.

        Decisions use noLongDelay to determine appropriate response speed.
        This makes the bot appear more human-like by "thinking" before acting.

        Args:
            game_id: The game ID
            channel_number: Current channel number
            decision_id: The decision ID
            decision_value: The response value
            no_long_delay: If True, respond quickly (1s). If False, "think" (3s).

        Returns:
            Update XML or None on error
        """
        self._apply_delay('decision', no_long_delay)
        start = time.time()
        result = self.client.post_decision(game_id, channel_number, decision_id, decision_value)
        self._record_request('game/decision', time.time() - start, result is not None)
        self.last_request_time = time.time()
        return result

    def get_card_info(self, game_id: str, card_id: str) -> Optional[str]:
        """
        Get card info from server.

        cardInfo is a background request so uses longer delay.

        Args:
            game_id: The game ID
            card_id: The card ID

        Returns:
            HTML response or None on error
        """
        self._apply_delay('background')
        start = time.time()
        result = self.client.get_card_info(game_id, card_id)
        self._record_request('game/cardInfo', time.time() - start, result is not None)
        self.last_request_time = time.time()
        return result

    def get_chat_messages(self, game_id: str, last_msg_id: int) -> tuple:
        """
        Poll for chat messages.

        Chat uses minimal delay.

        Args:
            game_id: The game ID
            last_msg_id: Last message ID received

        Returns:
            Tuple of (messages, new_last_msg_id)
        """
        self._apply_delay('minimal')
        start = time.time()
        result = self.client.get_chat_messages(game_id, last_msg_id)
        self._record_request('chat/poll', time.time() - start, True)
        self.last_request_time = time.time()
        return result

    def post_chat_message(self, game_id: str, message: str, username: str = None) -> bool:
        """
        Post a chat message.

        Chat sends use minimal delay.

        Args:
            game_id: The game ID
            message: The message to send
            username: Optional username

        Returns:
            True if successful
        """
        self._apply_delay('minimal')
        start = time.time()
        result = self.client.post_chat_message(game_id, message, username)
        self._record_request('chat/send', time.time() - start, result)
        self.last_request_time = time.time()
        return result

    def register_chat(self, game_id: str) -> tuple:
        """
        Register with game chat server.

        Args:
            game_id: The game ID

        Returns:
            Tuple of (success, last_msg_id)
        """
        self._apply_delay('minimal')
        start = time.time()
        result = self.client.register_chat(game_id)
        self._record_request('chat/register', time.time() - start, result[0])
        self.last_request_time = time.time()
        return result

    def update_hall(self, channel_number: int) -> tuple:
        """
        Poll for hall updates using incremental endpoint.

        Hall updates are background requests with longer delays.

        Args:
            channel_number: Last known hall channel number

        Returns:
            Tuple of (tables, new_channel_number)
        """
        self._apply_delay('background')
        start = time.time()
        result = self.client.update_hall(channel_number)
        self._record_request('hall/update', time.time() - start, result is not None)
        self.last_request_time = time.time()
        return result

    def get_hall_initial(self) -> List:
        """
        Get initial hall state (full state, not incremental).

        Only used on login, not for polling.

        Returns:
            List of GameTable objects
        """
        self._apply_delay('minimal')
        start = time.time()
        result = self.client.get_hall_tables()
        self._record_request('hall/initial', time.time() - start, len(result) >= 0)
        self.last_request_time = time.time()
        return result

    # =========================================================================
    # Pass-through methods (no delay needed)
    # =========================================================================

    def login(self, username: str, password: str) -> bool:
        """Login to server (no delay needed)"""
        return self.client.login(username, password)

    def logout(self):
        """Logout from server"""
        return self.client.logout()

    def join_game(self, game_id: str) -> Optional[str]:
        """Join a game (no delay needed)"""
        return self.client.join_game(game_id)

    def concede_game(self, game_id: str) -> bool:
        """Concede the game (no delay needed)"""
        return self.client.concede_game(game_id)

    def create_table(self, deck_name: str, table_name: str,
                     game_format: str = "open", is_library: bool = True) -> Optional[str]:
        """Create a table (no delay needed)"""
        return self.client.create_table(deck_name, table_name, game_format, is_library)

    def leave_table(self, table_id: str) -> bool:
        """Leave a table (no delay needed)"""
        return self.client.leave_table(table_id)

    def leave_chat(self, game_id: str) -> bool:
        """Leave chat (no delay needed)"""
        return self.client.leave_chat(game_id)

    def get_library_decks(self) -> List:
        """Get library decks (no delay needed)"""
        return self.client.get_library_decks()

    def get_user_decks(self) -> List:
        """Get user decks (no delay needed)"""
        return self.client.get_user_decks()

    @property
    def logged_in(self) -> bool:
        """Check if logged in"""
        return self.client.logged_in

    @property
    def last_error(self) -> Optional[str]:
        """Get last error from client"""
        return self.client.last_error
