"""
Data models for GEMP game entities.

These are the data structures we get back from GEMP server
and use to track game state at a high level.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Player:
    """A player in the hall or game"""
    name: str
    participant_id: Optional[str] = None
    side: Optional[str] = None  # "light" or "dark" (from hall table info)


@dataclass
class GameTable:
    """A game table in the hall"""
    table_id: str
    table_name: str
    game_format: str = "open"  # "open", "legacy", etc.
    status: str = "waiting"  # "waiting", "playing", "finished"
    players: List[Player] = field(default_factory=list)
    deck_type: Optional[str] = None  # "light" or "dark"
    game_id: Optional[str] = None

    def is_available(self) -> bool:
        """Can we join this table?"""
        return self.status == "waiting" and len(self.players) < 2

    def is_rando_table(self) -> bool:
        """Is this a table created by rando?"""
        name_lower = self.table_name.lower()
        return ("bot table" in name_lower or
                "astrogation chart" in name_lower or
                "rando" in name_lower)

    def get_opponent(self, my_name: str) -> Optional[Player]:
        """Get the opponent player (not me)"""
        for player in self.players:
            if player.name != my_name:
                return player
        return None


@dataclass
class DeckInfo:
    """Information about a deck"""
    name: str
    is_library: bool  # Library deck vs personal deck
    side: Optional[str] = None  # "light" or "dark"
    code: Optional[str] = None  # Deck code/identifier


@dataclass
class GameInfo:
    """Information about an active game"""
    game_id: str
    table_id: str
    participant_id: Optional[str] = None
    opponent_name: Optional[str] = None
    my_deck: Optional[str] = None
    their_deck_type: Optional[str] = None


@dataclass
class ChatMessage:
    """A chat message from the game"""
    from_user: str
    message: str
    msg_id: int
