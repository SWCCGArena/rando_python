"""
GEMP HTTP Client

Handles all communication with the GEMP server via HTTP.
Manages session, authentication, and API calls.
"""

import requests
from typing import List, Optional, Dict
import logging
from .models import GameTable, DeckInfo, GameInfo
from .parser import XMLParser

logger = logging.getLogger(__name__)


class GEMPClient:
    """HTTP client for GEMP server communication"""

    def __init__(self, server_url: str):
        """
        Initialize GEMP client.

        Args:
            server_url: Base URL of GEMP server (e.g., http://localhost:8082/gemp-swccg-server/)
        """
        self.server_url = server_url.rstrip('/')
        self.session = requests.Session()
        self.participant_id = None
        self.logged_in = False
        self.parser = XMLParser()

        # Set a reasonable timeout for all requests
        self.timeout = 15

        logger.info(f"GEMP client initialized for {self.server_url}")

    def login(self, username: str, password: str) -> bool:
        """
        Authenticate with GEMP server.

        Args:
            username: GEMP username
            password: GEMP password

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info(f"Attempting login to {self.server_url} as '{username}'")

            response = self.session.post(
                f"{self.server_url}/login",
                data={
                    'login': username,
                    'password': password
                },
                timeout=self.timeout
            )

            logger.debug(f"Login response status: {response.status_code}")
            logger.debug(f"Login response text: {response.text[:200]}")

            # Check response
            success, error_msg = self.parser.parse_login_response(
                response.text,
                response.status_code
            )

            if success:
                logger.info("✅ Login successful")
                self.logged_in = True
                return True
            else:
                logger.error(f"❌ Login failed: {error_msg}")
                return False

        except requests.RequestException as e:
            logger.error(f"Login request failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during login: {e}", exc_info=True)
            return False

    def get_hall_tables(self) -> List[GameTable]:
        """
        Get list of current tables in the hall.

        Returns:
            List of GameTable objects
        """
        if not self.logged_in:
            logger.warning("Not logged in, cannot get hall tables")
            return []

        try:
            logger.debug("Fetching hall tables")

            response = self.session.get(
                f"{self.server_url}/hall",
                params={'participantId': 'null'},
                timeout=self.timeout
            )

            if response.status_code == 200:
                logger.debug(f"Hall XML response (first 1000 chars): {response.text[:1000]}")
                tables = self.parser.parse_hall_tables(response.text)
                logger.debug(f"Found {len(tables)} tables in hall")
                return tables
            else:
                logger.error(f"Failed to get hall: HTTP {response.status_code}")
                return []

        except requests.RequestException as e:
            logger.error(f"Hall request failed: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error fetching hall: {e}")
            return []

    def create_table(self, deck_name: str, table_name: str,
                     game_format: str = "open", is_library: bool = True) -> Optional[str]:
        """
        Create a new game table.

        Args:
            deck_name: Name of the deck to use
            table_name: Name for the table
            game_format: Game format (open, legacy, etc.)
            is_library: True for library deck, False for user deck

        Returns:
            Table ID if successful, None otherwise
        """
        if not self.logged_in:
            logger.warning("Not logged in, cannot create table")
            return None

        try:
            logger.info(f"Creating table '{table_name}' with deck '{deck_name}' (library: {is_library})")

            # GEMP create table endpoint
            response = self.session.post(
                f"{self.server_url}/hall",
                data={
                    'participantId': 'null',
                    'deckName': deck_name,
                    'sampleDeck': 'true' if is_library else 'false',
                    'format': game_format,
                    'tableDesc': table_name,  # This is the table name/description
                    'isPrivate': 'false'
                },
                timeout=self.timeout
            )

            logger.debug(f"Create table response status: {response.status_code}")
            logger.info(f"Create table response body: {response.text[:500]}")

            # Check for error in response body (GEMP may return 200 with error XML)
            error_msg = self.parser.parse_error_response(response.text)
            if error_msg:
                logger.error(f"❌ Table creation failed: {error_msg}")
                return None

            if response.status_code == 200:
                logger.info("✅ Table created successfully (HTTP 200, no error in body)")

                # Fetch hall to find our new table
                # (GEMP may not return table ID directly)
                # Try a few times with small delays in case of race condition
                import time
                for attempt in range(3):
                    if attempt > 0:
                        logger.info(f"Retrying hall fetch (attempt {attempt + 1}/3)...")
                        time.sleep(0.5)
                    tables = self.get_hall_tables()
                    if tables:
                        break

                # Find table we just created
                logger.debug(f"Looking for table '{table_name}' among {len(tables)} tables")
                for table in tables:
                    logger.debug(f"  Checking table: {table.table_id} - '{table.table_name}' - players: {[p.name for p in table.players]}")

                    # Check if it's our table
                    if any(p.name == self.session.cookies.get('loggedUser', 'rando_cal') or p.name == 'rando_cal' for p in table.players):
                        logger.info(f"Found created table: {table.table_id} - '{table.table_name}'")
                        return table.table_id

                logger.warning("Table created but couldn't find ID")
                logger.warning(f"Searched for table with us as player, but didn't find it")
                return None
            else:
                logger.error(f"Failed to create table: HTTP {response.status_code}")
                logger.debug(f"Response: {response.text[:500]}")
                return None

        except requests.RequestException as e:
            logger.error(f"Create table request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error creating table: {e}", exc_info=True)
            return None

    def get_library_decks(self) -> List[DeckInfo]:
        """
        Get list of available library (sample) decks.

        Returns:
            List of DeckInfo objects
        """
        if not self.logged_in:
            logger.warning("Not logged in, cannot get decks")
            return []

        try:
            logger.debug("Fetching library decks")

            response = self.session.get(
                f"{self.server_url}/deck/libraryList",
                params={'participantId': 'null'},
                timeout=self.timeout
            )

            if response.status_code == 200:
                # Parse deck list from response
                decks = self.parser.parse_deck_list(response.text)
                logger.info(f"Retrieved {len(decks)} library decks")
                return decks
            else:
                logger.error(f"Failed to get decks: HTTP {response.status_code}")
                logger.error(f"Response text: {response.text[:500]}")
                return []

        except requests.RequestException as e:
            logger.error(f"Get decks request failed: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error fetching decks: {e}")
            return []

    def get_user_decks(self) -> List[DeckInfo]:
        """
        Get list of user's personal decks.

        Returns:
            List of DeckInfo objects
        """
        if not self.logged_in:
            logger.warning("Not logged in, cannot get user decks")
            return []

        try:
            logger.debug("Fetching user decks")

            response = self.session.get(
                f"{self.server_url}/deck/list",
                params={'participantId': 'null'},
                timeout=self.timeout
            )

            if response.status_code == 200:
                decks = self.parser.parse_deck_list(response.text)
                for deck in decks:
                    deck.is_library = False  # Mark as personal decks
                logger.info(f"Retrieved {len(decks)} user decks")
                return decks
            else:
                logger.error(f"Failed to get user decks: HTTP {response.status_code}")
                logger.error(f"Response text: {response.text[:500]}")
                return []

        except requests.RequestException as e:
            logger.error(f"Get user decks request failed: {e}")
            return []

    def leave_table(self, table_id: str) -> bool:
        """
        Leave/drop from a table.

        Args:
            table_id: ID of the table to leave

        Returns:
            True if successful, False otherwise
        """
        if not self.logged_in:
            logger.warning("Not logged in, cannot leave table")
            return False

        try:
            logger.info(f"Leaving table {table_id}")

            # GEMP drop from table endpoint
            response = self.session.post(
                f"{self.server_url}/hall/{table_id}",
                data={
                    'participantId': 'null',
                    'action': 'drop'
                },
                timeout=self.timeout
            )

            logger.debug(f"Leave table response status: {response.status_code}")

            if response.status_code == 200:
                logger.info("✅ Successfully left table")
                return True
            else:
                logger.error(f"Failed to leave table: HTTP {response.status_code}")
                return False

        except requests.RequestException as e:
            logger.error(f"Leave table request failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error leaving table: {e}", exc_info=True)
            return False

    def logout(self):
        """Logout from GEMP server"""
        if self.logged_in:
            try:
                logger.info("Logging out from GEMP server")
                self.logged_in = False
                self.session.close()
                self.session = requests.Session()
            except Exception as e:
                logger.error(f"Logout failed: {e}")

    def join_game(self, game_id: str) -> Optional[str]:
        """
        Join a game session.

        Args:
            game_id: The game ID to join

        Returns:
            Initial game state XML if successful, None otherwise
        """
        if not self.logged_in:
            logger.warning("Not logged in, cannot join game")
            return None

        try:
            logger.info(f"Joining game {game_id}")

            # GEMP game join endpoint
            response = self.session.get(
                f"{self.server_url}/game/{game_id}",
                params={'participantId': 'null'},
                timeout=self.timeout
            )

            logger.debug(f"Join game response status: {response.status_code}")

            if response.status_code == 200:
                logger.info(f"✅ Joined game successfully ({len(response.text)} bytes)")
                # Log full XML at INFO level for debugging (initial game state is important)
                if len(response.text) < 5000:
                    logger.info(f"📄 Initial game state XML: {response.text}")
                else:
                    logger.info(f"📄 Initial game state XML (first 3000 chars): {response.text[:3000]}...")
                return response.text
            else:
                logger.error(f"Failed to join game: HTTP {response.status_code}")
                return None

        except requests.RequestException as e:
            logger.error(f"Join game request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error joining game: {e}", exc_info=True)
            return None

    def get_game_update(self, game_id: str, channel_number: int) -> Optional[str]:
        """
        Poll for game updates.

        Args:
            game_id: The game ID
            channel_number: Current channel number (increments with each update)

        Returns:
            Game update XML if successful, None otherwise
            Returns "SESSION_EXPIRED" string if 409 error (session needs refresh)
        """
        if not self.logged_in:
            return None

        try:
            # GEMP uses POST to /game/{gameId} with form data, NOT GET to /update
            response = self.session.post(
                f"{self.server_url}/game/{game_id}",
                data={
                    'participantId': 'null',
                    'channelNumber': str(channel_number)
                },
                timeout=self.timeout
            )

            if response.status_code == 200:
                logger.debug(f"✅ Game update successful (cn={channel_number}, {len(response.text)} bytes)")
                # Log XML at INFO level for debugging
                if len(response.text) < 3000:
                    logger.info(f"📄 Game update XML: {response.text}")
                else:
                    logger.info(f"📄 Game update XML (first 2000 chars): {response.text[:2000]}...")
                return response.text
            elif response.status_code == 409:
                # 409 = session expired, need to re-login
                logger.warning(f"⚠️  HTTP 409 - Session expired, need to re-login")
                self.logged_in = False
                return "SESSION_EXPIRED"
            else:
                logger.error(f"Failed to get game update: HTTP {response.status_code}")
                logger.error(f"Response: {response.text[:200]}")
                return None

        except requests.RequestException as e:
            logger.error(f"Game update request failed: {e}")
            return None

    def get_card_info(self, game_id: str, card_id: str) -> Optional[str]:
        """
        Get detailed card information (force drain amounts, icons, etc.).

        Calls the cardInfo endpoint to get HTML-formatted card details.
        Used during Control phase to check location force drain amounts
        and Battle Order rules.

        Args:
            game_id: The game ID
            card_id: The card ID to get info for

        Returns:
            HTML response with card info, or None on error
        """
        if not self.logged_in:
            return None

        try:
            response = self.session.get(
                f"{self.server_url}/game/{game_id}/cardInfo",
                params={
                    'participantId': 'null',
                    'cardId': card_id
                },
                timeout=self.timeout
            )

            if response.status_code == 200:
                logger.debug(f"✅ Card info received for {card_id} ({len(response.text)} bytes)")
                return response.text
            else:
                logger.warning(f"Failed to get card info: HTTP {response.status_code}")
                return None

        except requests.RequestException as e:
            logger.error(f"Card info request failed: {e}")
            return None

    def post_decision(self, game_id: str, channel_number: int,
                     decision_id: str, decision_value: str) -> Optional[str]:
        """
        Post a decision response to the server.

        Args:
            game_id: The game ID
            channel_number: Current channel number
            decision_id: The decision ID from the decision event
            decision_value: The response value (e.g., "0" for first option)

        Returns:
            Update XML if successful, None otherwise
        """
        if not self.logged_in:
            return None

        try:
            logger.info(f"📤 Posting decision: id={decision_id}, value={decision_value}")

            response = self.session.post(
                f"{self.server_url}/game/{game_id}",
                data={
                    'participantId': 'null',
                    'channelNumber': str(channel_number),
                    'decisionId': decision_id,
                    'decisionValue': decision_value
                },
                timeout=self.timeout
            )

            if response.status_code == 200:
                logger.info(f"✅ Decision posted successfully ({len(response.text)} bytes)")
                # Log XML at INFO level for debugging
                if len(response.text) < 3000:
                    logger.info(f"📄 Decision response XML: {response.text}")
                else:
                    logger.info(f"📄 Decision response XML (first 2000 chars): {response.text[:2000]}...")
                return response.text
            else:
                logger.error(f"Failed to post decision: HTTP {response.status_code}")
                logger.error(f"Response: {response.text[:500]}")
                return None

        except requests.RequestException as e:
            logger.error(f"Post decision request failed: {e}")
            return None

    def register_chat(self, game_id: str) -> tuple[bool, int]:
        """
        Register with the game chat server.

        Must be called once when joining a game before polling for messages.
        Based on C# GameCommsHelper.RegisterWithChatServer()

        Args:
            game_id: The game ID

        Returns:
            Tuple of (success, last_msg_id from registration)
        """
        if not self.logged_in:
            return False, 0

        try:
            # Registration is a GET request with participantId=null
            response = self.session.get(
                f"{self.server_url}/chat/Game{game_id}",
                params={'participantId': 'null'},
                timeout=self.timeout
            )

            if response.status_code == 200:
                # Parse to get initial last_msg_id
                _, last_msg_id = self.parser.parse_chat_messages(response.text, 0)
                logger.info(f"💬 Registered with chat server for game {game_id}, last_msg_id={last_msg_id}")
                return True, last_msg_id
            else:
                logger.warning(f"Failed to register chat: HTTP {response.status_code}")
                return False, 0

        except requests.RequestException as e:
            logger.error(f"Chat registration failed: {e}")
            return False, 0

    def leave_chat(self, game_id: str) -> bool:
        """
        Leave the game chat server.

        Should be called when leaving a game.

        Args:
            game_id: The game ID

        Returns:
            True if successful
        """
        # GEMP doesn't have an explicit leave - just stop polling
        # This is here for completeness and future use
        logger.info(f"💬 Left chat for game {game_id}")
        return True

    def get_chat_messages(self, game_id: str, last_msg_id: int = 0) -> tuple[list, int]:
        """
        Poll for chat messages from the game.

        Args:
            game_id: The game ID
            last_msg_id: Last message ID received (to get only new messages)

        Returns:
            Tuple of (list of ChatMessage namedtuples, new last_msg_id)
            ChatMessage has fields: (from_user, message, msg_id)
        """
        if not self.logged_in:
            return [], last_msg_id

        try:
            # Poll with POST, include latestMsgIdRcvd to get only new messages
            response = self.session.post(
                f"{self.server_url}/chat/Game{game_id}",
                data={
                    'participantId': 'null',
                    'latestMsgIdRcvd': str(last_msg_id)
                },
                timeout=self.timeout
            )

            if response.status_code == 200:
                messages, new_last_id = self.parser.parse_chat_messages(
                    response.text, last_msg_id
                )
                if messages:
                    logger.debug(f"📨 Received {len(messages)} new chat messages")
                return messages, new_last_id
            else:
                # HTTP 410 (Gone) is normal when game ends - don't warn
                if response.status_code == 410:
                    logger.debug(f"Chat room gone (game ended): HTTP 410")
                else:
                    logger.warning(f"Failed to get chat: HTTP {response.status_code}")
                return [], last_msg_id

        except requests.RequestException as e:
            logger.error(f"Get chat request failed: {e}")
            return [], last_msg_id

    def post_chat_message(self, game_id: str, message: str, username: str = None) -> bool:
        """
        Post a chat message to the game.

        Args:
            game_id: The game ID
            message: The message to send
            username: Username to post as (defaults to logged in user)

        Returns:
            True if successful, False otherwise
        """
        if not self.logged_in:
            return False

        try:
            logger.info(f"💬 Posting chat message: '{message}'")

            # Use provided username or get from session cookie
            participant = username or self.session.cookies.get('loggedUser', 'rando_cal')

            response = self.session.post(
                f"{self.server_url}/chat/Game{game_id}",
                data={
                    'participantId': participant,
                    'message': message
                },
                timeout=self.timeout
            )

            if response.status_code == 200:
                logger.info(f"✅ Chat message sent")
                return True
            else:
                logger.error(f"Failed to send chat: HTTP {response.status_code}")
                return False

        except requests.RequestException as e:
            logger.error(f"Post chat request failed: {e}")
            return False

    def __del__(self):
        """Cleanup on deletion"""
        if hasattr(self, 'session'):
            self.session.close()
