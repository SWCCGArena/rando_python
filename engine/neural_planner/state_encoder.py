"""
State Encoder - Converts BoardState to fixed-size tensor for neural network.

Tensor layout [640 dimensions]:
- Global features [0:64]: Turn, force, life, drain gap, side
- Location features [64:448]: 16 locations x 24 features each
- Hand aggregate features [448:480]: Summary stats (32 dims)
- Per-card features [480:640]: Top 8 deployable cards x 20 features each (160 dims)

All values normalized to roughly [0, 1] range for stable training.

Per-card features include power, deploy cost, ability, type (ground/space),
allowing the network to learn card-specific deployment decisions.
"""

import numpy as np
from typing import List, Optional, Any
import logging

logger = logging.getLogger(__name__)

# Constants
STATE_DIM = 640  # Expanded to include per-card features
MAX_LOCATIONS = 16
LOCATION_FEATURES = 24
GLOBAL_FEATURES = 64
HAND_AGGREGATE_FEATURES = 32  # Reduced aggregate stats
MAX_CARDS_ENCODED = 8  # Top 8 deployable cards
PER_CARD_FEATURES = 20  # Features per card
HAND_FEATURES = HAND_AGGREGATE_FEATURES + (MAX_CARDS_ENCODED * PER_CARD_FEATURES)  # 32 + 160 = 192

# Action space
NUM_ACTIONS = 21
ACTION_HOLD_BACK = 0
ACTION_DEPLOY_LOC_START = 1  # Actions 1-16 are deploy to location index 0-15
ACTION_DEPLOY_LOC_END = 16
ACTION_DEPLOY_LOCATION_CARD = 17
ACTION_ESTABLISH_GROUND = 18
ACTION_ESTABLISH_SPACE = 19
ACTION_REINFORCE_BEST = 20


class StateEncoder:
    """
    Encodes BoardState to fixed-size tensor for neural network input.

    Also generates action masks to prevent invalid actions.
    """

    def __init__(self):
        """Initialize the state encoder."""
        pass

    def encode(self, board_state: Any) -> np.ndarray:
        """
        Convert BoardState to [640] feature vector.

        Args:
            board_state: BoardState or MockBoardState object

        Returns:
            np.ndarray of shape [640] with float32 values
        """
        features = np.zeros(STATE_DIM, dtype=np.float32)

        # === Global features [0:64] ===
        self._encode_global_features(board_state, features, offset=0)

        # === Location features [64:448] ===
        self._encode_location_features(board_state, features, offset=GLOBAL_FEATURES)

        # === Hand features [448:640] (aggregate + per-card) ===
        self._encode_hand_features(board_state, features, offset=GLOBAL_FEATURES + MAX_LOCATIONS * LOCATION_FEATURES)

        return features

    def _encode_global_features(self, bs: Any, features: np.ndarray, offset: int) -> None:
        """Encode global game state features."""
        idx = offset

        # Turn number (normalized, cap at 20)
        features[idx] = min(getattr(bs, 'turn_number', 1) / 20.0, 1.0)
        idx += 1

        # My resources (normalized)
        features[idx] = min(getattr(bs, 'force_pile', 0) / 20.0, 1.0)
        idx += 1
        features[idx] = min(getattr(bs, 'used_pile', 0) / 20.0, 1.0)
        idx += 1
        features[idx] = min(getattr(bs, 'reserve_deck', 0) / 60.0, 1.0)
        idx += 1

        # Opponent resources
        features[idx] = min(getattr(bs, 'their_force_pile', 0) / 20.0, 1.0)
        idx += 1
        features[idx] = min(getattr(bs, 'their_used_pile', 0) / 20.0, 1.0)
        idx += 1
        features[idx] = min(getattr(bs, 'their_reserve_deck', 0) / 60.0, 1.0)
        idx += 1

        # Life force (total remaining cards = reserve + used + force pile)
        my_life = getattr(bs, 'reserve_deck', 0) + getattr(bs, 'used_pile', 0) + getattr(bs, 'force_pile', 0)
        their_life = getattr(bs, 'their_reserve_deck', 0) + getattr(bs, 'their_used_pile', 0) + getattr(bs, 'their_force_pile', 0)
        features[idx] = min(my_life / 60.0, 1.0)
        idx += 1
        features[idx] = min(their_life / 60.0, 1.0)
        idx += 1

        # Life force advantage (normalized to [-1, 1])
        life_diff = my_life - their_life
        features[idx] = np.clip(life_diff / 30.0, -1.0, 1.0)
        idx += 1

        # Side (one-hot: dark=0, light=1)
        my_side = getattr(bs, 'my_side', 'dark')
        features[idx] = 1.0 if my_side == 'dark' else 0.0
        idx += 1
        features[idx] = 1.0 if my_side == 'light' else 0.0
        idx += 1

        # Hand size
        hand_size = len(getattr(bs, 'cards_in_hand', []))
        features[idx] = min(hand_size / 16.0, 1.0)
        idx += 1

        # Opponent hand size
        their_hand_size = getattr(bs, 'their_hand_size', 0)
        features[idx] = min(their_hand_size / 16.0, 1.0)
        idx += 1

        # Total power
        my_power = self._safe_call(bs, 'total_my_power', 0)
        their_power = self._safe_call(bs, 'total_their_power', 0)
        features[idx] = min(my_power / 50.0, 1.0)
        idx += 1
        features[idx] = min(their_power / 50.0, 1.0)
        idx += 1

        # Power advantage
        power_diff = my_power - their_power
        features[idx] = np.clip(power_diff / 30.0, -1.0, 1.0)
        idx += 1

        # Force generation (icons)
        my_gen = getattr(bs, 'dark_generation', 0) if my_side == 'dark' else getattr(bs, 'light_generation', 0)
        their_gen = getattr(bs, 'light_generation', 0) if my_side == 'dark' else getattr(bs, 'dark_generation', 0)
        features[idx] = min(my_gen / 10.0, 1.0)
        idx += 1
        features[idx] = min(their_gen / 10.0, 1.0)
        idx += 1

        # Drain gap and contested locations
        drain_gap, num_contested, num_bleed = self._calculate_drain_stats(bs)
        features[idx] = np.clip(drain_gap / 5.0, -1.0, 1.0)  # drain_gap in [-5, 5] typical
        idx += 1
        features[idx] = min(num_contested / 5.0, 1.0)
        idx += 1
        features[idx] = min(num_bleed / 5.0, 1.0)
        idx += 1

        # Phase (one-hot: Deploy, Battle, Move, Draw, Control, Activate)
        phase = getattr(bs, 'current_phase', '').upper()
        phases = ['DEPLOY', 'BATTLE', 'MOVE', 'DRAW', 'CONTROL', 'ACTIVATE']
        for p in phases:
            features[idx] = 1.0 if phase == p else 0.0
            idx += 1

        # Is my turn
        is_my_turn = self._safe_call(bs, 'is_my_turn', True)
        features[idx] = 1.0 if is_my_turn else 0.0
        idx += 1

        # Consecutive hold turns (to prevent hold loops)
        consecutive_holds = getattr(bs, 'consecutive_hold_turns', 0)
        features[idx] = min(consecutive_holds / 3.0, 1.0)
        idx += 1

        # Hold failed last turn
        features[idx] = 1.0 if getattr(bs, 'hold_failed_last_turn', False) else 0.0
        idx += 1

        # Remaining global slots are padding (reserved for future features)

    def _encode_location_features(self, bs: Any, features: np.ndarray, offset: int) -> None:
        """Encode features for each location (up to 16)."""
        locations = getattr(bs, 'locations', [])
        my_side = getattr(bs, 'my_side', 'dark')

        for i in range(MAX_LOCATIONS):
            loc_offset = offset + i * LOCATION_FEATURES

            if i >= len(locations) or locations[i] is None:
                # Location doesn't exist - all zeros (exists flag = 0)
                continue

            loc = locations[i]
            idx = loc_offset

            # Exists flag
            features[idx] = 1.0
            idx += 1

            # Location type
            is_ground = getattr(loc, 'is_ground', False) or getattr(loc, 'is_site', False)
            is_space = getattr(loc, 'is_space', False)
            features[idx] = 1.0 if is_ground else 0.0
            idx += 1
            features[idx] = 1.0 if is_space else 0.0
            idx += 1

            # Interior/exterior
            features[idx] = 1.0 if getattr(loc, 'is_interior', False) else 0.0
            idx += 1
            features[idx] = 1.0 if getattr(loc, 'is_exterior', True) else 0.0
            idx += 1

            # Power at location
            my_power = self._safe_call(bs, 'my_power_at_location', 0, i)
            their_power = self._safe_call(bs, 'their_power_at_location', 0, i)
            features[idx] = min(my_power / 20.0, 1.0)
            idx += 1
            features[idx] = min(their_power / 20.0, 1.0)
            idx += 1

            # Power differential
            power_diff = my_power - their_power
            features[idx] = np.clip(power_diff / 15.0, -1.0, 1.0)
            idx += 1

            # Icons at location
            my_icons = self._get_icons_at_location(bs, loc, i, my_side, is_mine=True)
            their_icons = self._get_icons_at_location(bs, loc, i, my_side, is_mine=False)
            features[idx] = min(my_icons / 3.0, 1.0)
            idx += 1
            features[idx] = min(their_icons / 3.0, 1.0)
            idx += 1

            # Control status
            i_control = my_power > 0 and their_power == 0
            they_control = their_power > 0 and my_power == 0
            contested = my_power > 0 and their_power > 0
            features[idx] = 1.0 if i_control else 0.0
            idx += 1
            features[idx] = 1.0 if they_control else 0.0
            idx += 1
            features[idx] = 1.0 if contested else 0.0
            idx += 1

            # Am I draining / being drained
            am_draining = i_control and their_icons > 0
            being_drained = they_control and my_icons > 0
            features[idx] = 1.0 if am_draining else 0.0
            idx += 1
            features[idx] = 1.0 if being_drained else 0.0
            idx += 1

            # Card counts
            my_cards = len(getattr(loc, 'my_cards', []))
            their_cards = len(getattr(loc, 'their_cards', []))
            features[idx] = min(my_cards / 5.0, 1.0)
            idx += 1
            features[idx] = min(their_cards / 5.0, 1.0)
            idx += 1

            # Can deploy here (based on hand - computed later in action mask)
            # For now just mark as potentially valid
            features[idx] = 1.0 if is_ground or is_space else 0.0
            idx += 1

            # Location type one-hot (site vs system)
            is_site = getattr(loc, 'is_site', False)
            features[idx] = 1.0 if is_site else 0.0
            idx += 1
            features[idx] = 1.0 if not is_site else 0.0  # is_system
            idx += 1

            # Parsec (for space locations)
            parsec = getattr(loc, 'parsec', 0)
            features[idx] = min(parsec / 10.0, 1.0)
            idx += 1

            # Is battleground
            # For now assume exterior sites and systems are battlegrounds
            is_battleground = getattr(loc, 'is_exterior', True) or is_space
            features[idx] = 1.0 if is_battleground else 0.0
            idx += 1

            # Remaining slots are padding

    def _encode_hand_features(self, bs: Any, features: np.ndarray, offset: int) -> None:
        """
        Encode hand features: aggregate stats + per-card details.

        Layout:
        - [offset:offset+32]: Aggregate statistics
        - [offset+32:offset+192]: Per-card features (8 cards x 20 features)
        """
        cards_in_hand = getattr(bs, 'cards_in_hand', [])
        force_available = getattr(bs, 'force_pile', 0)

        idx = offset

        # === Aggregate stats [32 dims] ===
        total_ground_power = 0
        total_space_power = 0
        num_characters = 0
        num_starships = 0
        num_vehicles = 0
        num_locations = 0
        num_pilots = 0
        num_main_characters = 0
        min_deploy = 99
        max_deploy = 0
        affordable_ground = 0
        affordable_space = 0

        # Collect deployable cards for per-card encoding
        deployable_cards = []

        for card in cards_in_hand:
            card_type = getattr(card, 'card_type', '').lower()
            power = getattr(card, 'power', 0) or 0
            deploy = getattr(card, 'deploy', 0) or 0
            ability = getattr(card, 'ability', 0) or 0
            card_title = getattr(card, 'card_title', '').lower()

            is_character = 'character' in card_type
            is_starship = 'starship' in card_type
            is_vehicle = 'vehicle' in card_type
            is_location = 'location' in card_type or 'site' in card_type or 'system' in card_type
            is_pilot = 'pilot' in card_type or 'pilot' in card_title
            is_ground = is_character or is_vehicle
            is_space = is_starship
            can_afford = deploy <= force_available

            if deploy > 0:
                min_deploy = min(min_deploy, deploy)
                max_deploy = max(max_deploy, deploy)

            if is_character:
                num_characters += 1
                total_ground_power += power
                if can_afford:
                    affordable_ground += 1
            elif is_starship:
                num_starships += 1
                total_space_power += power
                if can_afford:
                    affordable_space += 1
            elif is_vehicle:
                num_vehicles += 1
                total_ground_power += power
                if can_afford:
                    affordable_ground += 1
            elif is_location:
                num_locations += 1

            if is_pilot:
                num_pilots += 1
            if ability >= 4:
                num_main_characters += 1

            # Collect deployable cards (characters, ships, vehicles with power)
            if (is_character or is_starship or is_vehicle) and power > 0:
                deployable_cards.append({
                    'power': power,
                    'deploy': deploy,
                    'ability': ability,
                    'is_character': is_character,
                    'is_starship': is_starship,
                    'is_vehicle': is_vehicle,
                    'is_pilot': is_pilot,
                    'is_ground': is_ground,
                    'is_space': is_space,
                    'can_afford': can_afford,
                    'is_unique': ability >= 4 or card_title.startswith('•'),
                    'destiny': getattr(card, 'destiny', 0) or 0,
                    'forfeit': getattr(card, 'forfeit', 0) or 0,
                })

        if min_deploy == 99:
            min_deploy = 0

        # Sort deployable cards by power (highest first) for consistent encoding
        deployable_cards.sort(key=lambda c: (-c['power'], c['deploy']))

        # Encode aggregate features [32 dims]
        features[idx] = min(total_ground_power / 30.0, 1.0); idx += 1
        features[idx] = min(total_space_power / 30.0, 1.0); idx += 1
        features[idx] = min(num_characters / 8.0, 1.0); idx += 1
        features[idx] = min(num_starships / 5.0, 1.0); idx += 1
        features[idx] = min(num_vehicles / 3.0, 1.0); idx += 1
        features[idx] = min(num_locations / 3.0, 1.0); idx += 1
        features[idx] = min(num_pilots / 4.0, 1.0); idx += 1
        features[idx] = min(num_main_characters / 3.0, 1.0); idx += 1
        features[idx] = min(min_deploy / 10.0, 1.0); idx += 1
        features[idx] = min(max_deploy / 10.0, 1.0); idx += 1
        features[idx] = min(affordable_ground / 5.0, 1.0); idx += 1
        features[idx] = min(affordable_space / 3.0, 1.0); idx += 1
        features[idx] = min(force_available / 15.0, 1.0); idx += 1
        features[idx] = min(len(deployable_cards) / 8.0, 1.0); idx += 1
        # Padding to 32
        idx = offset + HAND_AGGREGATE_FEATURES

        # === Per-card features [160 dims = 8 cards x 20 features] ===
        for card_idx in range(MAX_CARDS_ENCODED):
            card_offset = idx + card_idx * PER_CARD_FEATURES

            if card_idx < len(deployable_cards):
                card = deployable_cards[card_idx]
                self._encode_single_card(features, card_offset, card, force_available)
            # else: all zeros (no card in this slot)

    def _encode_single_card(self, features: np.ndarray, offset: int, card: dict, force_available: int) -> None:
        """
        Encode a single deployable card [20 features].

        Features:
        0: exists (1.0)
        1: power (normalized /10)
        2: deploy_cost (normalized /10)
        3: ability (normalized /6)
        4: is_character
        5: is_starship
        6: is_vehicle
        7: is_pilot
        8: is_ground_deployable
        9: is_space_deployable
        10: can_afford
        11: is_unique (main character)
        12: power_efficiency (power / deploy)
        13: destiny (normalized /7)
        14: forfeit (normalized /8)
        15: deploy_vs_force (how much force left after deploy)
        16-19: padding
        """
        idx = offset
        power = card['power']
        deploy = card['deploy']

        features[idx] = 1.0  # exists
        idx += 1
        features[idx] = min(power / 10.0, 1.0)  # power
        idx += 1
        features[idx] = min(deploy / 10.0, 1.0)  # deploy_cost
        idx += 1
        features[idx] = min(card['ability'] / 6.0, 1.0)  # ability
        idx += 1
        features[idx] = 1.0 if card['is_character'] else 0.0
        idx += 1
        features[idx] = 1.0 if card['is_starship'] else 0.0
        idx += 1
        features[idx] = 1.0 if card['is_vehicle'] else 0.0
        idx += 1
        features[idx] = 1.0 if card['is_pilot'] else 0.0
        idx += 1
        features[idx] = 1.0 if card['is_ground'] else 0.0  # ground deployable
        idx += 1
        features[idx] = 1.0 if card['is_space'] else 0.0  # space deployable
        idx += 1
        features[idx] = 1.0 if card['can_afford'] else 0.0
        idx += 1
        features[idx] = 1.0 if card['is_unique'] else 0.0
        idx += 1
        # Power efficiency: power per deploy cost
        efficiency = power / max(deploy, 1)
        features[idx] = min(efficiency / 2.0, 1.0)  # typical range 0.5-2.0
        idx += 1
        features[idx] = min(card['destiny'] / 7.0, 1.0)  # destiny
        idx += 1
        features[idx] = min(card['forfeit'] / 8.0, 1.0)  # forfeit
        idx += 1
        # Force remaining after deploy (if affordable)
        if card['can_afford']:
            remaining = (force_available - deploy) / max(force_available, 1)
            features[idx] = max(0.0, min(remaining, 1.0))
        idx += 1
        # Remaining slots are padding (16-19)

    def get_action_mask(self, board_state: Any) -> np.ndarray:
        """
        Generate valid action mask [21].

        True = action is valid, False = action is invalid.
        Invalid actions will be masked to -inf before softmax.
        """
        mask = np.zeros(NUM_ACTIONS, dtype=bool)

        # HOLD_BACK is always valid
        mask[ACTION_HOLD_BACK] = True

        locations = getattr(board_state, 'locations', [])
        cards_in_hand = getattr(board_state, 'cards_in_hand', [])
        force_available = getattr(board_state, 'force_pile', 0)

        # Analyze hand for deployable cards
        has_ground_cards = False
        has_space_cards = False
        has_location_cards = False

        for card in cards_in_hand:
            card_type = getattr(card, 'card_type', '').lower()
            deploy = getattr(card, 'deploy', 0)

            if deploy <= force_available:
                if 'character' in card_type or 'vehicle' in card_type:
                    has_ground_cards = True
                elif 'starship' in card_type:
                    has_space_cards = True

            if 'location' in card_type or 'site' in card_type or 'system' in card_type:
                # Locations typically cost 0 to deploy
                has_location_cards = True

        # Check each location for deployability
        for i, loc in enumerate(locations[:MAX_LOCATIONS]):
            if loc is None:
                continue

            is_ground = getattr(loc, 'is_ground', False) or getattr(loc, 'is_site', False)
            is_space = getattr(loc, 'is_space', False)

            if is_ground and has_ground_cards:
                mask[ACTION_DEPLOY_LOC_START + i] = True
            elif is_space and has_space_cards:
                mask[ACTION_DEPLOY_LOC_START + i] = True

        # Meta-actions
        if has_location_cards:
            mask[ACTION_DEPLOY_LOCATION_CARD] = True

        if has_ground_cards:
            mask[ACTION_ESTABLISH_GROUND] = True
            mask[ACTION_REINFORCE_BEST] = True

        if has_space_cards:
            mask[ACTION_ESTABLISH_SPACE] = True
            mask[ACTION_REINFORCE_BEST] = True

        return mask

    def _calculate_drain_stats(self, bs: Any) -> tuple:
        """Calculate drain gap, contested count, and bleed count."""
        locations = getattr(bs, 'locations', [])
        my_side = getattr(bs, 'my_side', 'dark')

        our_drain = 0
        their_drain = 0
        num_contested = 0
        num_bleed = 0

        for i, loc in enumerate(locations):
            if loc is None:
                continue

            my_power = self._safe_call(bs, 'my_power_at_location', 0, i)
            their_power = self._safe_call(bs, 'their_power_at_location', 0, i)
            my_icons = self._get_icons_at_location(bs, loc, i, my_side, is_mine=True)
            their_icons = self._get_icons_at_location(bs, loc, i, my_side, is_mine=False)

            i_control = my_power > 0 and their_power == 0
            they_control = their_power > 0 and my_power == 0
            contested = my_power > 0 and their_power > 0

            if contested:
                num_contested += 1

            # Drain calculations
            if i_control and their_icons > 0:
                our_drain += their_icons
            if they_control and my_icons > 0:
                their_drain += my_icons
                num_bleed += 1  # We're bleeding at this location

        drain_gap = our_drain - their_drain
        return drain_gap, num_contested, num_bleed

    def _get_icons_at_location(self, bs: Any, loc: Any, idx: int, my_side: str, is_mine: bool) -> int:
        """Get force icons at a location for a player."""
        # Try direct method first
        if is_mine:
            result = self._safe_call(bs, 'my_icons_at_location', None, idx)
            if result is not None:
                return result
        else:
            result = self._safe_call(bs, 'their_icons_at_location', None, idx)
            if result is not None:
                return result

        # Fall back to location attributes
        if is_mine:
            icons_str = getattr(loc, 'my_icons', '')
        else:
            icons_str = getattr(loc, 'their_icons', '')

        # Parse icons string (could be "2" or "[LS][LS]" format)
        if isinstance(icons_str, int):
            return icons_str
        if isinstance(icons_str, str):
            if icons_str.isdigit():
                return int(icons_str)
            # Count icon markers
            return icons_str.count('[')
        return 0

    def _safe_call(self, obj: Any, method_name: str, default: Any, *args):
        """Safely call a method or return default if it doesn't exist."""
        method = getattr(obj, method_name, None)
        if callable(method):
            try:
                return method(*args)
            except Exception:
                return default
        return default
