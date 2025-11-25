"""
Table Manager

Handles autonomous table lifecycle management for unattended bot operation.

Responsibilities:
- Auto-create tables when in lobby without one
- Monitor table health
- Handle table disappearance/expiration
- Manage deck rotation
- Track table creation failures and implement backoff

Design Goal: Bot should maintain a table 24/7 without human intervention.
"""

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Optional, List, Callable
from enum import Enum

logger = logging.getLogger(__name__)


class TableState(Enum):
    """States for table management"""
    NO_TABLE = "no_table"           # No table exists, should create one
    CREATING = "creating"            # Table creation in progress
    WAITING = "waiting"              # Table exists, waiting for opponent
    OPPONENT_JOINED = "opponent_joined"  # Opponent joined, waiting for game start
    IN_GAME = "in_game"             # Game in progress
    GAME_ENDED = "game_ended"       # Game ended, should create new table
    ERROR = "error"                  # Error state (will retry)


@dataclass
class TableManagerConfig:
    """Configuration for table management"""
    # Table creation settings
    table_name: str = "rando_cal's table"
    create_delay_seconds: float = 2.0          # Delay before creating table
    retry_delay_seconds: float = 5.0           # Delay between retries
    max_retry_delay_seconds: float = 60.0      # Max backoff delay
    max_consecutive_failures: int = 10         # Max failures before giving up

    # Table monitoring
    stale_table_minutes: int = 60              # Consider table stale after this time
    health_check_interval: int = 30            # Seconds between health checks

    # Deck selection
    prefer_library_decks: bool = True          # Use library decks by default
    deck_rotation: bool = True                 # Rotate through decks


@dataclass
class TableManagerState:
    """Runtime state for table manager"""
    current_table_id: Optional[str] = None
    current_deck_name: Optional[str] = None
    state: TableState = TableState.NO_TABLE

    # Failure tracking
    consecutive_failures: int = 0
    last_failure_time: float = 0
    last_failure_reason: str = ""

    # Table tracking
    table_created_time: float = 0
    last_opponent_name: Optional[str] = None
    games_played: int = 0

    # Deck rotation
    deck_index: int = 0


class TableManager:
    """
    Manages table lifecycle for autonomous bot operation.

    Usage:
        manager = TableManager(client, config)
        manager.set_decks(library_decks, user_decks)

        # In main loop:
        action = manager.get_required_action(current_tables, my_username)
        if action == 'create_table':
            manager.create_table()
        elif action == 'wait':
            pass  # Continue polling
    """

    def __init__(self, client, config: Optional[TableManagerConfig] = None):
        """
        Initialize table manager.

        Args:
            client: GEMPClient instance
            config: Optional configuration (uses defaults if not provided)
        """
        self.client = client
        self.config = config or TableManagerConfig()
        self.state = TableManagerState()

        # Decks
        self.library_decks: List = []
        self.user_decks: List = []

        # Callbacks
        self._on_table_created: Optional[Callable] = None
        self._on_table_lost: Optional[Callable] = None
        self._on_game_started: Optional[Callable] = None

    def set_decks(self, library_decks: List, user_decks: List) -> None:
        """Set available decks for table creation"""
        self.library_decks = library_decks or []
        self.user_decks = user_decks or []
        logger.info(f"TableManager: {len(self.library_decks)} library decks, {len(self.user_decks)} user decks available")

    def set_callbacks(self,
                      on_table_created: Callable = None,
                      on_table_lost: Callable = None,
                      on_game_started: Callable = None) -> None:
        """Set optional callbacks for table events"""
        self._on_table_created = on_table_created
        self._on_table_lost = on_table_lost
        self._on_game_started = on_game_started

    def get_required_action(self, tables: List, my_username: str) -> str:
        """
        Determine what action is needed based on current state.

        Args:
            tables: List of current hall tables
            my_username: Bot's username

        Returns:
            Action string: 'create_table', 'wait', 'join_game', 'none'
        """
        # Find our table in the list
        my_table = self._find_my_table(tables, my_username)

        if my_table:
            # We have a table
            self.state.current_table_id = my_table.table_id
            self.state.consecutive_failures = 0  # Reset failures

            # Check table status
            if my_table.status == 'finished':
                logger.info("Table finished - need new table")
                self.state.state = TableState.GAME_ENDED
                self.state.current_table_id = None
                return 'create_table'

            # Check if game started
            if my_table.game_id:
                self.state.state = TableState.IN_GAME
                return 'join_game'

            # Check for opponent
            opponent = my_table.get_opponent(my_username) if hasattr(my_table, 'get_opponent') else None
            if opponent:
                self.state.state = TableState.OPPONENT_JOINED
                self.state.last_opponent_name = opponent.name
            else:
                self.state.state = TableState.WAITING

            return 'wait'

        else:
            # No table found
            if self.state.current_table_id:
                # We had a table but it's gone
                logger.warning(f"Table {self.state.current_table_id} disappeared!")
                self.state.current_table_id = None
                if self._on_table_lost:
                    self._on_table_lost()

            self.state.state = TableState.NO_TABLE

            # Check if we should create a table
            if self._should_create_table():
                return 'create_table'
            else:
                return 'wait'  # Backoff active

    def _find_my_table(self, tables: List, my_username: str):
        """Find our table in the list"""
        for table in tables:
            # Check if we're in this table
            players = table.players if hasattr(table, 'players') else []
            for player in players:
                player_name = player.name if hasattr(player, 'name') else str(player)
                if player_name == my_username:
                    # Skip finished tables
                    if hasattr(table, 'status') and table.status == 'finished':
                        continue
                    return table
        return None

    def _should_create_table(self) -> bool:
        """Check if we should attempt to create a table (handles backoff)"""
        if self.state.consecutive_failures == 0:
            return True

        # Calculate backoff delay
        backoff = min(
            self.config.retry_delay_seconds * (2 ** self.state.consecutive_failures),
            self.config.max_retry_delay_seconds
        )

        time_since_failure = time.time() - self.state.last_failure_time
        if time_since_failure < backoff:
            logger.debug(f"Backoff active: {backoff - time_since_failure:.1f}s remaining")
            return False

        # Check max failures
        if self.state.consecutive_failures >= self.config.max_consecutive_failures:
            logger.error(f"Max consecutive failures ({self.config.max_consecutive_failures}) reached!")
            return False

        return True

    def create_table(self) -> Optional[str]:
        """
        Create a new table with a randomly selected deck.

        Returns:
            Table ID if successful, None if failed
        """
        self.state.state = TableState.CREATING

        # Select deck
        deck = self._select_deck()
        if not deck:
            logger.error("No decks available for table creation!")
            self._record_failure("No decks available")
            return None

        deck_name = deck.name if hasattr(deck, 'name') else str(deck)
        is_library = deck in self.library_decks

        logger.info(f"Creating table with deck: {deck_name} (library={is_library})")

        try:
            table_id = self.client.create_table(
                deck_name,
                self.config.table_name,
                is_library=is_library
            )

            if table_id:
                logger.info(f"✅ Table created: {table_id}")
                self.state.current_table_id = table_id
                self.state.current_deck_name = deck_name
                self.state.table_created_time = time.time()
                self.state.state = TableState.WAITING
                self.state.consecutive_failures = 0

                if self._on_table_created:
                    self._on_table_created(table_id, deck_name)

                return table_id
            else:
                self._record_failure("create_table returned None")
                return None

        except Exception as e:
            self._record_failure(str(e))
            logger.error(f"Table creation failed: {e}")
            return None

    def _select_deck(self):
        """Select a deck for table creation"""
        decks = []

        if self.config.prefer_library_decks and self.library_decks:
            decks = self.library_decks
        elif self.user_decks:
            decks = self.user_decks
        elif self.library_decks:
            decks = self.library_decks

        if not decks:
            return None

        if self.config.deck_rotation:
            # Rotate through decks
            self.state.deck_index = (self.state.deck_index + 1) % len(decks)
            return decks[self.state.deck_index]
        else:
            # Random selection
            return random.choice(decks)

    def _record_failure(self, reason: str) -> None:
        """Record a table creation failure"""
        self.state.consecutive_failures += 1
        self.state.last_failure_time = time.time()
        self.state.last_failure_reason = reason
        self.state.state = TableState.ERROR

        logger.warning(f"Table creation failure #{self.state.consecutive_failures}: {reason}")

    def on_game_ended(self) -> None:
        """Called when a game ends"""
        self.state.state = TableState.GAME_ENDED
        self.state.games_played += 1
        self.state.current_table_id = None
        logger.info(f"Game ended. Total games played: {self.state.games_played}")

    def reset(self) -> None:
        """Reset manager state (e.g., on reconnection)"""
        self.state = TableManagerState()
        logger.info("TableManager state reset")

    def get_status(self) -> dict:
        """Get current status for monitoring"""
        return {
            'state': self.state.state.value,
            'table_id': self.state.current_table_id,
            'deck': self.state.current_deck_name,
            'failures': self.state.consecutive_failures,
            'games_played': self.state.games_played,
            'last_failure': self.state.last_failure_reason,
        }


class ConnectionMonitor:
    """
    Monitors connection health and handles recovery.

    Features:
    - Track successful/failed requests
    - Detect connection loss patterns
    - Trigger reconnection when needed
    """

    def __init__(self, client, max_failures: int = 3, recovery_delay: float = 5.0):
        """
        Initialize connection monitor.

        Args:
            client: GEMPClient instance
            max_failures: Consecutive failures before triggering recovery
            recovery_delay: Seconds to wait before recovery attempt
        """
        self.client = client
        self.max_failures = max_failures
        self.recovery_delay = recovery_delay

        # State
        self.consecutive_failures = 0
        self.last_success_time = time.time()
        self.is_connected = True
        self.recovery_attempts = 0

    def record_success(self) -> None:
        """Record a successful request"""
        self.consecutive_failures = 0
        self.last_success_time = time.time()
        self.is_connected = True

    def record_failure(self, reason: str = "") -> bool:
        """
        Record a failed request.

        Returns:
            True if recovery should be triggered
        """
        self.consecutive_failures += 1
        logger.warning(f"Connection failure #{self.consecutive_failures}: {reason}")

        if self.consecutive_failures >= self.max_failures:
            self.is_connected = False
            return True

        return False

    def attempt_recovery(self, username: str, password: str) -> bool:
        """
        Attempt to recover connection.

        Returns:
            True if recovery successful
        """
        self.recovery_attempts += 1
        logger.info(f"Connection recovery attempt #{self.recovery_attempts}")

        try:
            # Wait before retry
            time.sleep(self.recovery_delay)

            # Try to re-login
            if self.client.login(username, password):
                logger.info("✅ Connection recovered successfully")
                self.consecutive_failures = 0
                self.is_connected = True
                return True
            else:
                logger.error("Recovery failed: login unsuccessful")
                return False

        except Exception as e:
            logger.error(f"Recovery failed: {e}")
            return False

    def get_status(self) -> dict:
        """Get current status"""
        return {
            'connected': self.is_connected,
            'consecutive_failures': self.consecutive_failures,
            'recovery_attempts': self.recovery_attempts,
            'seconds_since_success': time.time() - self.last_success_time,
        }
