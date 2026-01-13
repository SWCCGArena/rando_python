"""
XML Parser for GEMP server responses.

GEMP server returns XML for most responses. This module handles
parsing that XML into our Python data structures.
"""

import xml.etree.ElementTree as ET
from typing import List, Optional, Dict, Tuple
from .models import GameTable, Player, DeckInfo, ChatMessage
import logging

logger = logging.getLogger(__name__)


class XMLParser:
    """Utilities for parsing GEMP XML responses"""

    @staticmethod
    def parse_hall_tables(xml_text: str) -> List[GameTable]:
        """
        Parse hall XML response to get list of tables.

        Actual XML structure from GEMP:
        <hall>
            <table id="..." format="Open" gameId=""
                   players="rando_cal (LIGHT),opponent (DARK)"
                   status="WAITING" statusDescription="Waiting"
                   tournament="Casual - Bot Table: Random"
                   watchable="false"/>
        </hall>
        """
        try:
            root = ET.fromstring(xml_text)
            tables = []

            # Find all table elements
            for table_elem in root.findall('.//table'):
                table_id = table_elem.get('id')
                # Table name is in the 'tournament' attribute
                tournament = table_elem.get('tournament', 'Unnamed Table')
                # Remove "Casual - " prefix if present
                table_name = tournament.replace('Casual - ', '')

                status = table_elem.get('status', 'unknown').upper()
                game_format = table_elem.get('format', 'open')
                game_id = table_elem.get('gameId')

                # Parse players from comma-separated "name (SIDE)" format
                players = []
                players_str = table_elem.get('players', '')
                if players_str:
                    # Split by comma: "rando_cal (LIGHT),opponent (DARK)"
                    for player_entry in players_str.split(','):
                        player_entry = player_entry.strip()
                        # Extract name and side from "name (SIDE)" format
                        player_name = player_entry.split(' (')[0].strip()
                        player_side = None
                        if ' (' in player_entry and player_entry.endswith(')'):
                            side_part = player_entry.split(' (')[1].rstrip(')')
                            if side_part.upper() in ('LIGHT', 'DARK'):
                                player_side = side_part.lower()
                        if player_name:
                            players.append(Player(name=player_name, side=player_side))

                if table_id:  # Only add if we have a valid table ID
                    tables.append(GameTable(
                        table_id=table_id,
                        table_name=table_name,
                        game_format=game_format,
                        status=status.lower(),
                        players=players,
                        game_id=game_id if game_id else None
                    ))

            logger.debug(f"Parsed {len(tables)} tables from hall XML")
            if len(tables) == 0:
                logger.warning(f"No tables found. Full hall XML: {xml_text}")
            return tables

        except ET.ParseError as e:
            logger.error(f"Failed to parse hall XML: {e}")
            logger.debug(f"XML content: {xml_text[:500]}")  # Log first 500 chars
            return []
        except Exception as e:
            logger.error(f"Unexpected error parsing hall XML: {e}")
            return []

    @staticmethod
    def parse_login_response(response_text: str, status_code: int) -> tuple[bool, Optional[str]]:
        """
        Parse login response.

        Args:
            response_text: Response body (may be XML or plain text)
            status_code: HTTP status code

        Returns:
            (success: bool, error_message: Optional[str])
        """
        # GEMP may return different formats
        if status_code == 200:
            # Check if response contains error
            if 'error' in response_text.lower() or 'invalid' in response_text.lower():
                return False, "Invalid credentials"
            return True, None
        elif status_code == 401:
            return False, "Authentication failed"
        else:
            return False, f"Login failed with status {status_code}"

    @staticmethod
    def parse_deck_list(xml_text: str) -> List[DeckInfo]:
        """
        Parse deck list XML response.

        GEMP returns deck lists in this format:
        <decks>
            <darkDeck>Hunt Down And Destroy The Jedi</darkDeck>
            <darkDeck>Court Of The Vile Gangster</darkDeck>
            <lightDeck>Your Insight Serves You Well</lightDeck>
        </decks>
        """
        try:
            root = ET.fromstring(xml_text)
            decks = []

            # Find all darkDeck elements
            for deck_elem in root.findall('.//darkDeck'):
                name = deck_elem.text
                if name:
                    decks.append(DeckInfo(
                        name=name,
                        is_library=True,
                        side='dark'
                    ))

            # Find all lightDeck elements
            for deck_elem in root.findall('.//lightDeck'):
                name = deck_elem.text
                if name:
                    decks.append(DeckInfo(
                        name=name,
                        is_library=True,
                        side='light'
                    ))

            logger.info(f"Parsed {len(decks)} decks from XML")
            return decks

        except ET.ParseError as e:
            logger.error(f"Failed to parse deck list XML: {e}")
            logger.error(f"XML content: {xml_text[:500]}")
            return []

    @staticmethod
    def parse_error_response(xml_text: str) -> Optional[str]:
        """
        Parse error message from XML response.

        Example:
        <error>Error message here</error>
        """
        try:
            root = ET.fromstring(xml_text)
            error_elem = root.find('.//error')
            if error_elem is not None and error_elem.text:
                return error_elem.text
            return None
        except:
            return None

    @staticmethod
    def is_xml(text: str) -> bool:
        """Check if text appears to be XML"""
        return text.strip().startswith('<')

    @staticmethod
    def parse_chat_messages(xml_text: str, last_msg_id: int = 0) -> Tuple[List[ChatMessage], int]:
        """
        Parse chat messages from XML response.

        GEMP chat XML format:
        <chat>
            <user>username1</user>
            <user>username2</user>
            <message from="username" msgId="123">message text</message>
        </chat>

        Args:
            xml_text: XML response from chat endpoint
            last_msg_id: Only return messages with ID greater than this

        Returns:
            Tuple of (list of ChatMessage objects, new highest message ID)
        """
        try:
            root = ET.fromstring(xml_text)
            messages = []
            new_last_id = last_msg_id

            # Find all message elements
            for msg_elem in root.findall('.//message'):
                from_user = msg_elem.get('from', '')
                msg_id_str = msg_elem.get('msgId', '0')
                message_text = msg_elem.text or ''

                try:
                    msg_id = int(msg_id_str)
                except ValueError:
                    msg_id = 0

                # Only include messages newer than last_msg_id
                if msg_id > last_msg_id:
                    messages.append(ChatMessage(
                        from_user=from_user,
                        message=message_text,
                        msg_id=msg_id
                    ))
                    if msg_id > new_last_id:
                        new_last_id = msg_id

            return messages, new_last_id

        except ET.ParseError as e:
            logger.error(f"Failed to parse chat XML: {e}")
            return [], last_msg_id
        except Exception as e:
            logger.error(f"Unexpected error parsing chat XML: {e}")
            return [], last_msg_id
