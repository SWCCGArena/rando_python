"""
Achievements System

Tracks 139 achievements:
- Single card appearances (53)
- Ships/locations (18)
- Card combinations (42)
- Combat/damage (5)
- Location-specific combos (2)
- Route score achievements (4)
- Gameplay achievements (5)
- Meta achievements (5)
- Seasonal/random (2)

Achievement triggers:
- card_in_play: Single card appears on board
- cards_together: Multiple cards at same location
- cards_at_site: Cards at specific site type (e.g., Hoth)
- my_card: Bot's card appears
- their_card: Opponent's card appears
- card_killed: Card was on board then removed
- damage: Damage threshold met
- route_score: Route score threshold
- games_played: Number of games
- achievements: Number of achievements unlocked
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from engine.board_state import BoardState
    from persistence.stats_repository import StatsRepository

logger = logging.getLogger(__name__)


@dataclass
class AchievementDef:
    """Definition of an achievement"""
    key: str
    quote: str
    trigger: str  # 'card_in_play', 'cards_together', etc.
    card_match: str = None  # For single card triggers
    cards: List[str] = None  # For multi-card triggers
    card_type: str = None  # Filter by type (Character, Starship, etc.)
    site_filter: str = None  # For location-specific achievements
    owner: str = None  # 'my', 'their', or None for any
    threshold: int = None  # For numeric triggers


# =============================================================================
# Achievement Definitions - 72 Total
# =============================================================================

ACHIEVEMENTS: Dict[str, AchievementDef] = {}

# -----------------------------------------------------------------------------
# Single Card Appearances (53)
# -----------------------------------------------------------------------------

_single_cards = [
    ("achievement_bossk", "Thinking takes too long. Action gets things done.", "bossk", "Character"),
    ("achievement_fortuna", "I take you to Jabba now.", "fortuna", "Character"),
    ("achievement_shmi", "You can't stop the change, any more than you can stop the suns from setting.", "shmi", "Character"),
    ("achievement_returned", "I Have Died Before.", "emperor returned", "Character"),
    ("achievement_jyn", "If we can make it to the ground, we'll take the next chance. And the next. On and on until we win... or the chances are spent.", "jyn", "Character"),
    ("achievement_poe", "Permission to hop in an X-Wing and blow something up?", "poe", "Character"),
    ("achievement_chirrut", "I'm one with the Force, and the Force is with me.", "chirrut", "Character"),
    ("achievement_jerjerrod", "I assure you, Lord Vader. My men are working as fast as they can.", "jerjerrod", "Character"),
    ("achievement_kylo", "Forgive me. I feel it again... the call from light.", "kylo ren", "Character"),
    ("achievement_saber", "An elegant weapon, from a more civilized age.", "lightsaber", "Weapon"),
    ("achievement_dooku", "Twice the pride, double the fall.", "dooku", None),
    ("achievement_senate", "I am the Senate!", "senator palpatine", None),
    ("achievement_sidious", "Power! Unlimited Power!", "sidious", None),
    ("achievement_kenobi", "Hello there.", "general kenobi", None),
    ("achievement_rey", "The garbage'll do!", "rey", "Character"),
    ("achievement_ackbar", "It's a trap!", "ackbar", None),
    ("achievement_ahsoka", "I am no Jedi.", "ahsoka", "Character"),
    ("achievement_jango", "I'm just a simple man trying to make my way in the universe.", "jango", "Character"),
    ("achievement_watto", "Mind tricks don't work on me.", "watto", "Character"),
    ("achievement_quigon", "There's always a bigger fish.", "qui-gon", "Character"),
    ("achievement_gunray", "In time, the suffering of your people will persuade you to see our point of view.", "gunray", "Character"),
    ("achievement_kamino", "It Ought To Be Here... But It Isn't...", "kamino", "Location"),
    ("achievement_yoda", "Do. Or do not. There is no try.", "yoda", "Character"),
    ("achievement_lando", "Everything you've heard about me is true.", "lando", "Character"),
    ("achievement_gonk", "Gonk, Gonk.", "eegee", "Character"),
    ("achievement_k2so", "Jyn, I'll be there for you. Cassian said I had to.", "2so", None),
    # NEW: Expanded Universe & Bounty Hunters
    ("achievement_thrawn", "To defeat an enemy, you must know them. Not simply their battle tactics, but their history, philosophy, art.", "thrawn", "Character"),
    ("achievement_marajane", "The last command was, 'You will kill Luke Skywalker.'", "mara jade", "Character"),
    ("achievement_dash", "The name's Dash. Dash Rendar. Freelance.", "dash rendar", "Character"),
    ("achievement_xizor", "Vader will pay for the death of my family.", "xizor", "Character"),
    ("achievement_ig88", "Bounty hunting is a complicated profession.", "ig-88", "Character"),
    ("achievement_dengar", "I've been waiting for this a long time, Solo.", "dengar", "Character"),
    ("achievement_zuckuss", "The mists have shown me the way.", "zuckuss", "Character"),
    ("achievement_4lom", "Protocol dictates I must inform you: you are worth more dead.", "4-lom", "Character"),
    ("achievement_aurra", "I don't work for the Republic.", "aurra sing", "Character"),
    ("achievement_cadbane", "I make a living. And I'm the best there is.", "cad bane", "Character"),
    ("achievement_greedo", "Oota goota, Solo?", "greedo", "Character"),
    # NEW: Prequel Era
    ("achievement_grievous", "Your lightsabers will make a fine addition to my collection.", "grievous", "Character"),
    ("achievement_mace", "This party's over.", "mace windu", "Character"),
    ("achievement_ventress", "I am fear. I am the queen of a blood-soaked planet.", "ventress", "Character"),
    ("achievement_rex", "In my book, experience outranks everything.", "captain rex", "Character"),
    ("achievement_cody", "Blast him!", "commander cody", "Character"),
    # NEW: Imperial Officers
    ("achievement_piett", "Intensify forward firepower!", "piett", "Character"),
    ("achievement_veers", "Maximum firepower!", "veers", "Character"),
    # NEW: Sequel Era
    ("achievement_phasma", "You were always scum.", "phasma", "Character"),
    ("achievement_hux", "Today is the end of the Republic!", "general hux", "Character"),
    # NEW: Mandalorian Era
    ("achievement_mando", "This is the way.", "din djarin", "Character"),
    ("achievement_grogu", "That's not a toy!", "grogu", "Character"),
    ("achievement_gideon", "You have something I want.", "moff gideon", "Character"),
    # NEW: Rebels Era
    ("achievement_hera", "If all you do is fight for your own life, then your life is worth nothing.", "hera syndulla", "Character"),
    ("achievement_ezra", "I'm Ezra Bridger, and this is my home.", "ezra bridger", "Character"),
    ("achievement_kanan", "I lost my way for a long time, but now I have a chance to change that.", "kanan", "Character"),
    # NEW: Miscellaneous Fan Favorites
    ("achievement_hondo", "This effort is no longer profitable!", "hondo", "Character"),
    ("achievement_aphra", "I'm not the good guy. Get it through your head.", "aphra", "Character"),
    ("achievement_saw", "Save the Rebellion! Save the dream!", "saw gerrera", "Character"),
    ("achievement_krennic", "We were on the verge of greatness. We were this close.", "krennic", "Character"),
]

for key, quote, card_match, card_type in _single_cards:
    ACHIEVEMENTS[key] = AchievementDef(
        key=key,
        quote=quote,
        trigger='card_in_play',
        card_match=card_match.lower(),
        card_type=card_type
    )

# -----------------------------------------------------------------------------
# Ships/Locations (18)
# -----------------------------------------------------------------------------

_ships_locations = [
    ("achievement_tydirium", "I was about to clear them.", "tydirium"),
    ("achievement_deathstar", "That's no moon.", "death star"),
    ("achievement_lando_falcon", "I'll take good care of her. She won't get a scratch.", "lando in millennium falcon"),
    ("achievement_onewithforce", "I'm one with the Force. The Force is with me.", "chirrut"),
    # NEW: Iconic Starships
    ("achievement_executor", "The Emperor is not as forgiving as I am.", "executor"),
    ("achievement_slavei", "Put Captain Solo in the cargo hold.", "slave i"),
    ("achievement_ghost", "Spectre-1, standing by.", "ghost"),
    ("achievement_tantive", "There'll be no escape for the Princess this time.", "tantive"),
    ("achievement_outrider", "She may not look like much, but she's got it where it counts.", "outrider"),
    ("achievement_chimaera", "Thrawn's flagship looms overhead.", "chimaera"),
    ("achievement_homeone", "May the Force be with us.", "home one"),
    ("achievement_profundity", "Rogue One, may the Force be with you.", "profundity"),
    ("achievement_devastator", "There she is! Set for stun.", "devastator"),
    ("achievement_wildkarrde", "Information is the galaxy's most valuable commodity.", "wild karrde"),
    ("achievement_houndstooth", "Scorekeeper will be pleased.", "hound's tooth"),
    ("achievement_scimitar", "At last we will reveal ourselves to the Jedi.", "scimitar"),
    ("achievement_supremacy", "That's Snoke's ship. You think you got him?", "supremacy"),
    ("achievement_starkiller", "It's another Death Star.", "starkiller base"),
]

for key, quote, card_match in _ships_locations:
    ACHIEVEMENTS[key] = AchievementDef(
        key=key,
        quote=quote,
        trigger='card_in_play',
        card_match=card_match.lower()
    )

# My/Their card specific
ACHIEVEMENTS["achievement_falcon"] = AchievementDef(
    key="achievement_falcon",
    quote="It's the ship that made the Kessel run in less than 12 parsecs.",
    trigger='my_card',
    card_match="falcon"
)
ACHIEVEMENTS["achievement_boba"] = AchievementDef(
    key="achievement_boba",
    quote="boba fett? boba fett?! where??",
    trigger='their_card',
    card_match="boba"
)
ACHIEVEMENTS["achievement_womprat"] = AchievementDef(
    key="achievement_womprat",
    quote="I used to bullseye womp rats in my T-16 back home, they're not much bigger than two meters.",
    trigger='their_card',
    card_match="womp rat"
)

# -----------------------------------------------------------------------------
# Card Combinations (42)
# -----------------------------------------------------------------------------

_card_combos = [
    ("achievement_anakin_kenobi", "It's Over, Anakin. I Have The High Ground.", ["anakin", "kenobi"]),
    ("achievement_emperor_luke", "Now, young Skywalker, you will die.", ["emperor", "skywalker"]),
    ("achievement_leia_chew", "Would somebody get this big walking carpet out of my way?", ["leia", "chew"]),
    ("achievement_kylo_luke", "I want every gun that we have to fire on that man.", ["kylo ren", "luke skywalker"]),
    ("achievement_jabba_luke", "Your mind powers won't work on me boy.", ["jabba", "luke"]),
    ("achievement_anakin_padme", "Ani, you'll always be that little boy I knew on Tatooine.", ["anakin", "luke"]),
    ("achievement_tie_red5", "The force is strong with this one.", ["custom tie", "red 5"]),
    ("achievement_leia_tarkin", "I recognized your foul stench when I was brought onboard.", ["tarkin", "leia"]),
    ("achievement_luke_owen", "But I was going to Tosche Station to pick up some power converters!", ["luke", "owen"]),
    ("achievement_vader_motti", "I find your lack of faith disturbing.", ["vader", "motti"]),
    ("achievement_3po_crawler", "What's that, a transport? I'm saved!", ["c-3", "crawler"]),
    ("achievement_3po_owen", "Shut up, I'll take this one.", ["c-3", "owen"]),
    ("achievement_3po_r2", "Oh, my dear friend. How I've missed you.", ["c-3", "r2-d2"]),
    ("achievement_luke_obi", "The Force will be with you. Always.", ["obi", "luke"]),
    ("achievement_leia_obi", "Help me Obi-Wan, you're our only hope.", ["leia", "obi"]),
    ("achievement_leia_luke", "Aren't you a little short for a stormtrooper?", ["leia", "luke"]),
    ("achievement_vader_obi", "If you strike me down, I shall become more powerful than you can possibly imagine.", ["vader", "obi"]),
    ("achievement_werehome", "Chewie, we're home.", ["chew", "han", "falcon"]),
    # NEW: Multi-card combos
    ("achievement_fettlegacy", "I'm just a simple man, like my father before me.", ["jango", "boba"]),
    ("achievement_501st", "We're just clones, sir. We're meant to be expendable.", ["rex", "cody"]),
    ("achievement_order66", "Execute Order 66.", ["cody", "kenobi"]),
    ("achievement_spectre", "Spectre team, standing by.", ["ezra", "hera"]),
    ("achievement_mandalore", "Wherever I go, he goes.", ["din", "grogu"]),
    ("achievement_maul_kenobi", "I have been waiting for you.", ["maul", "kenobi"]),
    ("achievement_dooku_anakin", "I've been looking forward to this.", ["dooku", "anakin"]),
    ("achievement_grievous_kenobi", "General Kenobi! You are a bold one.", ["grievous", "kenobi"]),
    ("achievement_shadows", "Black Sun rises.", ["xizor", "guri"]),
    ("achievement_bounty_hunters", "We don't need that scum.", ["bossk", "dengar"]),
    ("achievement_protocol_bounty", "An odd couple, but effective.", ["4-lom", "zuckuss"]),
    ("achievement_inquisitors", "There are some things far more frightening than death.", ["inquisitor", "fifth brother"]),
    ("achievement_twin_suns", "Look sir, droids!", ["r2", "c-3"]),
    # NEW: Vehicle/Ship Combos
    ("achievement_stompy", "Imperial walkers on the north ridge!", ["blizzard 1", "blizzard 2"]),
    ("achievement_walker_assault", "That armor's too strong for blasters.", ["blizzard", "blizzard 4"]),
    ("achievement_bounty_fleet", "There will be a substantial reward.", ["slave i", "hound's tooth"]),
    ("achievement_hunter_armada", "No disintegrations.", ["mist hunter", "punishing one"]),
    ("achievement_rogue_squadron", "Lock S-foils in attack position.", ["red 5", "red leader"]),
    ("achievement_gold_squadron", "Stay on target!", ["gold leader", "gold 1"]),
    ("achievement_imperial_navy", "Concentrate all fire on that Super Star Destroyer!", ["executor", "chimaera"]),
    ("achievement_blockade", "A communications disruption can only mean one thing.", ["blockade flagship", "droid control"]),
    ("achievement_cloud_cars", "I've just made a deal that will keep the Empire out of here forever.", ["cloud car", "combat cloud car"]),
]

for key, quote, cards in _card_combos:
    ACHIEVEMENTS[key] = AchievementDef(
        key=key,
        quote=quote,
        trigger='cards_together',
        cards=[c.lower() for c in cards]
    )

# -----------------------------------------------------------------------------
# Location-Specific Combos (2)
# -----------------------------------------------------------------------------

ACHIEVEMENTS["achievement_leia_han_hoth"] = AchievementDef(
    key="achievement_leia_han_hoth",
    quote="Why, you stuck-up, half-witted, scruffy-looking nerf herder!",
    trigger='cards_at_site',
    cards=["leia", "han"],
    site_filter="hoth"
)
ACHIEVEMENTS["achievement_sand"] = AchievementDef(
    key="achievement_sand",
    quote="I Don't Like Sand. It's Coarse And Rough And Irritating. And It Gets Everywhere.",
    trigger='cards_at_site',
    cards=["anakin"],
    site_filter="tatooine"
)

# -----------------------------------------------------------------------------
# Combat/Damage (5)
# -----------------------------------------------------------------------------

ACHIEVEMENTS["achievement_60_damage"] = AchievementDef(
    key="achievement_60_damage",
    quote="We seem to be made to suffer. It's our lot in life.",
    trigger='damage',
    threshold=60
)
ACHIEVEMENTS["achievement_r2_killed"] = AchievementDef(
    key="achievement_r2_killed",
    quote="We're doomed.",
    trigger='card_killed',
    card_match="r2-d2"
)
ACHIEVEMENTS["achievement_chewie_killed"] = AchievementDef(
    key="achievement_chewie_killed",
    quote="Will somebody get this big walking carpet out of my way.",
    trigger='card_killed',
    card_match="chew"
)
ACHIEVEMENTS["achievement_han_boba"] = AchievementDef(
    key="achievement_han_boba",
    quote="He's no good to me dead.",
    trigger='card_killed_by',
    card_match="han",
    cards=["boba"]  # Killed while Boba was present
)

# -----------------------------------------------------------------------------
# NEW: Route Score Achievements (4)
# -----------------------------------------------------------------------------

ACHIEVEMENTS["achievement_perfect_route"] = AchievementDef(
    key="achievement_perfect_route",
    quote="A hyperspace route this good could fund a rebellion!",
    trigger='route_score',
    threshold=50
)
ACHIEVEMENTS["achievement_first_sellable"] = AchievementDef(
    key="achievement_first_sellable",
    quote="Your first sellable route! I knew you had potential.",
    trigger='first_route_score',
    threshold=30
)
ACHIEVEMENTS["achievement_comeback"] = AchievementDef(
    key="achievement_comeback",
    quote="From the brink of failure to profit!",
    trigger='comeback',  # Special: go from negative to 30+
    threshold=30
)
ACHIEVEMENTS["achievement_speedrun"] = AchievementDef(
    key="achievement_speedrun",
    quote="The Kessel Run has nothing on this!",
    trigger='speedrun',  # Win in 5 or fewer turns
    threshold=5
)

# -----------------------------------------------------------------------------
# NEW: Gameplay Achievements (5)
# -----------------------------------------------------------------------------

ACHIEVEMENTS["achievement_pacifist"] = AchievementDef(
    key="achievement_pacifist",
    quote="Violence is never the answer... apparently.",
    trigger='pacifist',  # Win without initiating battles
)
ACHIEVEMENTS["achievement_blitzkrieg"] = AchievementDef(
    key="achievement_blitzkrieg",
    quote="Aggressive negotiations complete.",
    trigger='battles_won',
    threshold=3
)
ACHIEVEMENTS["achievement_fortress"] = AchievementDef(
    key="achievement_fortress",
    quote="The galaxy trembles at your dominance.",
    trigger='locations_controlled',
    threshold=5
)
ACHIEVEMENTS["achievement_economist"] = AchievementDef(
    key="achievement_economist",
    quote="A credit saved is a credit earned.",
    trigger='force_remaining',
    threshold=15
)
ACHIEVEMENTS["achievement_collector"] = AchievementDef(
    key="achievement_collector",
    quote="Impressive collection you have there.",
    trigger='hand_size',
    threshold=8
)

# -----------------------------------------------------------------------------
# NEW: Meta Achievements (5)
# -----------------------------------------------------------------------------

ACHIEVEMENTS["achievement_regular"] = AchievementDef(
    key="achievement_regular",
    quote="A regular customer! The droid remembers you.",
    trigger='games_played',
    threshold=10
)
ACHIEVEMENTS["achievement_veteran"] = AchievementDef(
    key="achievement_veteran",
    quote="You've logged more hyperspace hours than most pilots.",
    trigger='games_played',
    threshold=50
)
ACHIEVEMENTS["achievement_legend"] = AchievementDef(
    key="achievement_legend",
    quote="Your name echoes across the trade routes.",
    trigger='games_played',
    threshold=100
)
ACHIEVEMENTS["achievement_perfectionist"] = AchievementDef(
    key="achievement_perfectionist",
    quote="Achievement unlocked: achievement unlocker.",
    trigger='achievements',
    threshold=50
)
ACHIEVEMENTS["achievement_highroller"] = AchievementDef(
    key="achievement_highroller",
    quote="The traders speak your name with reverence.",
    trigger='ast_score',
    threshold=500
)

# -----------------------------------------------------------------------------
# NEW: Seasonal/Random (2)
# -----------------------------------------------------------------------------

ACHIEVEMENTS["achievement_lucky"] = AchievementDef(
    key="achievement_lucky",
    quote="Never tell me the odds!",
    trigger='lucky_win',  # Win while behind all game
)
ACHIEVEMENTS["achievement_unlucky"] = AchievementDef(
    key="achievement_unlucky",
    quote="Even droids feel sympathy sometimes.",
    trigger='unlucky_loss',  # Lose while ahead all game
)


# =============================================================================
# Achievement Tracker Class
# =============================================================================

class AchievementTracker:
    """
    Tracks and awards achievements during games.

    Checks board state each turn for card-based achievements.
    Checks game end for score/meta achievements.
    """

    TOTAL_ACHIEVEMENTS = len(ACHIEVEMENTS)  # 139

    def __init__(self, stats_repo: 'StatsRepository' = None):
        """
        Initialize tracker.

        Args:
            stats_repo: Stats repository for persistence
        """
        self.stats_repo = stats_repo

        # Per-game state tracking
        self._cards_seen_this_game: Set[str] = set()
        self._cards_on_board_previously: Set[str] = set()
        self._achievements_triggered_this_game: Set[str] = set()
        self._lowest_score_this_game: int = None
        self._highest_score_this_game: int = None
        self._battles_initiated: int = 0
        self._battles_won: int = 0

    def reset_for_game(self):
        """Reset tracking for new game"""
        self._cards_seen_this_game.clear()
        self._cards_on_board_previously.clear()
        self._achievements_triggered_this_game.clear()
        self._lowest_score_this_game = None
        self._highest_score_this_game = None
        self._battles_initiated = 0
        self._battles_won = 0

    def check_board_for_achievements(self, board_state: 'BoardState',
                                     opponent_name: str) -> List[str]:
        """
        Check current board state for achievements.

        Args:
            board_state: Current game state
            opponent_name: Opponent's name for persistence

        Returns:
            List of achievement messages to send
        """
        messages = []

        # Get all cards currently on board (dict of title -> type)
        cards_on_board = self._get_cards_on_board(board_state)

        # Check single card achievements
        for card_title, card_type in cards_on_board.items():
            messages.extend(self._check_single_card(card_title, card_type, board_state, opponent_name))

        # Check card combinations
        messages.extend(self._check_card_combos(board_state, opponent_name))

        # Check for killed cards (was on board, now isn't)
        current_titles = set(cards_on_board.keys())
        messages.extend(self._check_killed_cards(current_titles, board_state, opponent_name))

        # Update tracking (just the titles for killed card detection)
        self._cards_on_board_previously = current_titles.copy()
        self._cards_seen_this_game.update(current_titles)

        return messages

    def _get_cards_on_board(self, board_state: 'BoardState') -> Dict[str, str]:
        """
        Get all cards currently on board with their types.

        Returns:
            Dict mapping card_title (lowercase) -> card_type
        """
        cards = {}

        # Cards at locations
        for loc in board_state.locations:
            if loc is None:
                continue
            # Add location itself
            if loc.site_name:
                cards[loc.site_name.lower()] = "Location"
            # Add cards at location
            for card in loc.my_cards:
                if card.card_title:
                    cards[card.card_title.lower()] = getattr(card, 'card_type', '') or ''
            for card in loc.their_cards:
                if card.card_title:
                    cards[card.card_title.lower()] = getattr(card, 'card_type', '') or ''

        return cards

    def _check_single_card(self, card_title: str, actual_card_type: str,
                          board_state: 'BoardState', opponent_name: str) -> List[str]:
        """Check single-card achievements"""
        messages = []
        card_lower = card_title.lower()

        for key, ach in ACHIEVEMENTS.items():
            if ach.trigger not in ['card_in_play', 'my_card', 'their_card']:
                continue
            if key in self._achievements_triggered_this_game:
                continue
            if not ach.card_match:
                continue

            # Check if card matches (title AND type)
            if not self._card_matches(card_lower, ach.card_match, ach.card_type, actual_card_type):
                continue

            # Check ownership if required
            if ach.trigger == 'my_card':
                if not self._is_my_card(card_title, board_state):
                    continue
            elif ach.trigger == 'their_card':
                if not self._is_their_card(card_title, board_state):
                    continue

            # Award achievement
            msg = self._award_achievement(key, ach, opponent_name)
            if msg:
                messages.append(msg)

        return messages

    def _card_matches(self, card_title: str, card_match: str,
                      required_type: str, actual_type: str) -> bool:
        """
        Check if a card title matches the achievement criteria.

        Args:
            card_title: The card's title (lowercase)
            card_match: The substring to match in the title
            required_type: The type required by the achievement (e.g., "Character")
            actual_type: The actual card type from the board

        Returns:
            True if the card matches both title and type requirements
        """
        # Basic contains check
        if card_match not in card_title:
            return False

        # Check type restriction if specified
        if required_type:
            # Normalize types for comparison (handle variations like "Character" vs "Characters")
            req_lower = required_type.lower().rstrip('s')
            act_lower = actual_type.lower().rstrip('s')
            if req_lower != act_lower:
                return False

        # For Location-type achievements, be stricter about Systems vs Sites
        # Systems don't have ':' in the name, Sites do (e.g., "Kamino: Clone Birthing Center")
        if required_type == "Location" and ':' not in card_match:
            # Require the card to also be a System (no colon)
            if ':' in card_title:
                return False

        return True

    def _check_card_combos(self, board_state: 'BoardState',
                          opponent_name: str) -> List[str]:
        """Check multi-card combination achievements"""
        messages = []

        for key, ach in ACHIEVEMENTS.items():
            if ach.trigger not in ['cards_together', 'cards_at_site']:
                continue
            if key in self._achievements_triggered_this_game:
                continue
            if not ach.cards:
                continue

            # Check each location
            for loc in board_state.locations:
                if loc is None:
                    continue

                # For site-specific, check location name
                if ach.trigger == 'cards_at_site' and ach.site_filter:
                    site_name = (loc.site_name or "").lower()
                    if ach.site_filter.lower() not in site_name:
                        continue

                # Get cards at this location
                cards_here = []
                for card in loc.my_cards:
                    if card.card_title:
                        cards_here.append(card.card_title.lower())
                for card in loc.their_cards:
                    if card.card_title:
                        cards_here.append(card.card_title.lower())

                # Check if all required cards are present
                found_all = True
                for required in ach.cards:
                    found = any(required.lower() in c for c in cards_here)
                    if not found:
                        found_all = False
                        break

                if found_all:
                    msg = self._award_achievement(key, ach, opponent_name)
                    if msg:
                        messages.append(msg)
                    break  # Only award once per achievement

        return messages

    def _check_killed_cards(self, current_cards: Set[str], board_state: 'BoardState',
                           opponent_name: str) -> List[str]:
        """Check for cards that were removed (killed)"""
        messages = []

        # Find cards that were on board but now aren't
        removed = self._cards_on_board_previously - current_cards

        for key, ach in ACHIEVEMENTS.items():
            if ach.trigger not in ['card_killed', 'card_killed_by']:
                continue
            if key in self._achievements_triggered_this_game:
                continue
            if not ach.card_match:
                continue

            # Check if the target card was removed
            target_removed = any(ach.card_match.lower() in card for card in removed)
            if not target_removed:
                continue

            # For card_killed_by, check if killer is still present
            if ach.trigger == 'card_killed_by' and ach.cards:
                killer_present = any(
                    any(killer.lower() in card for card in current_cards)
                    for killer in ach.cards
                )
                if not killer_present:
                    continue

            msg = self._award_achievement(key, ach, opponent_name)
            if msg:
                messages.append(msg)

        return messages

    def _is_my_card(self, card_title: str, board_state: 'BoardState') -> bool:
        """Check if a card belongs to the bot"""
        for loc in board_state.locations:
            if loc is None:
                continue
            for card in loc.my_cards:
                if card.card_title and card_title.lower() in card.card_title.lower():
                    return True
        return False

    def _is_their_card(self, card_title: str, board_state: 'BoardState') -> bool:
        """Check if a card belongs to opponent"""
        for loc in board_state.locations:
            if loc is None:
                continue
            for card in loc.their_cards:
                if card.card_title and card_title.lower() in card.card_title.lower():
                    return True
        return False

    def _award_achievement(self, key: str, ach: AchievementDef,
                          opponent_name: str) -> Optional[str]:
        """
        Award an achievement if not already unlocked.

        Returns message to send, or None if already unlocked.
        """
        self._achievements_triggered_this_game.add(key)

        if not self.stats_repo:
            # No persistence, just return the message
            return f"{ach.quote} (1/{self.TOTAL_ACHIEVEMENTS})"

        # Check if already has this achievement
        if self.stats_repo.has_achievement(opponent_name, key):
            return None

        # Unlock it
        newly_unlocked, total = self.stats_repo.unlock_achievement(opponent_name, key)

        if newly_unlocked:
            logger.info(f"Achievement unlocked for {opponent_name}: {key}")
            return f"{ach.quote} ({total}/{self.TOTAL_ACHIEVEMENTS})"

        return None

    # =========================================================================
    # Game End Achievements
    # =========================================================================

    def check_game_end_achievements(self, opponent_name: str, won: bool,
                                    route_score: int, turns: int,
                                    force_remaining: int,
                                    player_stats) -> List[str]:
        """
        Check achievements at game end.

        Args:
            opponent_name: Opponent's name
            won: Whether the player won
            route_score: Final route score
            turns: Number of turns
            force_remaining: Force pile at end
            player_stats: Player's stats record

        Returns:
            List of achievement messages
        """
        messages = []

        if not self.stats_repo or not player_stats:
            return messages

        # Route score achievements
        if won and route_score >= 50:
            msg = self._award_achievement('achievement_perfect_route',
                                         ACHIEVEMENTS['achievement_perfect_route'],
                                         opponent_name)
            if msg:
                messages.append(msg)

        # First sellable route
        if won and route_score >= 30:
            msg = self._award_achievement('achievement_first_sellable',
                                         ACHIEVEMENTS['achievement_first_sellable'],
                                         opponent_name)
            if msg:
                messages.append(msg)

        # Speedrun (win in 5 or fewer turns)
        if won and turns <= 5:
            msg = self._award_achievement('achievement_speedrun',
                                         ACHIEVEMENTS['achievement_speedrun'],
                                         opponent_name)
            if msg:
                messages.append(msg)

        # Economist (high force remaining)
        if won and force_remaining >= 15:
            msg = self._award_achievement('achievement_economist',
                                         ACHIEVEMENTS['achievement_economist'],
                                         opponent_name)
            if msg:
                messages.append(msg)

        # Meta achievements based on player stats
        games = player_stats.games_played

        if games >= 10:
            msg = self._award_achievement('achievement_regular',
                                         ACHIEVEMENTS['achievement_regular'],
                                         opponent_name)
            if msg:
                messages.append(msg)

        if games >= 50:
            msg = self._award_achievement('achievement_veteran',
                                         ACHIEVEMENTS['achievement_veteran'],
                                         opponent_name)
            if msg:
                messages.append(msg)

        if games >= 100:
            msg = self._award_achievement('achievement_legend',
                                         ACHIEVEMENTS['achievement_legend'],
                                         opponent_name)
            if msg:
                messages.append(msg)

        # Astrogation score achievement
        if player_stats.total_ast_score >= 500:
            msg = self._award_achievement('achievement_highroller',
                                         ACHIEVEMENTS['achievement_highroller'],
                                         opponent_name)
            if msg:
                messages.append(msg)

        # Achievement count achievement - use actual count from Achievement table
        actual_achievement_count = self.stats_repo.get_achievement_count(opponent_name)
        if actual_achievement_count >= 50:
            msg = self._award_achievement('achievement_perfectionist',
                                         ACHIEVEMENTS['achievement_perfectionist'],
                                         opponent_name)
            if msg:
                messages.append(msg)

        return messages

    def record_damage(self, damage: int, opponent_name: str) -> Optional[str]:
        """Record damage and check for damage achievement"""
        if damage >= 60:
            msg = self._award_achievement('achievement_60_damage',
                                         ACHIEVEMENTS['achievement_60_damage'],
                                         opponent_name)
            return msg
        return None

    def get_achievement_count(self, opponent_name: str) -> int:
        """Get player's achievement count"""
        if not self.stats_repo:
            return 0
        return self.stats_repo.get_achievement_count(opponent_name)
