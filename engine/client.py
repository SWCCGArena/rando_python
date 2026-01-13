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

    # SAFETY: Block connections to live server in dev/test environments
    BLOCKED_HOSTS = ['gemp.starwarsccg.org', 'www.starwarsccg.org']

    def __init__(self, server_url: str):
        """
        Initialize GEMP client.

        Args:
            server_url: Base URL of GEMP server (e.g., http://localhost:8082/gemp-swccg-server/)
        """
        # SAFETY CHECK: Block any connection to live production server
        for blocked in self.BLOCKED_HOSTS:
            if blocked in server_url.lower():
                raise RuntimeError(
                    f"BLOCKED: Cannot connect to production server '{blocked}'. "
                    f"This build is for local testing only. "
                    f"Set GEMP_SERVER_URL to localhost or remove this safety check."
                )

        self.server_url = server_url.rstrip('/')
        self.session = requests.Session()
        self.participant_id = None
        self.logged_in = False
        self.parser = XMLParser()

        # Set default headers to match real browser client
        # This helps with Cloudflare and server compatibility
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/xml, text/xml, */*; q=0.01',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'X-Requested-With': 'XMLHttpRequest',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
        })

        # Set a reasonable timeout for all requests
        self.timeout = 15

        # Track last error for better diagnostics
        self.last_error: Optional[str] = None

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

    def get_hall_tables(self, return_channel_number: bool = False):
        """
        Get list of current tables in the hall.

        Args:
            return_channel_number: If True, returns (tables, channel_number) tuple

        Returns:
            List of GameTable objects, or (tables, channel_number) if return_channel_number=True
        """
        if not self.logged_in:
            logger.warning("Not logged in, cannot get hall tables")
            return ([], 0) if return_channel_number else []

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

                if return_channel_number:
                    channel_number = self._parse_hall_channel_number(response.text, 0)
                    logger.debug(f"Hall channel number: {channel_number}")
                    return tables, channel_number
                return tables
            else:
                logger.error(f"Failed to get hall: HTTP {response.status_code}")
                return ([], 0) if return_channel_number else []

        except requests.RequestException as e:
            logger.error(f"Hall request failed: {e}")
            return ([], 0) if return_channel_number else []
        except Exception as e:
            logger.error(f"Unexpected error fetching hall: {e}")
            return ([], 0) if return_channel_number else []

    def update_hall(self, channel_number: int) -> tuple:
        """
        Poll for hall updates using incremental endpoint.

        This is the proper way to poll the hall - using POST to /hall/update
        with a channel number, rather than GET /hall which fetches full state.

        Matches web client's updateHall() in communication.js

        Args:
            channel_number: Last known hall channel number

        Returns:
            Tuple of (List[GameTable], new_channel_number)
        """
        if not self.logged_in:
            logger.warning("Not logged in, cannot update hall")
            return [], channel_number

        try:
            request_data = {
                'channelNumber': str(channel_number),
                'participantId': 'null'
            }
            logger.info(f"📡 Hall update request: POST /hall/update data={request_data}")

            # Match exact request format from web client
            response = self.session.post(
                f"{self.server_url}/hall/update",
                data=request_data,
                timeout=20  # Match web client timeout
            )

            if response.status_code == 200:
                logger.debug(f"Hall update response (first 1000 chars): {response.text[:1000]}")
                tables = self.parser.parse_hall_tables(response.text)

                # Extract new channel number from response
                new_cn = self._parse_hall_channel_number(response.text, channel_number)

                logger.debug(f"Hall update: {len(tables)} tables, channel {channel_number} -> {new_cn}")
                return tables, new_cn
            elif response.status_code == 409:
                # 409 Conflict - channel number is stale, fall back to full GET
                logger.warning(f"⚠️  Hall update 409 (stale channel), falling back to GET /hall")
                return self.get_hall_tables(return_channel_number=True)
            else:
                logger.error(f"Failed to update hall: HTTP {response.status_code}")
                # Log response body for debugging
                if response.status_code in [401, 403]:
                    logger.error(f"Response body: {response.text[:500]}")
                return [], channel_number

        except requests.RequestException as e:
            logger.error(f"Hall update request failed: {e}")
            return [], channel_number
        except Exception as e:
            logger.error(f"Unexpected error updating hall: {e}")
            return [], channel_number

    def _parse_hall_channel_number(self, xml_text: str, default: int) -> int:
        """
        Parse channel number from hall update XML response.

        The channel number is in the root element attribute.

        Args:
            xml_text: Hall XML response
            default: Default value if parsing fails

        Returns:
            Channel number from response, or default
        """
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(xml_text)
            # Channel number is typically in root element or hall element
            cn_str = root.get('channelNumber', '')
            if cn_str:
                return int(cn_str)

            # Also try looking for it in hall element
            hall_elem = root.find('.//hall')
            if hall_elem is not None:
                cn_str = hall_elem.get('channelNumber', '')
                if cn_str:
                    return int(cn_str)

            return default
        except Exception:
            return default

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

                # Find table we just created - match by table name AND our username
                my_username = self.session.cookies.get('loggedUser', '')
                logger.debug(f"Looking for table '{table_name}' among {len(tables)} tables (my_username={my_username})")
                for table in tables:
                    logger.debug(f"  Checking table: {table.table_id} - '{table.table_name}' - players: {[p.name for p in table.players]}")

                    # Must match table name we requested AND have us as a player
                    if table.table_name == table_name and any(p.name == my_username for p in table.players):
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

    def join_table(self, table_id: str, deck_name: str, is_library: bool = True) -> bool:
        """
        Join an existing table as a player.

        Args:
            table_id: ID of the table to join
            deck_name: Name of the deck to use
            is_library: True for library deck, False for user deck

        Returns:
            True if successful, False otherwise
        """
        if not self.logged_in:
            logger.warning("Not logged in, cannot join table")
            return False

        try:
            logger.info(f"Joining table '{table_id}' with deck '{deck_name}' (library: {is_library})")

            # GEMP join table endpoint: POST /hall/{tableId}
            response = self.session.post(
                f"{self.server_url}/hall/{table_id}",
                data={
                    'deckName': deck_name,
                    'sampleDeck': 'true' if is_library else 'false',
                },
                timeout=self.timeout
            )

            logger.debug(f"Join table response status: {response.status_code}")
            logger.debug(f"Join table response body: {response.text[:500]}")

            # Check for error in response body
            error_msg = self.parser.parse_error_response(response.text)
            if error_msg:
                logger.error(f"❌ Join table failed: {error_msg}")
                return False

            if response.status_code == 200:
                logger.info(f"✅ Successfully joined table {table_id}")
                return True
            else:
                logger.error(f"Failed to join table: HTTP {response.status_code}")
                return False

        except requests.RequestException as e:
            logger.error(f"Join table request failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error joining table: {e}", exc_info=True)
            return False

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
            # longPollingInterval controls how long server waits before responding
            # Use 100ms for local fast mode, 3000ms (3s) for production
            import os
            local_fast = os.environ.get('LOCAL_FAST_MODE', 'false').lower() == 'true'
            long_poll_interval = 100 if local_fast else 3000

            response = self.session.post(
                f"{self.server_url}/game/{game_id}",
                data={
                    'participantId': 'null',
                    'channelNumber': str(channel_number),
                    'longPollingInterval': str(long_poll_interval)
                },
                timeout=self.timeout
            )

            if response.status_code == 200:
                self.last_error = None  # Clear error on success
                logger.debug(f"✅ Game update successful (cn={channel_number}, {len(response.text)} bytes)")
                # Log XML at DEBUG level (coordinator already logs timing)
                if len(response.text) < 3000:
                    logger.debug(f"📄 Game update XML: {response.text}")
                else:
                    logger.debug(f"📄 Game update XML (first 2000 chars): {response.text[:2000]}...")
                return response.text
            elif response.status_code == 409:
                # 409 = session expired, need to re-login
                self.last_error = "Session expired (HTTP 409)"
                logger.warning(f"⚠️  HTTP 409 - Session expired, need to re-login")
                self.logged_in = False
                return "SESSION_EXPIRED"
            else:
                self.last_error = f"HTTP {response.status_code}: {response.text[:100]}"
                logger.error(f"Failed to get game update: HTTP {response.status_code}")
                logger.error(f"Response: {response.text[:200]}")
                return None

        except requests.RequestException as e:
            self.last_error = str(e)
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
                logger.debug(f"✅ Decision posted successfully ({len(response.text)} bytes)")
                # Log XML at DEBUG level (coordinator already logs timing)
                if len(response.text) < 3000:
                    logger.debug(f"📄 Decision response XML: {response.text}")
                else:
                    logger.debug(f"📄 Decision response XML (first 2000 chars): {response.text[:2000]}...")
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
            elif response.status_code == 410:
                # HTTP 410 means we were removed from chat room due to inactivity
                # Re-register with GET to rejoin the room
                logger.warning(f"⚠️ Removed from chat room (410) - re-registering...")
                success, new_msg_id = self.register_chat(game_id)
                if success:
                    logger.info(f"✅ Re-registered with chat room (last_msg_id={new_msg_id})")
                    return [], new_msg_id  # Return new msg_id so we don't miss messages
                else:
                    logger.error(f"❌ Failed to re-register with chat room")
                    return [], last_msg_id
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

    def concede_game(self, game_id: str) -> bool:
        """
        Concede the current game.

        Posts to the GEMP concede endpoint to forfeit the game.
        Used when we detect we can't meaningfully act anymore.

        Based on C# GameCommsHelper.PostConcede()

        Args:
            game_id: The game ID to concede

        Returns:
            True if concede was successful
        """
        if not self.logged_in:
            return False

        try:
            logger.info(f"🏳️ Conceding game {game_id}")

            response = self.session.post(
                f"{self.server_url}/game/{game_id}/concede",
                data={
                    'participantId': 'null'
                },
                timeout=self.timeout
            )

            if response.status_code == 200:
                logger.info(f"✅ Game conceded successfully")
                return True
            else:
                logger.error(f"Failed to concede: HTTP {response.status_code}")
                logger.error(f"Response: {response.text[:200]}")
                return False

        except requests.RequestException as e:
            logger.error(f"Concede request failed: {e}")
            return False

    def __del__(self):
        """Cleanup on deletion"""
        if hasattr(self, 'session'):
            self.session.close()
