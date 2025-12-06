"""
Holiday Overlay System

Provides seasonal personality overlays for the Astrogator brain.
Holidays have special greetings, message pools, and achievements.

Currently supported:
- Life Day (Dec 1-31): Star Wars Holiday Special themed

The system is extensible - add new HolidayConfig entries to HOLIDAYS dict.
Only one holiday is active at a time (first matching wins).
"""

import logging
import random
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Dict, Callable

logger = logging.getLogger(__name__)


@dataclass
class HolidayConfig:
    """
    Configuration for a holiday overlay.

    Attributes:
        key: Unique identifier (e.g., 'life_day')
        name: Display name (e.g., 'Life Day')
        start_month: Start month (1-12)
        start_day: Start day of month
        end_month: End month (1-12)
        end_day: End day of month
        greetings: Holiday-specific greetings (replaces normal greeting)
        deck_origins: Holiday deck origin stories (mixes with regular)
        score_messages: Holiday score tier messages (mixes with regular)
        damage_messages: Holiday damage messages (mixes with regular)
        battle_messages: Holiday battle messages (mixes with regular)
        game_end_messages: Holiday game end messages (mixes with regular)
        concede_messages: Holiday concede messages (mixes with regular)
        holiday_chance: Probability of using holiday message vs regular (0.0-1.0)
        achievement_keys: List of achievement keys only available during this holiday
    """
    key: str
    name: str
    start_month: int
    start_day: int
    end_month: int
    end_day: int

    # Message pools - these mix with regular messages
    greetings: List[str] = field(default_factory=list)
    deck_origins: List[str] = field(default_factory=list)
    score_messages: Dict[str, List[str]] = field(default_factory=dict)
    damage_messages: Dict[str, List[str]] = field(default_factory=dict)
    battle_messages: Dict[str, List[str]] = field(default_factory=dict)
    game_end_win_messages: List[str] = field(default_factory=list)
    game_end_loss_messages: List[str] = field(default_factory=list)
    concede_messages: List[str] = field(default_factory=list)

    # How often to use holiday messages vs regular (0.0 = never, 1.0 = always)
    holiday_chance: float = 0.5

    # Achievement keys that are only available during this holiday
    achievement_keys: List[str] = field(default_factory=list)

    def is_active(self, check_date: date = None) -> bool:
        """Check if this holiday is currently active."""
        if check_date is None:
            check_date = date.today()

        month = check_date.month
        day = check_date.day

        # Handle same-month holidays
        if self.start_month == self.end_month:
            return (month == self.start_month and
                    self.start_day <= day <= self.end_day)

        # Handle cross-year holidays (e.g., Dec 15 - Jan 5)
        if self.start_month > self.end_month:
            # Either in end of year or start of next year
            if month == self.start_month:
                return day >= self.start_day
            elif month == self.end_month:
                return day <= self.end_day
            elif month > self.start_month or month < self.end_month:
                return True
            return False

        # Handle multi-month holidays in same year
        if month == self.start_month:
            return day >= self.start_day
        elif month == self.end_month:
            return day <= self.end_day
        elif self.start_month < month < self.end_month:
            return True

        return False


# =============================================================================
# Life Day Holiday (Dec 1-31) - Star Wars Holiday Special Themed
# =============================================================================

# The infamous 1978 Star Wars Holiday Special featured:
# - Chewbacca trying to get home to Kashyyyk for Life Day
# - His family: wife Malla, father Itchy, son Lumpy
# - Boba Fett's first appearance (animated segment)
# - Gormaanda's cooking show ("Stir, Whip, Stir, Whip!")
# - Jefferson Starship hologram performance
# - Bea Arthur singing in the cantina
# - Carrie Fisher singing the Life Day song
# - Harvey Korman as various characters
# - VR headset ("mind evaporator") entertainment
# - Acrobats and circus performers
# - Wookiee robes and Life Day orbs

LIFE_DAY_GREETINGS = [
    # Classic Life Day
    "Happy Life Day! May your journey home be swift.",
    "Life Day greetings! The Tree of Life awaits.",
    "Happy Life Day! May you make it to Kashyyyk in time for the celebration.",
    "Life Day is upon us! Time to don our ceremonial robes.",

    # Wookiee family references
    "Malla, Itchy, and Lumpy send their Life Day regards.",
    "Somewhere on Kashyyyk, a Wookiee family awaits.",
    "Happy Life Day! Lumpy is watching his favorite hologram.",
    "Life Day greetings from the treetops of Kashyyyk!",

    # Meta/self-aware references
    "Happy Life Day! Yes, the Holiday Special was real. We don't talk about it.",
    "Life Day celebrations! No VR headsets required.",
    "It's Life Day! Try not to think too hard about the Holiday Special.",
    "Happy Life Day! This game will be slightly less confusing than that special.",
]

LIFE_DAY_DECK_ORIGINS = [
    # Holiday Special references
    "while Chewie was trying to get home for Life Day",
    "from Lumpy's secret hiding spot on Kashyyyk",
    "during a Life Day celebration in the Wookiee treehouse",
    "from Gormaanda's cooking show. Stir, whip, stir, whip!",
    "while watching Jefferson Starship via hologram",
    "in Ackmena's cantina during the Imperial curfew",
    "from a very disturbing VR headset experience",
    "hidden inside a Life Day orb",
    "during Carrie Fisher's Life Day song. I have feelings now.",
    "from an animated segment that was somehow the best part",
    "while Itchy was using the Mind Evaporator",
    "from Harvey Korman's malfunctioning cooking droid",
    "during the Imperial search of the Wookiee home",
    "wrapped in ceremonial Life Day robes",
    "from a traveling circus that somehow got involved",
]

LIFE_DAY_SCORE_MESSAGES = {
    'profitable': [
        "A Life Day miracle! This route is actually sellable.",
        "The Tree of Life smiles upon this route score!",
        "Gormaanda would be proud. Stir, whip, stir, whip your way to victory!",
        "This route score is the real Holiday Special.",
        "Happy Life Day indeed! This score is a gift.",
    ],
    'promising': [
        "The Life Day orb glows with moderate approval.",
        "Somewhere, a Wookiee family nods in tentative approval.",
        "Getting warmer! Like a Kashyyyk fireplace.",
        "Lumpy would be... mildly entertained by this score.",
    ],
    'weak': [
        "This score is about as coherent as the Holiday Special plot.",
        "The Life Day spirit is... wavering.",
        "Even Gormaanda's cooking show made more sense than this score.",
        "Itchy's VR headset provided more value than this.",
    ],
    'even': [
        "This route is going nowhere, like the Holiday Special narrative.",
        "The Tree of Life is unimpressed. Very unimpressed.",
        "This is the route score equivalent of watching Lumpy's circus acrobats.",
        "Somewhere, Bea Arthur is sighing at this score.",
    ],
    'behind': [
        "The Empire will definitely find you at this rate.",
        "Even the Holiday Special had better pacing than your strategy.",
        "Life Day celebrations are in jeopardy!",
        "Malla is worried about you. Very worried.",
    ],
    'very_behind': [
        "This is worse than Harvey Korman's cooking droid malfunction.",
        "The Life Day tree has wilted. You did this.",
        "Not even the Mind Evaporator can make you forget this score.",
        "Happy Life Day... I guess someone has to lose.",
        "Lumpy is crying. Are you happy now?",
    ],
}

LIFE_DAY_DAMAGE_MESSAGES = {
    'high': [
        "Life Day is canceled! Too much destruction!",
        "The Tree of Life felt that one!",
        "That damage could power the Life Day celebrations for years!",
        "Even the Holiday Special wasn't this explosive!",
    ],
    'medium': [
        "A solid Life Day gift of destruction.",
        "The Wookiee treehouse shakes with that damage!",
        "Gormaanda approves. Stir that damage! Whip it good!",
    ],
    'low': [
        "That damage is as weak as the Holiday Special plot.",
        "Lumpy's toys pack more punch than that.",
        "The Life Day spirit does not flow through your attacks.",
    ],
}

LIFE_DAY_BATTLE_PLAYER_CRUSHING = [
    "The Life Day spirit is strong with you!",
    "This battle is a true Life Day miracle!",
    "Even the Empire couldn't stop this Life Day celebration!",
    "Chewie would be proud of this battle advantage!",
]

LIFE_DAY_BATTLE_BOT_CRUSHING = [
    "I'm about to ruin your Life Day celebration.",
    "The Empire sends their Life Day regards.",
    "This is worse than the Imperial search of Chewie's home.",
    "Happy Life Day! Here's your present: defeat.",
]

LIFE_DAY_BATTLE_CLOSE = [
    "A Life Day battle for the ages!",
    "May the Life Day spirit determine the victor!",
    "The Tree of Life watches this battle with interest.",
]

LIFE_DAY_GAME_END_WIN = [
    "Happy Life Day! Your gift is victory. GG!",
    "The Tree of Life celebrates your triumph! Life Day well spent.",
    "A Life Day miracle! You've won! Malla and Lumpy are proud.",
    "Chewie made it home AND you won. Best Life Day ever!",
    "The Life Day orb glows with your victory! Happy holidays!",
    "Victory! Now go celebrate with some Wookiee-ookies. GG!",
]

LIFE_DAY_GAME_END_LOSS = [
    "Happy Life Day anyway! I'll just keep this victory as my gift.",
    "The Empire wins Life Day this year. Sorry, Wookiees.",
    "I won on Life Day. The Holiday Special prepared me for anything.",
    "Life Day belongs to the droids this year!",
]

LIFE_DAY_CONCEDE = [
    "I concede. Happy Life Day! May the next game be better.",
    "The Life Day spirit compels me to accept defeat gracefully. GG!",
    "Even droids know when Life Day is lost. Congratulations!",
    "My Life Day gift to you: this concession. You're welcome.",
    "The Tree of Life has spoken. I yield. Happy Life Day!",
    "Life Day is about togetherness, not victory. I concede. GG!",
]

# Life Day holiday configuration
LIFE_DAY = HolidayConfig(
    key='life_day',
    name='Life Day',
    start_month=12,
    start_day=1,
    end_month=12,
    end_day=31,
    greetings=LIFE_DAY_GREETINGS,
    deck_origins=LIFE_DAY_DECK_ORIGINS,
    score_messages=LIFE_DAY_SCORE_MESSAGES,
    damage_messages=LIFE_DAY_DAMAGE_MESSAGES,
    battle_messages={
        'player_crushing': LIFE_DAY_BATTLE_PLAYER_CRUSHING,
        'bot_crushing': LIFE_DAY_BATTLE_BOT_CRUSHING,
        'close': LIFE_DAY_BATTLE_CLOSE,
    },
    game_end_win_messages=LIFE_DAY_GAME_END_WIN,
    game_end_loss_messages=LIFE_DAY_GAME_END_LOSS,
    concede_messages=LIFE_DAY_CONCEDE,
    holiday_chance=0.5,  # 50% holiday, 50% regular
    achievement_keys=[
        'achievement_life_day_celebrant',
        'achievement_wookiee_homecoming',
        'achievement_first_boba',
        'achievement_life_day_orb',
        'achievement_holiday_special',
    ]
)


# =============================================================================
# Holiday Registry
# =============================================================================

# All holidays - first matching wins
HOLIDAYS: Dict[str, HolidayConfig] = {
    'life_day': LIFE_DAY,
    # Add more holidays here:
    # 'may_the_fourth': MAY_THE_FOURTH,
    # 'halloween': HALLOWEEN,
}


class HolidayOverlay:
    """
    Manages holiday overlay functionality.

    Checks for active holidays and provides mixed message pools.
    """

    def __init__(self):
        self._current_holiday: Optional[HolidayConfig] = None
        self._last_check_date: Optional[date] = None
        self._refresh_holiday()

    def _refresh_holiday(self):
        """Check which holiday (if any) is currently active."""
        today = date.today()

        # Cache check - only refresh once per day
        if self._last_check_date == today:
            return

        self._last_check_date = today
        self._current_holiday = None

        for holiday in HOLIDAYS.values():
            if holiday.is_active(today):
                self._current_holiday = holiday
                logger.info(f"Holiday active: {holiday.name}")
                break

        if not self._current_holiday:
            logger.debug("No holiday currently active")

    @property
    def is_holiday_active(self) -> bool:
        """Check if any holiday is currently active."""
        self._refresh_holiday()
        return self._current_holiday is not None

    @property
    def current_holiday(self) -> Optional[HolidayConfig]:
        """Get the currently active holiday, if any."""
        self._refresh_holiday()
        return self._current_holiday

    @property
    def holiday_name(self) -> Optional[str]:
        """Get the name of the current holiday."""
        if self.current_holiday:
            return self.current_holiday.name
        return None

    def should_use_holiday_message(self) -> bool:
        """
        Determine if this message should be holiday-themed.

        Uses the holiday's configured chance.
        """
        if not self.current_holiday:
            return False
        return random.random() < self.current_holiday.holiday_chance

    def get_holiday_greeting(self) -> Optional[str]:
        """
        Get a holiday greeting if active.

        Returns None if no holiday or should use regular greeting.
        """
        if not self.current_holiday:
            return None

        # Greetings always use holiday during the holiday
        if self.current_holiday.greetings:
            return random.choice(self.current_holiday.greetings)
        return None

    def get_deck_origin(self, regular_origins: List[str]) -> str:
        """
        Get a deck origin story, mixing holiday and regular.
        """
        if self.current_holiday and self.should_use_holiday_message():
            if self.current_holiday.deck_origins:
                return random.choice(self.current_holiday.deck_origins)
        return random.choice(regular_origins)

    def get_score_message(self, tier: str, regular_messages: Dict[str, List[str]]) -> str:
        """
        Get a score tier message, mixing holiday and regular.
        """
        if self.current_holiday and self.should_use_holiday_message():
            holiday_msgs = self.current_holiday.score_messages.get(tier)
            if holiday_msgs:
                return random.choice(holiday_msgs)

        regular_msgs = regular_messages.get(tier, regular_messages.get('even', []))
        if regular_msgs:
            return random.choice(regular_msgs)
        return ""

    def get_damage_message(self, tier: str, regular_messages: Dict[str, List[str]]) -> str:
        """
        Get a damage tier message, mixing holiday and regular.
        """
        if self.current_holiday and self.should_use_holiday_message():
            holiday_msgs = self.current_holiday.damage_messages.get(tier)
            if holiday_msgs:
                return random.choice(holiday_msgs)

        regular_msgs = regular_messages.get(tier, [])
        if regular_msgs:
            return random.choice(regular_msgs)
        return ""

    def get_battle_message(self, message_type: str, regular_messages: List[str]) -> str:
        """
        Get a battle message, mixing holiday and regular.

        message_type: 'player_crushing', 'bot_crushing', or 'close'
        """
        if self.current_holiday and self.should_use_holiday_message():
            holiday_msgs = self.current_holiday.battle_messages.get(message_type)
            if holiday_msgs:
                return random.choice(holiday_msgs)

        return random.choice(regular_messages) if regular_messages else ""

    def get_game_end_message(self, won: bool, regular_messages: List[str]) -> str:
        """
        Get a game end message, mixing holiday and regular.
        """
        if self.current_holiday and self.should_use_holiday_message():
            if won:
                holiday_msgs = self.current_holiday.game_end_win_messages
            else:
                holiday_msgs = self.current_holiday.game_end_loss_messages

            if holiday_msgs:
                return random.choice(holiday_msgs)

        return random.choice(regular_messages) if regular_messages else ""

    def get_concede_message(self, regular_messages: List[str]) -> str:
        """
        Get a concede message, mixing holiday and regular.
        """
        if self.current_holiday and self.should_use_holiday_message():
            if self.current_holiday.concede_messages:
                return random.choice(self.current_holiday.concede_messages)

        return random.choice(regular_messages) if regular_messages else ""

    def get_holiday_achievement_keys(self) -> List[str]:
        """
        Get achievement keys that are only available during the current holiday.
        """
        if not self.current_holiday:
            return []
        return self.current_holiday.achievement_keys

    def is_holiday_achievement_active(self, achievement_key: str) -> bool:
        """
        Check if a holiday-specific achievement is currently available.
        """
        if not self.current_holiday:
            return False
        return achievement_key in self.current_holiday.achievement_keys


# Global instance for easy access
_holiday_overlay: Optional[HolidayOverlay] = None


def get_holiday_overlay() -> HolidayOverlay:
    """Get the global holiday overlay instance."""
    global _holiday_overlay
    if _holiday_overlay is None:
        _holiday_overlay = HolidayOverlay()
    return _holiday_overlay
