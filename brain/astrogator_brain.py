"""
Astrogator Brain - Personality Mode

A mercenary astrogation droid that treats each game as calculating hyperspace routes.
Players earn "route scores" that can be "sold to traders" - higher scores = better routes.

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

    # 15 random deck origin stories
    DECK_ORIGINS = [
        "in the outer rim",
        "from an imperial spy on Eriadu",
        "from an angry wookie",
        "while exploring some old ruins",
        "in a crashed x-wing",
        "etched into this creepy old knife",
        "in the memory banks of some old r2 unit",
        "while touring the debris field of Alderaan",
        "from this weird guy who won't take his helmet off",
        "from a trader on Jakku",
        "in the dumped garbage of a star destroyer",
        "from a bounty hunter on Tatooine",
        "deep in the bowels of a tauntaun",
        "in the bones of a kryat dragon",
        "from the hands of a tiny floating green baby",
        "from this blue guy who said he had his own star destroyer",
    ]

    # Route score messages by tier
    SCORE_MESSAGES = {
        # Score >= 30 (profitable)
        'profitable': [
            "That's not too bad, keep it up and we might make some money today!",
            "Yes! I can sell this route now, just don't start losing again.",
            "Perfect! I can sell this to anybody.",
            "If we can just hold this course we're going to be rich!",
            "You've been learning, now we can make some real money.",
            "I knew you could do it, or at least that's what I'm telling you.",
        ],
        # Score 20-29 (promising)
        'promising': [
            "That's ok, but we'll have to do better if we want to sell this route.",
            "This is good, but nobody will buy a route scored less than 30.",
            "The higher the score, the better the hyperspace route in the end.",
            "Last time I followed a route this good I found a chunk of Beskar.",
            "You show promise human, maybe we can sell this afterall.",
        ],
        # Score 10-19 (weak potential)
        'weak': [
            "Well it isn't terrible, but I can't sell such a suboptimal route. Hopefully you can turn things around.",
            "A route score like this shows promise, maybe we'll find some bantha poodoo at the end of it.",
            "This is a great start, still not worth anything, but a great start.",
            "Clearly you know what you are doing, keep it up.",
            "Yes, yes, let the hate flow through you.",
            "This is a good score, it could be better.",
        ],
        'weak_improving': [
            "You are really turning this around, keep it up!",
            "A good improvement from last turn!",
        ],
        'weak_declining': [
            "Hmm, I thought we were going to be rich, I guess not.",
            "This is getting worse, it's not supposed to get worse.",
            "No, no, you want to do better each turn, not worse.",
        ],
        # Score 0-9 (breaking even)
        'even': [
            "Hmm, remember we're trying to optimize this route, are you up to the task?",
            "You do understand we're trying to make some money here right? We need a higher route score.",
            "I could probably do better playing against myself.",
            "This is depressing.",
            "Remember your goal is to have more lifeforce than I do.",
            "I can't sell this route unless the score is greater than 30.",
        ],
        'even_improving': [
            "At least you are improving.",
            "This is better than last turn, keep it up and maybe this won't be a waste of time.",
            "Your score is getting higher, so is my hope for making some money today.",
        ],
        'even_declining': [
            "Wait this can't be right... you are doing worse than last turn?",
            "Your score is supposed to be getting higher you know.",
            "Route score is dropping, just like my hopes of making any money from this deck.",
        ],
        # Score -10 to -1 (slightly behind)
        'behind': [
            "Wait I'm not supposed to be winning?!",
            "I'm literally just playing random cards at this point...",
            "Nobody ever says 'let the robots win', that's because you are supposed to win.",
            "I'm trying to be nice, but really you should be doing better than this.",
            "You realize I'm just a badly programmed bot right? Why are you losing?",
            "I hope you have some tricks up your sleeve, this isn't looking good.",
        ],
        'behind_improving': [
            "At least the score is moving in the right direction.",
        ],
        'behind_declining': [
            "And you were doing so well earlier, what went wrong?",
        ],
        # Score < -10 (heavily behind)
        'very_behind': [
            "You do realize you are supposed to be winning right?",
            "Well you are behind, but maybe you have a plan?",
            "You have a 97.6% chance of failure.",
            "This is why we never let the humans hold the blasters.",
            "I thought you were good at this?",
            "I'll set you up for an easy win next turn.",
            "I was trying to make this easy for you.",
        ],
        'very_behind_improving': [
            "This is better than last turn, still bad.",
        ],
        'very_behind_declining': [
            "Route score was already bad, now it's worse.",
        ],
    }

    # Battle damage messages by tier
    DAMAGE_MESSAGES = {
        # Damage > 20 (high)
        'high': [
            "This should really help our route score.",
            "I'm not even mad, that's impressive.",
            "HaHa! Yes! Now we're making some money!",
            "This route might lead us to a sith artifact.",
            "The more damage you do, the more money we, I mean I will make.",
            "That's a lot of explosions.",
            "Whoosh, Zap, BOoM! I just love making the noises.",
            "I may be a droid, but even I felt that.",
        ],
        # Damage 10-20 (medium)
        'medium': [
            "Now we're really going to optimize this route.",
            "I'll take this, more would be better.",
            "I was just testing you.",
            "I thought they smelled bad on the outside.",
            "They died for a good cause I'm sure.",
            "This should help us find a good shortcut.",
            "I hope all this suffering is worth it.",
            "Some of those guys were just contractors.",
        ],
        # Damage 1-9 (low)
        'low': [
            "I guess it's better than nothing.",
            "No, that's really not much better than nothing.",
            "Similar accuracy to the empire's finest I see.",
            "The Ewoks had more kills than you.",
            "I'm a droid, what's your excuse?",
            "That isn't very good.",
            "You know hearthstone needs some players, you could go try that?",
            "Next time try pointing the blasters at my troops.",
            "I tried to set you up, what went wrong?",
            "You can always revert and try again, I won't tell anyone.",
        ],
    }

    # Game end messages (player won, by score tier)
    END_GAME_NEW_RECORD = {
        'excellent': "Amazing! You scored {score}, this is a nearly perfect route! We're both going to be very rich once I sell this.",
        'good': "Not bad, you scored {score}, it's the best route I've seen with this deck so far, but far from perfect.",
        'okay': "Your score of {score} is the best route I've seen, still I doubt it's even worth selling. Hopefully somebody beats it soon, no offense.",
        'poor': "A score of {score}... that's the best we can do? Such a waste of good cards.",
    }

    END_GAME_NO_RECORD = {
        'excellent': "Amazing! You scored {score}, still {holder} has you beat at {high_score}. I'll record this, but I can't sell it for anything.",
        'good': "Not bad, you scored {score}. {holder} scored {high_score} though, so why should I bother following your route, try harder next time.",
        'okay': "Your score of {score} is pitiful. {holder} has already scored {high_score}. It seems you have a lot to learn.",
        'poor': "{score}! You really only got {score}? I feel like I'd be better served playing against myself, thanks for trying... I guess.",
    }

    # Bot won message
    BOT_WON_MESSAGE = "It's ok, even droids get lucky... from time to time. You'll do better next game!"

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
        available = [m for m in pool if m not in self.last_messages[-3:]]
        if not available:
            available = pool

        message = random.choice(available)
        self.last_messages.append(message)

        # Keep last 10 messages
        if len(self.last_messages) > 10:
            self.last_messages = self.last_messages[-10:]

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

        Args:
            opponent_name: Name of the opponent
            deck_name: Name of the deck being used
            opponent_side: 'dark' or 'light'
        """
        # Greeting based on side
        if opponent_side == 'light':
            greeting = "Greetings rebel scum, "
        else:
            greeting = "Greetings imperial nuisance, "

        # Main intro
        intro = (
            f"I am rando_cal, famous Astrogator. To you these are just cards, "
            f"but to me each deck is a map to treasures in the Unknown Regions! "
            f"Help me find optimal routes by beating me with the highest lifeforce "
            f"difference in the fewest turns. Lifeforce is your used + force + reserve "
            f"pile count. For example if you can beat me on turn 5 with 15 more lifeforce "
            f"than me, that's a route score of 10. I need a route score of at least 30 "
            f"to make any real money, the higher the better!"
        )

        # Deck context (includes player's score on this deck if returning)
        deck_context = self._get_deck_context_message(deck_name, opponent_name)

        # Player's cumulative astrogation score (if returning player)
        player_score_context = self._get_player_score_context(opponent_name)

        # Global leaderboard context
        leader_context = self._get_leader_context()

        # Help reminder
        help_text = "Type 'rando help' for commands."

        return f"{greeting}{intro} {deck_context}{player_score_context} {leader_context} {help_text} gl, hf {opponent_name}!"

    def _get_player_score_context(self, opponent_name: str) -> str:
        """Get player's cumulative astrogation score context"""
        if not self.stats_repo:
            return ""

        player_stats = self.stats_repo.get_player_stats(opponent_name)
        if player_stats and player_stats.total_ast_score > 0:
            return f" Your current astrogation score is: {player_stats.total_ast_score} (cumulative over all games played)."
        return ""

    def _get_deck_context_message(self, deck_name: str, opponent_name: str) -> str:
        """
        Get context message about this deck's history.

        Matches C# SendWelcomeMessage logic:
        - Shows deck's global high score (any player)
        - Shows player's previous score on THIS specific deck
        - Different messages based on whether player holds record
        """
        if not self.stats_repo:
            origin = self._get_deck_origin()
            return f"I just picked this deck up {origin}, help me find the secrets it holds."

        deck_stats = self.stats_repo.get_deck_stats(deck_name)
        player_deck_stats = self.stats_repo.get_player_deck_stats(opponent_name, deck_name)

        # Check if deck has any history
        has_deck_high_score = deck_stats and deck_stats.best_score > 0
        has_player_deck_score = player_deck_stats and player_deck_stats.best_score > 0

        if not has_deck_high_score:
            # No history for this deck at all
            origin = self._get_deck_origin()
            return f"I just picked this deck up {origin}, help me find the secrets it holds."

        high_score = deck_stats.best_score
        high_player = deck_stats.best_player
        player_score = player_deck_stats.best_score if has_player_deck_score else 0

        # Check if this player holds the deck record
        player_is_holder = high_player == opponent_name

        if player_is_holder:
            # Player holds the record on this deck
            if high_score > 50:
                return f"This deck is almost fully optimized, you've already found a route score of {high_score}. Not quite perfect yet, help me find the final shortcuts."
            elif high_score > 30:
                return f"This deck's route score of {high_score} feels suboptimal, we must improve it."
            else:
                return f"I appreciate your help with this, but the current route score of {high_score} is not... great. I'm certain you can beat it this time."
        elif has_player_deck_score:
            # Player has a score on this deck but doesn't hold the record
            diff = high_score - player_score
            if diff > 30:
                return f"Hmm are you sure you can do this? Your score of {player_score} is over 30 points behind the route found by {high_player}. They scored {high_score}, but I guess you can try if you want."
            elif diff < 10:
                return f"Your score of {player_score} is only {diff} points behind the leader. Let's focus and try to beat {high_player}'s score of {high_score}."
            else:
                return f"Your score of {player_score} is {diff} points behind the leader. {high_player} has a score of {high_score}."
        else:
            # Player has never played this deck, but others have
            if high_score > 50:
                return f"This deck is almost fully optimized, {high_player} found a route score of {high_score}. Not quite perfect yet, help me find the final shortcuts."
            elif high_score > 30:
                return f"This deck's route score of {high_score}, held by {high_player} feels suboptimal, we must improve it."
            else:
                return f"I appreciate {high_player}'s help with this, but the current route score of {high_score} is not... great. I'm certain even you can beat it."

    def _get_leader_context(self) -> str:
        """Get global leaderboard context"""
        if not self.stats_repo:
            return ""

        record = self.stats_repo.get_global_record('ast_score')
        if record and record.value > 0:
            return f"The current top Astrogator across all decks is: {record.player_name} with {record.value} points."
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

        # Build message
        if turn_number <= 3:
            prefix = f"The route score is your lifeforce ({their_lifeforce}) - the turn number ({turn_number}) subtracted from my lifeforce ({my_lifeforce}), oh I'll just do the math for you it's: {score}"
        else:
            prefix = f"Current route score is {score}"

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
                          previous_record: int = None) -> Optional[str]:
        """
        Generate battle damage commentary.

        Args:
            damage: Amount of damage dealt
            is_new_global_record: Whether this sets a new global record
            is_new_personal_record: Whether this sets a new personal record
            previous_holder: Previous record holder name
            previous_record: Previous record value
        """
        if damage <= 0:
            return None

        # New global record
        if is_new_global_record:
            if previous_holder:
                return f"Nice damage score! You just set the high score, beating: {previous_holder}. Your score was: {damage}"
            else:
                return f"Wow! You set a new damage high score: {damage}"

        # New personal record
        if is_new_personal_record:
            if previous_record and previous_record > 0:
                return f"That should help! You set a new personal damage score: {damage}, beating your old score of: {previous_record}"
            else:
                return f"That should help! You set a new personal damage score: {damage}!"

        # Regular damage commentary
        if damage > 20:
            tier = 'high'
            prefix = f"Oh {damage} damage!"
        elif damage > 10:
            tier = 'medium'
            prefix = f"{damage} damage!"
        else:
            tier = 'low'
            prefix = f"{damage} damage..."

        message = self._pick_message(self.DAMAGE_MESSAGES[tier])
        return f"{prefix} {message}"

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

        Args:
            won: Whether the player won (True = player won, False = bot won)
            route_score: Final route score
            deck_name: Name of the deck
            is_new_deck_record: Whether this is a new record for this deck
            previous_holder: Previous record holder
            previous_score: Previous record score
            new_total_score: New cumulative astrogation score
            is_new_top_astrogator: Whether player is new global leader
        """
        if not won:
            return self.BOT_WON_MESSAGE

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
            message += f" Your new astrogation score is: {new_total_score}"

        # Add top astrogator notification
        if is_new_top_astrogator:
            message += " You are the new top Astrogator, thanks for making me rich!"

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
