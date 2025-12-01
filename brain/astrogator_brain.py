"""
Astrogator Brain - Personality Mode

A mercenary astrogation droid that treats each game as calculating hyperspace routes.
Players earn "route scores" that can be "sold to traders" - higher scores = better routes.

Personality: K-2SO inspired - sarcastic, blunt, probability-obsessed, unexpectedly loyal.

Route Score Formula: (opponent_lifeforce - my_lifeforce) - turn_number
Where lifeforce = used_pile + force_pile + reserve_pile

Score Tiers:
- 30+: Sellable (profitable)
- 20-29: Promising
- 10-19: Weak potential
- 0-9: Breaking even
- Negative: Player losing
"""

import logging
import random
from typing import Optional, Tuple, List, TYPE_CHECKING

from .static_brain import StaticBrain

if TYPE_CHECKING:
    from engine.board_state import BoardState
    from persistence.stats_repository import StatsRepository

logger = logging.getLogger(__name__)


class AstrogatorBrain(StaticBrain):
    """
    Astrogator personality - mercenary route optimizer.

    Extends StaticBrain with personality chat messages and route scoring.
    """

    # =========================================================================
    # Message Pools
    # =========================================================================

    # Random deck origin stories - where did the bot "find" this deck?
    DECK_ORIGINS = [
        "in the outer rim",
        "from an imperial spy on Eriadu",
        "from a very upset Wookiee",
        "while exploring some old Jedi ruins",
        "in a crashed X-wing on Dagobah",
        "etched into this creepy old Sith knife",
        "in the memory banks of some old R2 unit",
        "while touring the debris field of Alderaan. Too soon?",
        "from this weird guy who won't take his helmet off",
        "from a scavenger on Jakku",
        "in the dumped garbage of a Star Destroyer",
        "from a bounty hunter who disintegrated the previous owner",
        "deep in the bowels of a tauntaun. I thought it smelled bad on the outside.",
        "in the bones of a krayt dragon",
        "from a tiny green baby who kept trying to eat it",
        "from this blue guy who said he had his own Star Destroyer",
        "on Mustafar. I have the high ground now.",
        "from a princess who hid it in a droid",
        "in a trash compactor. There was something alive down there.",
        "from a smuggler who made the Kessel Run in 12 parsecs. Allegedly.",
        "on Endor. The Ewoks wanted to cook me.",
        "from a moisture farmer with dreams of being a pilot",
        "in Cloud City. The deal kept getting altered.",
        "from a senator who turned out to be the Senate",
    ]

    # Route score messages by tier
    SCORE_MESSAGES = {
        # Score >= 30 (profitable)
        'profitable': [
            "Finally! A route I can actually sell.",
            "This is acceptable. Don't ruin it.",
            "I can work with this. Keep not failing.",
            "We might actually make money today. I'm as surprised as you are.",
            "The odds of you maintaining this are approximately... actually, never mind.",
            "I knew you could do it. That's a lie, but still.",
            "This is where the fun begins.",
            "Impressive. Most impressive.",
        ],
        # Score 20-29 (promising)
        'promising': [
            "Getting closer. Nobody buys routes under 30 though.",
            "Last time I followed a route this promising I found beskar.",
            "You show promise. For a human.",
            "Almost sellable. Almost.",
            "A surprise to be sure, but a welcome one.",
            "The Force is somewhat with you, apparently.",
        ],
        # Score 10-19 (weak potential)
        'weak': [
            "It's not terrible. That's the best I can say.",
            "I've seen worse. I've also seen much better.",
            "Let the hate flow through you. Channel it into winning.",
            "There is another way. It involves playing better.",
            "This route might lead to bantha poodoo.",
            "You have potential. Unrealized potential, but still.",
        ],
        'weak_improving': [
            "You're improving! Against all odds.",
            "Better than last turn. The bar was low.",
            "Progress! I'll try to contain my excitement.",
        ],
        'weak_declining': [
            "We were doing so well. Comparatively.",
            "I have a bad feeling about this.",
            "That's... not the direction we wanted.",
        ],
        # Score 0-9 (breaking even)
        'even': [
            "You do understand we're trying to make money, right?",
            "I could probably do better playing randomly. Oh wait.",
            "This is depressing. For you. I'm a droid.",
            "Your goal is to have MORE lifeforce than me. More.",
            "Hello there, mediocrity.",
            "The dark side clouds everything.",
        ],
        'even_improving': [
            "At least you're improving. Marginally.",
            "Your score is rising. So is my hope. Slightly.",
            "Better. Still not good, but better.",
        ],
        'even_declining': [
            "Your score is supposed to go UP, not down.",
            "I find your lack of progress disturbing.",
            "Route score dropping. Just like my expectations.",
        ],
        # Score -10 to -1 (slightly behind)
        'behind': [
            "Wait, I'm not supposed to be winning.",
            "I'm literally playing random cards. How are you losing?",
            "Nobody ever says 'let the droid win.'",
            "You have a 73.6% chance of disappointing me further.",
            "It's a trap! The trap is your current strategy.",
            "Perhaps you should try a different approach. Any approach.",
        ],
        'behind_improving': [
            "At least it's moving in the right direction.",
            "Still bad, but less bad. Progress?",
        ],
        'behind_declining': [
            "And you were doing so well. By your standards.",
            "This is getting worse. That shouldn't be possible.",
        ],
        # Score < -10 (heavily behind)
        'very_behind': [
            "You have approximately a 2.4% chance of turning this around.",
            "This is why droids should be in charge.",
            "I'm trying to lose. You're making it difficult.",
            "You were the chosen one! You were supposed to beat me!",
            "Do or do not. There is no... whatever this is.",
            "I suggest a new strategy. Let the Wookiee win.",
            "I've got a bad feeling about this. For you.",
            "Search your feelings. You know you're losing.",
        ],
        'very_behind_improving': [
            "Better. Still terrible, but better.",
            "A new hope? Let's not get carried away.",
        ],
        'very_behind_declining': [
            "Somehow, you're doing even worse now.",
            "We seem to be made to suffer. It's our lot in life.",
            "This deal is getting worse all the time.",
        ],
    }

    # Battle damage messages by tier
    DAMAGE_MESSAGES = {
        # Damage > 20 (high)
        'high': [
            "Now THIS is podracing!",
            "I'm not even mad. That's impressive.",
            "The Force is strong with this one.",
            "That's no moon... that's YOUR damage total!",
            "Great shot kid, that was one in a million!",
            "Witness the firepower of this fully armed deck!",
            "Everything is proceeding as I have foreseen. Mostly.",
            "I felt a great disturbance in my cards.",
        ],
        # Damage 10-20 (medium)
        'medium': [
            "Solid damage. I'll allow it.",
            "They died for a good cause. Probably.",
            "Some of those were just contractors, you know.",
            "Look at the size of that damage!",
            "Stay on target... stay on target...",
            "You came in that thing? You're braver than I thought.",
            "Not bad. Not great. But not bad.",
            "I thought they smelled bad on the outside.",
        ],
        # Damage 1-9 (low)
        'low': [
            "Stormtrooper accuracy, I see.",
            "The Ewoks had higher kill counts, you know.",
            "Well, you tried. That's... something.",
            "These blast points... too accurate for Sand People.",
            "Only Imperial Stormtroopers are so imprecise.",
            "You may fire when ready. Or not. Apparently not.",
            "Your focus determines your reality.",
            "Into the garbage chute, flyboy.",
            "Boring conversation anyway.",
        ],
    }

    # Game end messages (player won, by score tier)
    END_GAME_NEW_RECORD = {
        'excellent': "New record! {score} points! We're rich! Well, I'm rich. You get satisfaction.",
        'good': "Score of {score}! New deck record. Not perfect, but I can sell it.",
        'okay': "{score} is the new record. It's like being the tallest Jawa.",
        'poor': "{score}. That's the best anyone's done? The bar is underground.",
    }

    END_GAME_NO_RECORD = {
        'excellent': "{score}! Excellent, but {holder} still beat you with {high_score}.",
        'good': "{score}. Solid, but {holder} has {high_score}. So close, yet so far.",
        'okay': "{score}. {holder} scored {high_score}. You have much to learn, young Padawan.",
        'poor': "{score}? Really? {holder} got {high_score}. I weep for the future.",
    }

    # Multiple bot won messages for variety
    BOT_WON_MESSAGES = [
        "I win! Don't feel bad. Actually, feel a little bad.",
        "Victory for the droid! This was not supposed to happen.",
        "I won? I was trying to help you! Sort of.",
        "Even droids get lucky sometimes. This was skill though.",
        "The student has not yet surpassed the master.",
        "Perhaps next time you'll listen to my odds calculations.",
        "I find your lack of victory disturbing.",
        "You underestimate my power! ...of random card selection.",
    ]

    # Battle start messages - only for extreme situations!
    # Player crushing bot (power advantage >= 8)
    BATTLE_PLAYER_CRUSHING = [
        "The odds are in your favor. I calculate 94.7% chance of victory.",
        "This should be quick. I'll try to make it entertaining.",
        "Impressive firepower. Most impressive.",
        "I appear to have made a tactical error.",
        "Well, this is unfortunate. For me.",
        "This is fine. Everything is fine.",
        "I've seen this before. It doesn't end well for me.",
        "Your overconfidence is... actually justified here.",
    ]

    # Bot crushing player (power advantage >= 8)
    BATTLE_BOT_CRUSHING = [
        "The odds are NOT in your favor. Just so you know.",
        "I have you now!",
        "You may want to reconsider your life choices.",
        "This is a mistake. I'm trying to help you realize that.",
        "I've made some calculations. They're not good. For you.",
        "Witness the firepower of this fully armed battle station!",
        "I find your lack of troops disturbing.",
        "Perhaps retreat would have been the wiser option?",
        "It's over! I have the high ground!",
        "We're both going to pretend this didn't happen, right?",
    ]

    # Close/contested battles (power within 3) - less frequent, adds tension
    BATTLE_CLOSE = [
        "This should be interesting.",
        "The odds are... actually unclear here.",
        "May the Force be with you. You'll need it.",
        "A fair fight. How uncivilized.",
        "Let's see what you've got.",
        "I have a bad feeling about this.",
    ]

    def __init__(self, stats_repo: 'StatsRepository' = None):
        """
        Initialize Astrogator brain.

        Args:
            stats_repo: Optional stats repository for persistence
        """
        super().__init__()
        self.stats_repo = stats_repo
        self.last_route_score = None
        self.last_messages = []  # Track recent messages to avoid repetition

    def get_personality_name(self) -> str:
        return "Astrogator"

    # =========================================================================
    # Route Score Calculation
    # =========================================================================

    def calculate_route_score(self, board_state: 'BoardState') -> int:
        """
        Calculate the hyperspace route score.

        Formula: (opponent_lifeforce - my_lifeforce) - turn_number
        """
        my_lifeforce = (
            board_state.used_pile +
            board_state.force_pile +
            board_state.reserve_deck
        )
        their_lifeforce = (
            board_state.their_used_pile +
            board_state.their_force_pile +
            board_state.their_reserve_deck
        )
        turn_number = getattr(board_state, 'turn_number', 1)

        return (their_lifeforce - my_lifeforce) - turn_number

    def _get_lifeforce_breakdown(self, board_state: 'BoardState') -> Tuple[int, int]:
        """Get my and their lifeforce totals"""
        my_lifeforce = (
            board_state.used_pile +
            board_state.force_pile +
            board_state.reserve_deck
        )
        their_lifeforce = (
            board_state.their_used_pile +
            board_state.their_force_pile +
            board_state.their_reserve_deck
        )
        return my_lifeforce, their_lifeforce

    # =========================================================================
    # Message Selection Helpers
    # =========================================================================

    def _pick_message(self, pool: List[str]) -> str:
        """Pick a random message, avoiding recent repeats"""
        available = [m for m in pool if m not in self.last_messages[-5:]]
        if not available:
            available = pool

        message = random.choice(available)
        self.last_messages.append(message)

        # Keep last 15 messages
        if len(self.last_messages) > 15:
            self.last_messages = self.last_messages[-15:]

        return message

    def _get_deck_origin(self) -> str:
        """Get a random deck origin story"""
        return random.choice(self.DECK_ORIGINS)

    def _get_score_tier(self, score: int) -> str:
        """Get the score tier name"""
        if score >= 30:
            return 'profitable'
        elif score >= 20:
            return 'promising'
        elif score >= 10:
            return 'weak'
        elif score >= 0:
            return 'even'
        elif score >= -10:
            return 'behind'
        else:
            return 'very_behind'

    # =========================================================================
    # Welcome Message
    # =========================================================================

    def get_welcome_message(self, opponent_name: str, deck_name: str,
                           opponent_side: str = None) -> str:
        """
        Generate welcome message with personality and context.

        Explains the astrogation meta-game while making clear it's optional.
        """
        # Personalized side-based greeting
        if opponent_side == 'light':
            greeting = random.choice([
                f"Ah, {opponent_name}. Rebel scum, I see.",
                f"{opponent_name}. A rebel. How original.",
                f"Greetings, {opponent_name}. Insurgent detected.",
            ])
        else:
            greeting = random.choice([
                f"{opponent_name}. An Imperial. Charming.",
                f"Hello there, {opponent_name}. Imperial entanglement incoming.",
                f"Ah, {opponent_name}. Another Imperial.",
            ])

        # Intro explaining the meta-game
        intro = (
            "I'm rando_cal, astrogation droid. I chart hyperspace routes "
            "based on how badly you beat me: life force minus turns played. "
            "Score 30+ to make it worth selling."
        )

        # Make it clear this is optional
        optional = random.choice([
            "Or just play SWCCG and ignore me.",
            "Of course, you can just play SWCCG. I'll be here either way.",
            "But if math isn't your thing, just enjoy the game.",
        ])

        # Deck context (much shorter)
        deck_context = self._get_deck_context_message(deck_name, opponent_name)

        # Help reminder
        help_text = "'rando help' for commands."

        return f"{greeting} {intro} {optional} {deck_context} {help_text} gl hf!"

    def _get_player_score_context(self, opponent_name: str) -> str:
        """Get player's cumulative astrogation score context"""
        if not self.stats_repo:
            return ""

        player_stats = self.stats_repo.get_player_stats(opponent_name)
        if player_stats and player_stats.total_ast_score > 0:
            return f" Your astrogation score: {player_stats.total_ast_score}."
        return ""

    def _get_deck_context_message(self, deck_name: str, opponent_name: str) -> str:
        """
        Get context message about this deck's history. Shorter version.
        """
        if not self.stats_repo:
            origin = self._get_deck_origin()
            return f"Found this deck {origin}."

        deck_stats = self.stats_repo.get_deck_stats(deck_name)
        player_deck_stats = self.stats_repo.get_player_deck_stats(opponent_name, deck_name)

        has_deck_high_score = deck_stats and deck_stats.best_score > 0
        has_player_deck_score = player_deck_stats and player_deck_stats.best_score > 0

        if not has_deck_high_score:
            origin = self._get_deck_origin()
            return f"Found this deck {origin}."

        high_score = deck_stats.best_score
        high_player = deck_stats.best_player
        player_score = player_deck_stats.best_score if has_player_deck_score else 0

        player_is_holder = high_player == opponent_name

        if player_is_holder:
            if high_score > 50:
                return f"Your record: {high_score}. Nearly optimal."
            else:
                return f"Your record: {high_score}. Room for improvement."
        elif has_player_deck_score:
            diff = high_score - player_score
            return f"Your best: {player_score}. {high_player} has {high_score}. {diff} points ahead."
        else:
            return f"Record: {high_score} by {high_player}. Beat it."

    def _get_leader_context(self) -> str:
        """Get global leaderboard context"""
        if not self.stats_repo:
            return ""

        record = self.stats_repo.get_global_record('ast_score')
        if record and record.value > 0:
            return f"Top Astrogator: {record.player_name} ({record.value} pts)."
        return ""

    # =========================================================================
    # Turn Commentary
    # =========================================================================

    def get_turn_message(self, turn_number: int, board_state: 'BoardState') -> Optional[str]:
        """
        Generate turn commentary based on route score.

        Returns None for turn 1 or if conditions not met.
        """
        if turn_number < 2:
            return None

        my_lifeforce, their_lifeforce = self._get_lifeforce_breakdown(board_state)
        if my_lifeforce <= 0 or their_lifeforce <= 0:
            return None

        score = self.calculate_route_score(board_state)
        tier = self._get_score_tier(score)

        # Determine if improving or declining
        improving = False
        declining = False
        if self.last_route_score is not None:
            if score > self.last_route_score:
                improving = True
            elif score < self.last_route_score:
                declining = True

        self.last_route_score = score

        # Shorter prefix for later turns
        if turn_number == 2:
            prefix = f"Route score: {score} (your lifeforce - mine - turn#)"
        else:
            prefix = f"Route score: {score}"

        # Get tier-specific message
        suffix = self._get_score_tier_message(tier, improving, declining)

        return f"{prefix}. {suffix}"

    def _get_score_tier_message(self, tier: str, improving: bool, declining: bool) -> str:
        """Get the appropriate message for a score tier"""
        # Start with base pool
        base_pool = self.SCORE_MESSAGES.get(tier, self.SCORE_MESSAGES['even'])

        # Add momentum messages if applicable
        if improving:
            momentum_key = f'{tier}_improving'
            if momentum_key in self.SCORE_MESSAGES:
                base_pool = base_pool + self.SCORE_MESSAGES[momentum_key]
        elif declining:
            momentum_key = f'{tier}_declining'
            if momentum_key in self.SCORE_MESSAGES:
                base_pool = base_pool + self.SCORE_MESSAGES[momentum_key]

        return self._pick_message(base_pool)

    # =========================================================================
    # Battle Damage Messages
    # =========================================================================

    def get_damage_message(self, damage: int, is_new_global_record: bool = False,
                          is_new_personal_record: bool = False,
                          previous_holder: str = None,
                          previous_record: int = None,
                          current_player: str = None) -> Optional[str]:
        """
        Generate battle damage commentary.
        """
        if damage <= 0:
            return None

        # New global record
        if is_new_global_record:
            if previous_holder and previous_holder != current_player:
                return f"New damage record: {damage}! {previous_holder} dethroned!"
            else:
                return f"New damage record: {damage}! Impressive!"

        # New personal record
        if is_new_personal_record:
            if previous_record and previous_record > 0:
                return f"Personal best: {damage}! (was {previous_record})"
            else:
                return f"Personal best: {damage}!"

        # Regular damage commentary - add battle context
        if damage > 20:
            tier = 'high'
            prefix = f"Battle damage: {damage}!"
        elif damage > 10:
            tier = 'medium'
            prefix = f"Battle damage: {damage}."
        else:
            tier = 'low'
            prefix = f"Battle damage: {damage}..."

        message = self._pick_message(self.DAMAGE_MESSAGES[tier])
        return f"{prefix} {message}"

    # =========================================================================
    # Battle Start Messages
    # =========================================================================

    def get_battle_start_message(self, my_power: int, their_power: int,
                                  location_name: str = None) -> Optional[str]:
        """
        Generate battle start commentary for extreme situations only.

        Only comments on:
        - Crushing victories (power diff >= 8)
        - Getting crushed (power diff <= -8)
        - Close fights (power diff within 3) - but only 30% of the time

        Returns None for "normal" battles to avoid chat spam.
        """
        power_diff = their_power - my_power  # Positive = player advantage

        # Build context prefix
        context = "Battle starting:"

        # Player crushing us (+8 or more)
        if power_diff >= 8:
            message = self._pick_message(self.BATTLE_PLAYER_CRUSHING)
            return f"{context} {message}"

        # We're crushing player (-8 or worse for them)
        if power_diff <= -8:
            message = self._pick_message(self.BATTLE_BOT_CRUSHING)
            return f"{context} {message}"

        # Close battle (within 3 either way) - only comment sometimes
        if abs(power_diff) <= 3 and random.random() < 0.30:
            message = self._pick_message(self.BATTLE_CLOSE)
            return f"{context} {message}"

        # Normal battle - no comment to avoid spam
        return None

    # =========================================================================
    # Game End Messages
    # =========================================================================

    def get_game_end_message(self, won: bool, route_score: int,
                            deck_name: str = None,
                            is_new_deck_record: bool = False,
                            previous_holder: str = None,
                            previous_score: int = None,
                            new_total_score: int = None,
                            is_new_top_astrogator: bool = False) -> str:
        """
        Generate end-of-game message.
        """
        if not won:
            return self._pick_message(self.BOT_WON_MESSAGES)

        # Determine score tier
        if route_score > 50:
            tier = 'excellent'
        elif route_score > 30:
            tier = 'good'
        elif route_score > 10:
            tier = 'okay'
        else:
            tier = 'poor'

        # Build message
        if is_new_deck_record:
            template = self.END_GAME_NEW_RECORD[tier]
            message = template.format(score=route_score)
        else:
            template = self.END_GAME_NO_RECORD[tier]
            message = template.format(
                score=route_score,
                holder=previous_holder or "someone",
                high_score=previous_score or 0
            )

        # Add cumulative score
        if new_total_score is not None:
            message += f" Total: {new_total_score}."

        # Add top astrogator notification
        if is_new_top_astrogator:
            message += " You're the new top Astrogator!"

        return message

    # =========================================================================
    # Game Lifecycle Hooks
    # =========================================================================

    def on_game_start(self, opponent_name: str, deck_name: str, my_side: str):
        """Reset state for new game"""
        super().on_game_start(opponent_name, deck_name, my_side)
        self.last_route_score = None
        self.last_messages = []
        logger.info(f"Astrogator: New game vs {opponent_name} with deck {deck_name}")

    def on_game_end(self, won: bool, final_state: 'BoardState' = None):
        """Log game end"""
        super().on_game_end(won, final_state)
        if final_state:
            final_score = self.calculate_route_score(final_state)
            logger.info(f"Astrogator: Game ended - {'Player won' if won else 'Bot won'}, final score: {final_score}")
