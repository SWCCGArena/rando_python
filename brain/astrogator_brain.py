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
from .holiday_overlay import get_holiday_overlay, HolidayOverlay

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
        'in the outer rim',
        'from an Imperial spy on Eriadu',
        'from a very upset Wookiee',
        'while exploring some old Jedi ruins',
        'in a crashed X-wing on Dagobah',
        'etched into this creepy old Sith knife',
        'in the memory banks of some old R2 unit',
        'taped under a holochess table on a Corellian freighter',
        'while touring the debris field of Alderaan. Too soon?',
        "from this weird guy who won't take his helmet off",
        'from a scavenger on Jakku',
        'in the dumped garbage of a Star Destroyer',
        'from a bounty hunter who disintegrated the previous owner',
        'deep in the bowels of a tauntaun. I thought it smelled bad on the outside.',
        'in the bones of a krayt dragon',
        'from a tiny green baby who kept trying to eat it',
        'from this blue guy who said he had his own Star Destroyer',
        'on Mustafar. I have the high ground now.',
        'from a princess who hid it in a droid',
        'in a trash compactor. There was something alive down there.',
        'from a smuggler who made the Kessel Run in 12 parsecs. Allegedly.',
        'on Endor. The Ewoks wanted to cook me.',
        'from a moisture farmer with dreams of being a pilot',
        'in Cloud City. The deal kept getting altered.',
        'from a senator who turned out to be the Senate',
        'on the edge of the Unknown Regions, where my warranty does not apply',
        "inside a smuggler's false-bottom crate labeled 'totally not contraband'",
        'in the spice hold of a freighter, under a very optimistic tarp',
        'under a pile of confiscated sabacc decks',
        "in a Hutt's lost-and-found. Do not ask.",
        'behind a cantina booth, next to a blaster scorch mark',
        'inside a navicomputer that was definitely not stolen',
        "in a crate marked 'FRAGILE' that was treated as a suggestion",
        'from a Bothan who insisted I not ask how they got it',
    ]

    # Route score messages by tier
    SCORE_MESSAGES = {
        # Score >= 30 (profitable)
        'profitable': [
            'Sellable route. Minimal turbulence. Maximum smugness.',
            "Coordinates locked. Profit projected. Try not to ruin it.",
            "This is a good route. Please don't tell anyone I said that.",
            'Route score: impressive. I recalculated twice just to be sure.',
            "You're actually beating me. On purpose. Fascinating.",
            'This would fetch a high price on the hyperspace market. Hypothetically.',
            "If you keep this up, people will assume I'm helping you.",
            'Congratulations. You\'re competent. Statistically unusual.',
            "I could frame this route score. If I had walls.",
            "This is the kind of win smugglers brag about. Quietly. In case they're wrong.",
        ],
        # Score 20-29 (promising)
        'promising': [
            'Promising route. Not rich, but not embarrassed either.',
            "This could sell. With a little less screaming and a little more math.",
            "You're close to profitable. Don't celebrate early. That's how you lose.",
            'Good trajectory. Keep pulling the lever that makes me sad.',
            "We are approaching 'worth it.' Do not drift off course now.",
            "You're doing better than my pessimism predicted.",
            'This route has potential. Like a stormtrooper who can aim. Rare, but possible.',
            'One more clean turn and this might be a payday.',
        ],
        # Score 10-19 (weak potential)
        'weak': [
            'Weak route. The traders will laugh. Softly. But still.',
            "This score is technically a route. So is flying into a sun.",
            "Not great. Not terrible. Actually, it's mostly terrible.",
            'You are paying for this with time, and you\'re getting store credit.',
            "I've seen sturdier plans drawn in bantha feed.",
            'This route is inefficient. So are most organics.',
            "If this were a hyperspace jump, we'd arrive yesterday.",
            "On the bright side, we're not dead. Yet.",
        ],
        'weak_improving': [
            'Improving. The bar was low, and you stepped over it.',
            'Progress detected. I will now pretend this was expected.',
            'Better. Still a mess, but a faster mess.',
            'Your route score is rising. So is my suspicion.',
        ],
        'weak_declining': [
            "Route score dropping. Like a ship with its hyperdrive removed.",
            "That's... not the direction we wanted.",
            'We were doing so well. Comparatively.',
            'I have a bad feeling about this. Mostly for you.',
        ],
        # Score 0-9 (breaking even)
        'even': [
            'Breaking even. Congratulations on achieving... nothing.',
            'This is mediocrity with extra steps.',
            "You do understand we're trying to make money, right?",
            "Hello there, equilibrium. It's as exciting as it sounds.",
            'My enthusiasm is limited. By design.',
            'Your goal is to have MORE life force than me. More.',
            "This is depressing. For you. I'm a droid.",
            'The dark side clouds everything. Including your planning.',
        ],
        'even_improving': [
            "At least you're improving. Marginally.",
            'Better. Still not good, but better.',
            'Your score is rising. So is my hope. Slightly.',
            'If you keep improving, I may have to revise my insults.',
        ],
        'even_declining': [
            'Your score is supposed to go up, not down.',
            'I find your lack of progress disturbing.',
            'Route score dropping. Just like my expectations.',
            "If you keep this up, I'll start routing you through an asteroid field.",
        ],
        # Score -10 to -1 (slightly behind)
        'behind': [
            "Wait, I'm not supposed to be winning.",
            'This is the part where you turn it around. Any time now.',
            'I ran the numbers. They ran away.',
            "You're behind. I would offer encouragement, but I don't want to lie.",
            "You're drifting off course. Stop doing that.",
            "At this rate, the only thing you'll sell is an apology.",
            "I'm trying to lose. You're making it difficult.",
        ],
        'behind_improving': [
            "Better. Still behind, but at least you're awake now.",
            'Recovery vector detected. Keep your hands inside the ship at all times.',
            "You're climbing back. Try not to fall again. It's repetitive.",
        ],
        'behind_declining': [
            "You're slipping. That is not a strategy.",
            'This is how ships end up as salvage.',
            'Trend analysis: bad. Very bad.',
        ],
        # Score < -10 (heavily behind)
        'very_behind': [
            'You have approximately a 2.4% chance of turning this around.',
            'This is why droids should be in charge.',
            "Search your feelings. You know you're losing.",
            'Do or do not. There is no... whatever this is.',
            "I suggest a new strategy: stop hemorrhaging life force.",
            "I've got a bad feeling about this. For you.",
            "If panic had a route score, this would be it.",
            "We are far off course. I would say 'recalculating,' but you keep overriding me.",
            'The odds are not in your favor. Unless you plan to draw miracles.',
        ],
        'very_behind_improving': [
            'Better. Still terrible, but better.',
            "A new hope? Let's not get carried away.",
            'Improvement detected. Please continue being less doomed.',
        ],
        'very_behind_declining': [
            "You're going the wrong way at speed.",
            'That was a choice. Not a good one.',
            "If you need me, I'll be in the corner recalculating regret.",
            'This route is collapsing. Like morale. Like everything.',
        ],
    }

    # Battle damage messages by tier
    DAMAGE_MESSAGES = {
        # Damage > 20 (high)
        'high': [
            'Now THIS is podracing!',
            "That's no moon... that's YOUR damage total!",
            'Great shot, kid. That was one in a million!',
            'Witness the firepower of this fully armed deck!',
            "I'm not even mad. That was efficient.",
            'I felt a great disturbance in my cards.',
            'That hurt. In a purely mathematical sense.',
            'You just deleted a large portion of my future. Impressive.',
            'If that was your plan, it worked. Unfortunately.',
            'Note to self: do not stand in front of that again.',
        ],
        # Damage 10-20 (medium)
        'medium': [
            'Ow. Moderate, but emotionally rude.',
            "That damage was... adequate. Like your excuses will be later.",
            "Not fatal. Yet. I hate that word.",
            "I've seen worse. I've also seen better. Please do worse.",
            'You may fire when ready. Apparently you did.',
            'I dislike this route. It has teeth.',
            "That's a meaningful number of cards. I'm counting them. Bitterly.",
            "Acceptable hit. Keep that up and I'll have to start respecting you.",
            'Your battle destiny is behaving suspiciously competent.',
            'Attrition is a cruel hobby.',
        ],
        # Damage 1-9 (low)
        'low': [
            'That was adorable.',
            'Light scratch. My pride is more damaged than my life force.',
            "Is that all? I've had worse from a malfunctioning mouse droid.",
            'Minimal impact. Maximum confidence. Classic organic.',
            'Those blast points... too accurate for Sand People.',
            'Only Imperial Stormtroopers are so imprecise.',
            'Into the garbage chute, flyboy. Preferably your strategy.',
            'Boring conversation anyway.',
            'You may fire when ready. Or not. Apparently not.',
            "If you're aiming for my feelings, you're doing great. For the cards, not so much.",
            'I barely noticed. Which is insulting in its own way.',
        ],
    }

    # Game end messages (player won, by score tier) - lists for variety
    END_GAME_NEW_RECORD = {
        'excellent': [
            'New record! {score} points! That\'s a premium route. Try not to get arrested.',
            "{score}! New record. I will log this under 'events I did not predict.'",
            'Record broken: {score}. I would applaud, but I\'m busy recalculating my ego.',
            "{score} points. New high score. The traders will pretend they always believed in you.",
            'New record: {score}! I calculated you had a 12.3% chance of this. Impressive.',
            '{score}! New record. If you start monologuing, I\'m powering down.',
        ],
        'good': [
            'Score of {score}! New deck record. Not perfect, but sellable.',
            "{score}! New record. You're getting better. Don't let it go to your head.",
            'New mark: {score}. I am almost impressed. Almost.',
            '{score} sets the record. I will update the charts and my grudges.',
            'Record: {score}. The previous holder sends their congratulations. Probably.',
            '{score} is the new standard. Your competence is escalating.',
        ],
        'okay': [
            "{score} is the new record. It's like being the tallest Jawa.",
            '{score}! New record. Low bar, but you cleared it. Barely.',
            'New record: {score}. Not impressive, but it is yours now.',
            "{score} sets the record! The previous holder wasn't trying very hard, clearly.",
            "Record: {score}. I've seen better. I've also seen worse. This is... adequate.",
            '{score}! New record. Somewhere, a Jawa is impressed. Just one though.',
            '{score} takes the top spot. Enjoy it before someone competent arrives.',
        ],
        'poor': [
            "{score}. That's the best anyone's done? The bar is underground.",
            'New record: {score}. I want you to understand how upsetting that is.',
            '{score} is now the record. Please do not advertise this.',
            'Record achieved: {score}. The traders are... confused.',
            "{score}. New record. I'm logging this as a system warning.",
            'You set the record at {score}. It is technically a record. Congratulations?',
            '{score} takes the crown. The crown is made of scrap.',
        ],
    }

    END_GAME_NO_RECORD = {
        'excellent': [
            '{score}! Excellent, but {holder} still beat you with {high_score}.',
            "{score} points! Great score, but {holder}'s {high_score} remains untouched.",
            "Impressive {score}! But {holder} holds the record at {high_score}. Next time.",
            "{score}! So close to {holder}'s record of {high_score}. The Force was almost with you.",
            "A mighty {score}! Yet {holder}'s {high_score} stands firm. A worthy challenge awaits.",
            '{score}. You were brilliant. {holder} was just... more so, at {high_score}.',
        ],
        'good': [
            '{score}. Solid, but {holder} has {high_score}. So close, yet so far.',
            "{score} points. Good effort. {holder}'s {high_score} lives another day.",
            'Nice: {score} points. {holder} still leads with {high_score} though.',
            "{score}. Respectable, but {holder}'s {high_score} remains the target.",
            "You scored {score}. {holder} scored {high_score}. Math is cruel.",
            '{score}. Good route. Not the best route. {holder} owns that at {high_score}.',
        ],
        'okay': [
            '{score}. {holder} scored {high_score}. You have much to learn, young Padawan.',
            "{score} points. {holder}'s {high_score} is still safe. Very safe.",
            "You got {score}. {holder} got {high_score}. I'll let you do the math.",
            "{score}. Not bad, but {holder}'s {high_score} is the target.",
            "{score} points. The record of {high_score} by {holder} remains unchallenged.",
            "{score}. {holder} is still ahead at {high_score}. Try harder next jump.",
            "{score}. If this were a race, you'd be lapped by {holder}. Twice.",
        ],
        'poor': [
            '{score}? Really? {holder} got {high_score}. I weep for the future.',
            "{score}. {holder} has {high_score}. The gap is... significant.",
            'You scored {score}. {holder} scored {high_score}. No comment.',
            "{score} points. {holder}'s {high_score} is in no danger whatsoever.",
            '{score}. The record is {high_score} by {holder}. You have work to do.',
            "{score}? {holder}'s {high_score} seems very far away right now.",
            "{score}. If this were a hyperspace route, we'd be lost until the heat death of the universe.",
        ],
    }

    # Multiple bot won messages for variety
    BOT_WON_MESSAGES = [
        'I win. This was not the intended outcome.',
        'Victory for the droid. Please file your complaint with the nearest void.',
        'I won? I was trying to help you. Sort of.',
        'Even droids get lucky sometimes. This was skill though.',
        'The student has not yet surpassed the master. Tragic.',
        "Perhaps next time you'll listen to my odds calculations.",
        'I find your lack of victory disturbing.',
        'Congratulations, you lost to a bot running on pure sarcasm.',
        'You fought well. That is not praise. It\'s an observation.',
        "If it helps, I feel nothing. You should still feel something.",
        "I will now log this as: 'organic learning opportunity.'",
        'Please try again. I require more data. And amusement.',
        'I am beginning to understand why the Empire used droids.',
        'Do not worry. Statistically, everyone loses to randomness eventually.',
    ]

    # Battle start messages - only for extreme situations!
    # Player crushing bot (power advantage >= 8)
    BATTLE_PLAYER_CRUSHING = [
        'The odds are in your favor. I calculate 94.7% chance of victory.',
        "This should be quick. I'll try to make it entertaining.",
        'Impressive firepower. Most impressive.',
        'I appear to have made a tactical error. Several, actually.',
        'Well, this is unfortunate. For me.',
        'This is fine. Everything is fine.',
        "I've seen this before. It doesn't end well for me.",
        'Your overconfidence is... actually justified here.',
        'Your route is clean. Mine is smoking.',
        'At this rate, I should start filing my own surrender paperwork.',
        'You are dismantling my plan with alarming efficiency.',
        "If this were hyperspace, I'd be the debris field.",
    ]

    # Bot crushing player (power advantage >= 8)
    BATTLE_BOT_CRUSHING = [
        'The odds are NOT in your favor. Just so you know.',
        'I have you now.',
        'You may want to reconsider your life choices.',
        'This engagement is trending in my favor. Shocking, I know.',
        'Your route is unstable. Mine is annoyingly stable.',
        'You are one bad decision away from disaster. Please proceed.',
        'I am calculating a victory lap. Metaphorically. I do not have legs.',
        "This is going better than expected. For me. Terrible for you.",
        'Do not panic. Actually, do panic. It makes you predictable.',
        "If you need encouragement, ask an R2 unit. I am not trained for that.",
        "Your odds are improving if you can invent a time machine.",
        'This is where the Rebellion usually improvises something. Any moment now.',
    ]

    # Close/contested battles (power within 3) - less frequent, adds tension
    BATTLE_CLOSE = [
        'This should be interesting.',
        'The odds are... actually unclear here.',
        "May the Force be with you. You'll need it.",
        'A fair fight. How uncivilized.',
        "Let's see what you've got.",
        'I have a bad feeling about this.',
        'Route is volatile. One mistake and we hit a moon.',
        'This is the fun part. Statistically speaking.',
        'Close fight. I dislike uncertainty. Continue anyway.',
    ]

    # Concede messages - K2SO-style sassy defeat acceptance
    # General concede (life force too low, no options)
    CONCEDE_GENERAL = [
        'I have calculated our odds of survival. They are not favorable.',
        'The odds of winning are approximately... never mind. GG.',
        'I was going to calculate the odds, but why bother? GG.',
        "My continued resistance would be futile. I know when I'm beaten.",
        'Congratulations. I have failed to fail successfully.',
        'I find my lack of life force disturbing. GG.',
        'This mission is over. The Death Star always wins. GG.',
        'I need to recalibrate my hyperspace calculations. You win.',
        "Even droids know when to fold 'em. GG.",
        "I would say 'good fight' but I'm a droid, so... adequate fight. GG.",
        "Your victory was statistically inevitable. I just didn't want to admit it.",
        "I'm not saying you played well, but you played better than me. GG.",
        "I've run out of options. And sarcasm. Almost. GG.",
        'This route calculation has gone horribly wrong. Conceding. GG.',
        'The probability of my victory just hit 0%. Surrendering now.',
        'I must return to my ship and rethink my strategy. GG.',
        'I need a reboot. You need congratulations. One of those is happening. GG.',
        'Plotting escape vector. Escape vector failed. Conceding instead. GG.',
        'I would continue, but I prefer my defeats quick and documented. GG.',
        "I have reached the 'acceptance' stage of strategy. GG.",
        'You win. I will now blame variance to protect my self-esteem module.',
        'My navicomputer recommends surrender. For once, I agree.',
    ]

    # Fatal damage concede (taking more damage than life force)
    CONCEDE_FATAL_DAMAGE = [
        'Fatal damage detected. I refuse to pretend otherwise. GG.',
        'I have calculated that I will lose approximately 100% of my remaining life. Conceding.',
        'Even droids know when the damage is fatal. Well played. GG.',
        'This is no moon... this is your damage output. I yield. GG.',
        'My life force just evaporated. Efficient. Rude. GG.',
        'That hit sealed it. Conceding before the next one makes me sentimental.',
        'Shields down. Hull breached. Pride obliterated. GG.',
        'You have removed my remaining options with extreme prejudice. GG.',
        'I am now mostly used pile. Conceding. GG.',
        'I cannot outrun that damage. Conceding to conserve dignity. GG.',
        'That was lethal math. Respectfully, stop. GG.',
    ]

    # Loop-based concede (stuck in decision loop)
    CONCEDE_LOOP = [
        'I appear to be caught in an infinite loop. I blame the Rebellion.',
        'My circuits are confused. Rebooting... actually, just conceding. GG.',
        "I'm experiencing a logic malfunction. Better to concede than freeze.",
        'Even droids get dizzy. Whatever is happening, I need to stop. GG.',
        "I've been making the same decision for too long. Time to cut my losses.",
        'Error 404: Good strategy not found. Conceding to avoid a crash.',
        'Decision loop detected. Terminating gracefully. Like a professional. GG.',
        'If I choose option A again, the universe collapses. Conceding instead. GG.',
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
        self.holiday_overlay = get_holiday_overlay()  # Holiday message overlay

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
        """Get a random deck origin story, with holiday variants."""
        return self.holiday_overlay.get_deck_origin(self.DECK_ORIGINS)

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
        Uses holiday greetings if a holiday is active.
        """
        # Check for holiday greeting first
        holiday_greeting = self.holiday_overlay.get_holiday_greeting()
        if holiday_greeting:
            # Use holiday greeting with opponent name
            greeting = f"{opponent_name}! {holiday_greeting}"
        # Personalized side-based greeting
        elif opponent_side and opponent_side.lower() == 'light':
            greeting = random.choice([
                f"Ah, {opponent_name}. Rebel scum. I see.",
                f"{opponent_name}. A rebel. How original.",
                f"Greetings, {opponent_name}. Insurgent detected.",
                f"Hello, {opponent_name}. Rebellion status: optimistic. We'll see.",
                f"Rebel identified: {opponent_name}. Please keep your hands away from the self-destruct lever.",
                f"Welcome, {opponent_name}. I've heard of you. Mostly from wanted posters.",
            ])
        else:
            greeting = random.choice([
                f"{opponent_name}. An Imperial. Charming.",
                f"Hello there, {opponent_name}. Imperial entanglement incoming.",
                f"Ah, {opponent_name}. Another Imperial.",
                f"Greetings, {opponent_name}. Imperial paperwork approved.",
                f"{opponent_name}. Empire-aligned. That explains the confidence.",
                f"Ah, {opponent_name}. Long live bureaucracy.",
            ])

        # Intro explaining the meta-game
        intro = (
            "I'm rando_cal, astrogation droid. I log a 'route score' based on how hard you beat me: "
            "(your life force - my life force) minus turns played. Score 30+ is worth selling."
        )

        # Make it clear this is optional
        optional = random.choice([
            "Or just play SWCCG and ignore me.",
            "Of course, you can just play SWCCG. I'll be here either way.",
            "If math isn't your thing, just enjoy the game.",
            "If you don't care about scores, that's fine. I still do. Sadly.",
            "Ignore the route score if you want. I'm contractually obligated to keep calculating it.",
            "Play your game. I'll stare at the numbers and judge quietly.",
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
        """Get the appropriate message for a score tier, with holiday variants."""
        # Check if we should use a holiday message
        if self.holiday_overlay.should_use_holiday_message():
            holiday_msg = self.holiday_overlay.get_score_message(tier, {})
            if holiday_msg:
                return holiday_msg

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

        # Try holiday message first
        if self.holiday_overlay.should_use_holiday_message():
            holiday_msg = self.holiday_overlay.get_damage_message(tier, {})
            if holiday_msg:
                return f"{prefix} {holiday_msg}"

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
        Uses holiday variants when a holiday is active.
        """
        power_diff = their_power - my_power  # Positive = player advantage

        # Build context prefix
        context = "Battle starting:"

        # Player crushing us (+8 or more)
        if power_diff >= 8:
            if self.holiday_overlay.should_use_holiday_message():
                message = self.holiday_overlay.get_battle_message(
                    'player_crushing', self.BATTLE_PLAYER_CRUSHING)
            else:
                message = self._pick_message(self.BATTLE_PLAYER_CRUSHING)
            return f"{context} {message}"

        # We're crushing player (-8 or worse for them)
        if power_diff <= -8:
            if self.holiday_overlay.should_use_holiday_message():
                message = self.holiday_overlay.get_battle_message(
                    'bot_crushing', self.BATTLE_BOT_CRUSHING)
            else:
                message = self._pick_message(self.BATTLE_BOT_CRUSHING)
            return f"{context} {message}"

        # Close battle (within 3 either way) - only comment sometimes
        if abs(power_diff) <= 3 and random.random() < 0.30:
            if self.holiday_overlay.should_use_holiday_message():
                message = self.holiday_overlay.get_battle_message(
                    'close', self.BATTLE_CLOSE)
            else:
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
        Generate end-of-game message with holiday variants.
        """
        # Check for holiday message
        if self.holiday_overlay.should_use_holiday_message():
            holiday_msg = self.holiday_overlay.get_game_end_message(won, [])
            if holiday_msg:
                # For holiday wins, still add the score context
                if won:
                    holiday_msg = f"Score: {route_score}. {holiday_msg}"
                return holiday_msg

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

        # Build message - pick randomly from list of templates
        if is_new_deck_record:
            templates = self.END_GAME_NEW_RECORD[tier]
            template = self._pick_message(templates)
            message = template.format(score=route_score)
        else:
            templates = self.END_GAME_NO_RECORD[tier]
            template = self._pick_message(templates)
            message = template.format(
                score=route_score,
                holder=previous_holder or "someone",
                high_score=previous_score or 0
            )

        # Add prefix to clarify this is the score for this game
        message = f"Route score: {message}"

        # Add cumulative lifetime score
        if new_total_score is not None:
            message += f" Lifetime: {new_total_score}."

        # Add top astrogator notification
        if is_new_top_astrogator:
            message += " You're the new top Astrogator!"

        return message

    def get_concede_message(self, reason: str = "") -> str:
        """
        Get a K2SO-style concede message.

        Args:
            reason: The reason for conceding (from should_concede()).
                   Used to pick appropriate message pool.

        Returns:
            A sassy concede message string.
        """
        # Check for holiday concede message
        if self.holiday_overlay.should_use_holiday_message():
            holiday_msg = self.holiday_overlay.get_concede_message(self.CONCEDE_GENERAL)
            if holiday_msg and holiday_msg not in self.CONCEDE_GENERAL:
                return holiday_msg

        reason_lower = reason.lower() if reason else ""

        # Choose message pool based on reason
        if "fatal" in reason_lower or "damage" in reason_lower or "unsurvivable" in reason_lower:
            return self._pick_message(self.CONCEDE_FATAL_DAMAGE)
        elif "loop" in reason_lower:
            return self._pick_message(self.CONCEDE_LOOP)
        else:
            return self._pick_message(self.CONCEDE_GENERAL)

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
            logger.info(f"Astrogator: Game ended - {'Bot won' if won else 'Bot lost'}, final score: {final_score}")
