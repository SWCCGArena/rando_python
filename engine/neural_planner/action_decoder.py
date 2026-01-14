"""
Action Decoder - Converts neural network action to DeploymentPlan.

Takes an action index (0-20) and creates a DeploymentPlan with
appropriate instructions that match the interface expected by
DeployEvaluator.

Actions:
- 0: HOLD_BACK - Don't deploy this phase
- 1-16: DEPLOY_TO_LOC_N - Deploy to specific location index
- 17: DEPLOY_LOCATION_CARD - Deploy a location from hand
- 18: ESTABLISH_GROUND - Auto-select best ground location to establish
- 19: ESTABLISH_SPACE - Auto-select best space location to establish
- 20: REINFORCE_BEST - Reinforce the most threatened position
"""

import logging
from typing import Any, List, Optional, Set

from engine.deploy_planner import (
    DeploymentPlan,
    DeploymentInstruction,
    DeployStrategy,
    LocationAnalysis,
)
from engine.card_loader import get_card

logger = logging.getLogger(__name__)

# Action indices (must match state_encoder.py)
ACTION_HOLD_BACK = 0
ACTION_DEPLOY_LOC_START = 1
ACTION_DEPLOY_LOC_END = 16
ACTION_DEPLOY_LOCATION_CARD = 17
ACTION_ESTABLISH_GROUND = 18
ACTION_ESTABLISH_SPACE = 19
ACTION_REINFORCE_BEST = 20


class ActionDecoder:
    """
    Decodes neural network action to DeploymentPlan.

    Creates plans that match the interface expected by DeployEvaluator,
    reusing existing instruction-building logic where possible.
    """

    def __init__(self):
        """Initialize the action decoder."""
        pass

    def decode(
        self,
        action: int,
        board_state: Any,
        confidence: float,
    ) -> DeploymentPlan:
        """
        Convert action index to DeploymentPlan with instructions.

        Args:
            action: Action index (0-20)
            board_state: Current BoardState
            confidence: Network's confidence in this action (0-1)

        Returns:
            DeploymentPlan with strategy and instructions
        """
        force_pile = getattr(board_state, 'force_pile', 0)

        if action == ACTION_HOLD_BACK:
            return self._create_hold_back_plan(board_state, confidence)

        if ACTION_DEPLOY_LOC_START <= action <= ACTION_DEPLOY_LOC_END:
            location_idx = action - ACTION_DEPLOY_LOC_START
            return self._create_location_deploy_plan(board_state, location_idx, confidence)

        if action == ACTION_DEPLOY_LOCATION_CARD:
            return self._create_deploy_location_card_plan(board_state, confidence)

        if action == ACTION_ESTABLISH_GROUND:
            return self._create_establish_plan(board_state, confidence, domain='ground')

        if action == ACTION_ESTABLISH_SPACE:
            return self._create_establish_plan(board_state, confidence, domain='space')

        if action == ACTION_REINFORCE_BEST:
            return self._create_reinforce_plan(board_state, confidence)

        # Unknown action - fall back to hold
        logger.warning(f"Unknown action {action}, defaulting to HOLD_BACK")
        return self._create_hold_back_plan(board_state, confidence)

    def _create_hold_back_plan(self, board_state: Any, confidence: float) -> DeploymentPlan:
        """Create a HOLD_BACK plan - don't deploy anything this phase."""
        return DeploymentPlan(
            strategy=DeployStrategy.HOLD_BACK,
            reason=f"Neural: hold back (confidence={confidence:.2f})",
            instructions=[],
            hold_back_cards=set(),
            target_locations=[],
            total_force_available=getattr(board_state, 'force_pile', 0),
            force_reserved_for_battle=1,
            force_to_spend=0,
        )

    def _create_location_deploy_plan(
        self,
        board_state: Any,
        location_idx: int,
        confidence: float,
    ) -> DeploymentPlan:
        """Create a plan to deploy to a specific location index."""
        locations = getattr(board_state, 'locations', [])
        cards_in_hand = getattr(board_state, 'cards_in_hand', [])
        force_pile = getattr(board_state, 'force_pile', 0)

        # Get the target location
        if location_idx >= len(locations) or locations[location_idx] is None:
            logger.warning(f"Invalid location index {location_idx}, falling back to hold")
            return self._create_hold_back_plan(board_state, confidence)

        loc = locations[location_idx]
        is_ground = getattr(loc, 'is_ground', False) or getattr(loc, 'is_site', False)
        is_space = getattr(loc, 'is_space', False)

        # Find deployable cards for this location
        deployable = self._find_deployable_cards(
            cards_in_hand, force_pile, is_ground, is_space
        )

        if not deployable:
            logger.info(f"No deployable cards for location {location_idx}, holding")
            return self._create_hold_back_plan(board_state, confidence)

        # Determine strategy based on location state
        my_power = self._safe_call(board_state, 'my_power_at_location', 0, location_idx)
        their_power = self._safe_call(board_state, 'their_power_at_location', 0, location_idx)

        if their_power > 0:
            if my_power > 0:
                strategy = DeployStrategy.REINFORCE
                strategy_name = "reinforce"
            else:
                strategy = DeployStrategy.ESTABLISH
                strategy_name = "contest"
        else:
            if my_power > 0:
                strategy = DeployStrategy.REINFORCE
                strategy_name = "strengthen"
            else:
                strategy = DeployStrategy.ESTABLISH
                strategy_name = "establish"

        # Create instructions for best deployable cards
        instructions = []
        loc_name = getattr(loc, 'site_name', '') or getattr(loc, 'system_name', '') or f"Location {location_idx}"
        loc_card_id = getattr(loc, 'card_id', f"loc_{location_idx}")

        remaining_force = force_pile - 1  # Reserve 1 for battle
        for card in deployable:
            deploy_cost = getattr(card, 'deploy', 0)
            if deploy_cost > remaining_force:
                continue

            instructions.append(DeploymentInstruction(
                card_blueprint_id=getattr(card, 'blueprint_id', ''),
                card_name=getattr(card, 'card_title', 'Unknown'),
                target_location_id=loc_card_id,
                target_location_name=loc_name,
                priority=1,
                reason=f"Neural: {strategy_name} at {loc_name}",
                power_contribution=getattr(card, 'power', 0),
                deploy_cost=deploy_cost,
                ability_contribution=getattr(card, 'ability', 0),
            ))
            remaining_force -= deploy_cost

        # Create location analysis for the target
        loc_analysis = LocationAnalysis(
            card_id=loc_card_id,
            name=loc_name,
            is_ground=is_ground,
            is_space=is_space,
            my_power=my_power,
            their_power=their_power,
            i_control=my_power > 0 and their_power == 0,
            they_control=their_power > 0 and my_power == 0,
            contested=my_power > 0 and their_power > 0,
            location_index=location_idx,
        )

        total_cost = sum(inst.deploy_cost for inst in instructions)

        return DeploymentPlan(
            strategy=strategy,
            reason=f"Neural: {strategy_name} at {loc_name} (confidence={confidence:.2f})",
            instructions=instructions,
            hold_back_cards=set(),
            target_locations=[loc_analysis],
            total_force_available=force_pile,
            force_reserved_for_battle=1,
            force_to_spend=total_cost,
            original_plan_cost=total_cost,
        )

    def _create_deploy_location_card_plan(
        self,
        board_state: Any,
        confidence: float,
    ) -> DeploymentPlan:
        """Create a plan to deploy a location card from hand."""
        cards_in_hand = getattr(board_state, 'cards_in_hand', [])
        force_pile = getattr(board_state, 'force_pile', 0)

        # Find location cards in hand
        location_cards = []
        for card in cards_in_hand:
            card_type = getattr(card, 'card_type', '').lower()
            if 'location' in card_type or 'site' in card_type or 'system' in card_type:
                location_cards.append(card)

        if not location_cards:
            logger.info("No location cards in hand, holding")
            return self._create_hold_back_plan(board_state, confidence)

        # Pick the first location card (in a more sophisticated version,
        # we could score them based on icons, strategic value, etc.)
        loc_card = location_cards[0]

        instructions = [
            DeploymentInstruction(
                card_blueprint_id=getattr(loc_card, 'blueprint_id', ''),
                card_name=getattr(loc_card, 'card_title', 'Location'),
                target_location_id=None,  # Locations deploy to the table
                target_location_name=None,
                priority=0,  # Locations deploy first
                reason="Neural: deploy location",
                power_contribution=0,
                deploy_cost=getattr(loc_card, 'deploy', 0),
                ability_contribution=0,
            )
        ]

        return DeploymentPlan(
            strategy=DeployStrategy.DEPLOY_LOCATIONS,
            reason=f"Neural: deploy location (confidence={confidence:.2f})",
            instructions=instructions,
            hold_back_cards=set(),
            target_locations=[],
            total_force_available=force_pile,
            force_reserved_for_battle=1,
            force_to_spend=getattr(loc_card, 'deploy', 0),
        )

    def _create_establish_plan(
        self,
        board_state: Any,
        confidence: float,
        domain: str,
    ) -> DeploymentPlan:
        """Create a plan to establish presence at an uncontested location."""
        locations = getattr(board_state, 'locations', [])
        cards_in_hand = getattr(board_state, 'cards_in_hand', [])
        force_pile = getattr(board_state, 'force_pile', 0)

        # Find best location to establish based on domain
        best_loc = None
        best_loc_idx = -1
        best_icons = 0

        for i, loc in enumerate(locations):
            if loc is None:
                continue

            is_ground = getattr(loc, 'is_ground', False) or getattr(loc, 'is_site', False)
            is_space = getattr(loc, 'is_space', False)

            if domain == 'ground' and not is_ground:
                continue
            if domain == 'space' and not is_space:
                continue

            # Check if we can establish (no presence yet)
            my_power = self._safe_call(board_state, 'my_power_at_location', 0, i)
            if my_power > 0:
                continue  # Already have presence

            # Prefer locations with opponent icons (we can drain)
            their_icons = self._get_icons_at_location(board_state, loc, i, is_mine=False)
            if their_icons > best_icons:
                best_icons = their_icons
                best_loc = loc
                best_loc_idx = i

        if best_loc is None:
            # No location to establish - just pick first empty one
            for i, loc in enumerate(locations):
                if loc is None:
                    continue
                is_ground = getattr(loc, 'is_ground', False) or getattr(loc, 'is_site', False)
                is_space = getattr(loc, 'is_space', False)
                if domain == 'ground' and not is_ground:
                    continue
                if domain == 'space' and not is_space:
                    continue
                my_power = self._safe_call(board_state, 'my_power_at_location', 0, i)
                if my_power == 0:
                    best_loc = loc
                    best_loc_idx = i
                    break

        if best_loc is None:
            logger.info(f"No {domain} location to establish, holding")
            return self._create_hold_back_plan(board_state, confidence)

        # Delegate to location-specific deploy
        return self._create_location_deploy_plan(board_state, best_loc_idx, confidence)

    def _create_reinforce_plan(
        self,
        board_state: Any,
        confidence: float,
    ) -> DeploymentPlan:
        """Create a plan to reinforce the most threatened position."""
        locations = getattr(board_state, 'locations', [])

        # Find location where we're most behind
        worst_loc_idx = -1
        worst_differential = 0  # Most negative = most threatened

        for i, loc in enumerate(locations):
            if loc is None:
                continue

            my_power = self._safe_call(board_state, 'my_power_at_location', 0, i)
            their_power = self._safe_call(board_state, 'their_power_at_location', 0, i)

            # Only reinforce where we have presence but are losing
            if my_power == 0:
                continue

            differential = my_power - their_power
            if differential < worst_differential:
                worst_differential = differential
                worst_loc_idx = i

        if worst_loc_idx == -1:
            # No contested locations - check for opponent-only locations
            for i, loc in enumerate(locations):
                if loc is None:
                    continue
                my_power = self._safe_call(board_state, 'my_power_at_location', 0, i)
                their_power = self._safe_call(board_state, 'their_power_at_location', 0, i)
                if their_power > 0 and my_power == 0:
                    worst_loc_idx = i
                    break

        if worst_loc_idx == -1:
            logger.info("No location needs reinforcement, holding")
            return self._create_hold_back_plan(board_state, confidence)

        return self._create_location_deploy_plan(board_state, worst_loc_idx, confidence)

    def _find_deployable_cards(
        self,
        cards_in_hand: List[Any],
        force_available: int,
        is_ground: bool,
        is_space: bool,
    ) -> List[Any]:
        """Find cards that can be deployed to the target location type."""
        deployable = []

        for card in cards_in_hand:
            card_type = getattr(card, 'card_type', '').lower()
            deploy_cost = getattr(card, 'deploy', 0)

            if deploy_cost > force_available:
                continue

            # Check if card can deploy to this location type
            if is_ground:
                if 'character' in card_type or 'vehicle' in card_type:
                    deployable.append(card)
            elif is_space:
                if 'starship' in card_type:
                    deployable.append(card)

        # Sort by power (highest first)
        deployable.sort(key=lambda c: getattr(c, 'power', 0), reverse=True)
        return deployable

    def _get_icons_at_location(
        self,
        board_state: Any,
        loc: Any,
        idx: int,
        is_mine: bool,
    ) -> int:
        """Get force icons at a location."""
        if is_mine:
            result = self._safe_call(board_state, 'my_icons_at_location', None, idx)
            if result is not None:
                return result
            icons_str = getattr(loc, 'my_icons', '')
        else:
            result = self._safe_call(board_state, 'their_icons_at_location', None, idx)
            if result is not None:
                return result
            icons_str = getattr(loc, 'their_icons', '')

        if isinstance(icons_str, int):
            return icons_str
        if isinstance(icons_str, str) and icons_str.isdigit():
            return int(icons_str)
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
