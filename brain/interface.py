"""
Brain Interface - The contract between the game engine and decision-making AI.

This interface allows for swappable "brains" - from static ranking algorithms
to future LLM-based decision making. The brain receives rich context about
the game state and returns a simple decision.

Key principle: The brain should be a pure function.
Given context, return decision. No side effects.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import Enum


class DecisionType(Enum):
    """Types of decisions the brain needs to make"""
    MULTIPLE_CHOICE = "multiple_choice"
    CARD_SELECTION = "card_selection"
    CARD_ACTION = "card_action"
    INTEGER_INPUT = "integer"
    ARBITRARY_CARDS = "arbitrary_cards"


@dataclass
class CardInfo:
    """
    Complete metadata about a card for brain decision-making.

    This is everything the brain might need to know about a card
    to make an intelligent decision.
    """
    card_id: str
    blueprint_id: str
    title: str
    type: str  # Character, Starship, Location, Effect, Interrupt, etc.

    # Combat stats
    power: Optional[int] = None
    ability: Optional[int] = None
    deploy_cost: Optional[int] = None
    forfeit: Optional[int] = None
    destiny: Optional[int] = None

    # Card properties
    icons: List[str] = field(default_factory=list)  # Pilot, Warrior, etc.
    game_text: str = ""

    # Location in game
    zone: str = "UNKNOWN"  # "IN_PLAY", "HAND", "FORCE_PILE", etc.
    location: Optional[str] = None  # Which location it's at (if in play)
    owner: str = "unknown"  # "me" or "opponent"

    # Attachments
    attached_to: Optional[str] = None  # Card ID it's attached to
    attachments: List[str] = field(default_factory=list)  # Card IDs attached to this


@dataclass
class LocationState:
    """
    State of a location on the board.

    Includes power totals, cards present, and threat assessment
    for improved AI decision-making.
    """
    location_id: str
    title: str
    blueprint_id: str
    system: Optional[str] = None  # Parent system if this is a site

    # Power and ability at this location
    my_power: int = 0
    their_power: int = 0
    my_ability: int = 0
    their_ability: int = 0

    # Cards present
    my_cards: List[CardInfo] = field(default_factory=list)
    their_cards: List[CardInfo] = field(default_factory=list)

    # Threat assessment (for improved AI)
    adjacent_locations: List[str] = field(default_factory=list)  # Nearby locations
    their_adjacent_power: int = 0  # Total power at adjacent locations (move-in threat)

    # Battle state
    battle_in_progress: bool = False

    # Force drain
    my_force_drain_amount: int = 0
    their_force_drain_amount: int = 0


@dataclass
class ZoneState:
    """State of a game zone (hand, piles, etc.)"""
    hand: List[CardInfo] = field(default_factory=list)
    hand_size: int = 0  # Might not know exact cards in opponent's hand

    force_pile_size: int = 0
    used_pile_size: int = 0
    lost_pile_size: int = 0
    reserve_deck_size: int = 0
    out_of_play_size: int = 0

    # Top cards (if visible)
    force_pile_top: Optional[CardInfo] = None
    lost_pile_top: Optional[CardInfo] = None


@dataclass
class BoardState:
    """
    Complete game state.

    This is the primary input to the brain - everything it needs
    to know about the current game situation.
    """
    turn_number: int = 0
    phase: str = "UNKNOWN"  # "DEPLOY", "BATTLE", "MOVE", "DRAW", etc.
    current_player: str = "unknown"  # "me" or "opponent"

    # Locations on the board
    locations: List[LocationState] = field(default_factory=list)

    # Player zones
    my_zones: ZoneState = field(default_factory=ZoneState)
    their_zones: ZoneState = field(default_factory=ZoneState)

    # Force generation
    my_force_generation: int = 0
    their_force_generation: int = 0

    # Quick reference totals
    my_total_power: int = 0
    their_total_power: int = 0
    my_total_force: int = 0  # force_pile + used_pile + reserve
    their_total_force: int = 0

    # Game metadata
    my_side: str = "unknown"  # "light" or "dark"
    their_side: str = "unknown"
    opponent_name: str = "Unknown"


@dataclass
class DecisionOption:
    """
    A single option the brain can choose.

    The engine provides all available options with metadata
    to help the brain rank them.
    """
    option_id: str
    option_type: str
    display_text: str

    # Context-specific metadata
    card: Optional[CardInfo] = None
    target_location: Optional[LocationState] = None
    integer_range: Optional[Tuple[int, int]] = None  # (min, max) for integer decisions

    # Pre-calculated metrics (to help brain)
    power_differential: Optional[int] = None
    estimated_value: Optional[float] = None

    # For brain's internal use during ranking
    rank: int = 0
    reason: str = ""


@dataclass
class DecisionRequest:
    """
    A decision the brain needs to make.

    The engine presents a decision with all available options,
    and the brain returns which option to choose.
    """
    decision_id: str
    decision_type: DecisionType
    prompt: str  # Human-readable question from GEMP
    options: List[DecisionOption] = field(default_factory=list)
    timeout_seconds: int = 30  # How long before auto-forfeit

    # For INTEGER decisions (force activation)
    min_value: int = 0
    max_value: int = 0
    default_value: int = 0  # Default value for INTEGER decisions

    # For ACTION_CHOICE decisions - can we pass/decline?
    no_pass: bool = True  # True = must select an action, False = can pass


@dataclass
class GameHistory:
    """
    Historical context for pattern recognition.

    Allows the brain to learn from previous turns and adapt
    to opponent behavior.
    """
    previous_decisions: List[Tuple[str, str, str]] = field(default_factory=list)  # (type, choice, outcome)
    turns_elapsed: int = 0

    # Opponent identification
    opponent_name: str = "Unknown"
    opponent_deck_type: str = "unknown"  # "dark" or "light"

    # Opponent behavior tracking (for improved AI)
    opponent_has_interrupted_battle: bool = False
    opponent_typical_hand_size: float = 7.0  # Running average
    locations_opponent_prioritizes: List[str] = field(default_factory=list)

    # Cards we've seen opponent play
    opponent_cards_seen: List[CardInfo] = field(default_factory=list)


@dataclass
class BrainContext:
    """
    Everything the brain needs to make a decision.

    This is the complete input to brain.make_decision().
    It contains all relevant game state, history, and configuration.
    """
    board_state: BoardState
    decision_request: DecisionRequest
    game_history: GameHistory

    # Strategy context
    current_strategy: str = "NEUTRAL"  # "DEPLOY", "HOLD", "AGGRESSIVE", etc.

    # Configuration (from admin UI)
    deploy_threshold: int = 3
    max_hand_size: int = 12

    def to_dict(self) -> dict:
        """Convert to dictionary for logging/serialization"""
        return {
            'turn': self.board_state.turn_number,
            'phase': self.board_state.phase,
            'my_power': self.board_state.my_total_power,
            'their_power': self.board_state.their_total_power,
            'my_force': self.board_state.my_total_force,
            'their_force': self.board_state.their_total_force,
            'decision_type': self.decision_request.decision_type.value,
            'num_options': len(self.decision_request.options),
            'strategy': self.current_strategy,
        }


@dataclass
class BrainDecision:
    """
    The brain's decision output.

    Simple structure: what to do and why.
    """
    choice: str  # option_id to execute (or "" to pass)
    reasoning: str  # Human-readable explanation for logging/debugging
    confidence: float  # 0.0 to 1.0, how confident the brain is
    alternative_considered: Optional[str] = None  # 2nd best option (for logging)

    def to_dict(self) -> dict:
        """Convert to dictionary for logging"""
        return {
            'choice': self.choice,
            'reasoning': self.reasoning,
            'confidence': self.confidence,
            'alternative': self.alternative_considered,
        }


class Brain(ABC):
    """
    Abstract base class for all decision-making implementations.

    This is the core interface that allows swapping between different
    AI approaches:
    - StaticBrain: Traditional ranking-based logic (port of Unity)
    - AstrogatorBrain: Extends StaticBrain with personality
    - LLMBrain: Future LLM-based decision making
    - RandomBrain: For testing

    The engine only calls these methods - it doesn't care how the
    brain makes decisions internally.
    """

    @abstractmethod
    def make_decision(self, context: BrainContext) -> BrainDecision:
        """
        Given game context, return a decision.

        This is the ONLY method the engine calls during gameplay.
        The brain can do whatever it wants internally - rankings,
        LLM API calls, Monte Carlo tree search, etc.

        Args:
            context: Complete game state and decision request

        Returns:
            BrainDecision with choice and reasoning
        """
        pass

    @abstractmethod
    def on_game_start(self, opponent_name: str, my_deck: str, their_deck_type: str):
        """
        Called when a game begins.

        Use this to initialize any per-game state, load opponent stats
        from database, etc.

        Args:
            opponent_name: Name of opponent
            my_deck: Deck we're using
            their_deck_type: "light" or "dark"
        """
        pass

    @abstractmethod
    def on_game_end(self, won: bool, final_state: BoardState):
        """
        Called when a game ends.

        Use this for learning, updating stats, checking achievements, etc.

        Args:
            won: True if we won, False if we lost
            final_state: Final board state
        """
        pass

    @abstractmethod
    def get_personality_name(self) -> str:
        """
        Return brain personality name.

        Examples: 'Astrogator', 'Standard', 'LLM-GPT4', etc.
        """
        pass

    def on_turn_start(self, turn_number: int, board_state: BoardState):
        """
        Called at the start of each turn (optional to override).

        Use this for turn-by-turn commentary, strategy updates, etc.
        """
        pass

    def get_welcome_message(self, opponent_name: str, deck_name: str) -> str:
        """
        Get welcome message to send in chat when game starts (optional).

        Default implementation returns generic message.
        Override for personality-specific greetings.
        """
        return f"Hello {opponent_name}! Good luck!"

    def get_game_end_message(self, won: bool, score: Optional[int] = None) -> str:
        """
        Get message to send in chat when game ends (optional).

        Default implementation returns generic message.
        Override for personality-specific messages.
        """
        if won:
            return "Good game! Victory achieved."
        else:
            return "Good game! Well played."
