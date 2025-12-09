"""
Card Selection Evaluator

Handles CARD_SELECTION decisions - choosing cards from a list.
Ported from C# AICSHandler.cs

Decision types handled:
- "choose card to set sabacc value" -> Random selection (cycles through cards)
- "choose where to deploy" -> Pick best location
- "choose force to lose" -> Pick best card to lose
- "move/transport/transit" -> Pick best destination
- "choose a card from battle to forfeit" -> Pick lowest forfeit value
- "choose a pilot" -> Pick best pilot
- "choose card to cancel" -> Cancel opponent's cards, not ours
- "choose...clone" -> PASS (don't clone sabacc cards)
"""

import logging
import random
import re
from typing import List, Optional
from .base import ActionEvaluator, DecisionContext, EvaluatedAction, ActionType
from ..priority_cards import (
    is_priority_card,
    get_protection_score,
    get_protection_score_by_title,
)
from ..shield_strategy import score_shield_for_deployment

logger = logging.getLogger(__name__)

# Rank deltas (from C# BotAIHelper)
VERY_GOOD_DELTA = 999.0
GOOD_DELTA = 10.0
BAD_DELTA = -10.0
VERY_BAD_DELTA = -999.0


class CardSelectionEvaluator(ActionEvaluator):
    """
    Evaluates CARD_SELECTION decisions.

    These are decisions where the player must select one or more cards
    from a list (e.g., choosing where to deploy, which card to forfeit).
    """

    def __init__(self):
        super().__init__("CardSelection")

    def can_evaluate(self, context: DecisionContext) -> bool:
        """Handle CARD_SELECTION and ARBITRARY_CARDS decisions"""
        return context.decision_type in ('CARD_SELECTION', 'ARBITRARY_CARDS')

    def evaluate(self, context: DecisionContext) -> List[EvaluatedAction]:
        """Evaluate card selection options based on decision text"""
        actions = []
        bs = context.board_state
        text = context.decision_text
        text_lower = text.lower()

        # Get all card IDs from the decision
        card_ids = context.card_ids

        if not card_ids:
            logger.warning(f"No card IDs in {context.decision_type} decision")
            return []

        # =====================================================
        # ARBITRARY_CARDS-specific patterns (check first)
        # =====================================================
        if context.decision_type == 'ARBITRARY_CARDS':
            if "starting location" in text_lower:
                return self._evaluate_starting_location(context)
            elif "card to deploy from reserve deck" in text_lower and context.phase == "Play starting cards":
                # Objective starting cards - use objective handler
                return self._evaluate_starting_deploy(context)
            elif "card to take into hand" in text_lower:
                return self._evaluate_take_into_hand(context)
            elif "card to put on lost pile" in text_lower:
                # Battle damage - reuse forfeit logic
                return self._evaluate_lost_pile_selection(context)
            elif "interrupt to play from lost pile" in text_lower:
                # Recursion effect - random selection is fine
                return self._evaluate_play_from_lost_pile(context)
            elif "card to place in" in text_lower and "pile" in text_lower:
                # Generic pile placement - prefer effects/interrupts
                return self._evaluate_pile_placement(context)
            # Fall through to common patterns below

        # =====================================================
        # Common patterns (CARD_SELECTION and ARBITRARY_CARDS)
        # =====================================================
        if "choose card to set sabacc value" in text_lower:
            actions = self._evaluate_sabacc_set_value(context)
        elif "choose" in text_lower and "clone" in text_lower:
            actions = self._evaluate_sabacc_clone(context)
        elif "choose where to deploy" in text_lower:
            # DeployEvaluator handles this more comprehensively
            # Use the fixed version here that properly handles starships/docking bays
            actions = self._evaluate_deploy_location_fixed(context)
        elif "force to lose or" in text_lower and "forfeit" in text_lower:
            # COMBINED decision: lose force OR forfeit card
            # Must come before individual force_loss/forfeit checks
            actions = self._evaluate_force_loss_or_forfeit(context)
        elif "choose force to lose" in text_lower:
            actions = self._evaluate_force_loss(context)
        elif any(x in text_lower for x in ["move", "transport", "transit"]):
            actions = self._evaluate_move_destination(context)
        elif "choose a card from battle to forfeit" in text_lower:
            actions = self._evaluate_forfeit(context)
        elif "if desired" in text_lower and not context.no_pass:
            actions = self._evaluate_optional_action(context)
        elif "choose a pilot" in text_lower:
            actions = self._evaluate_pilot_selection(context)
        elif "choose card to cancel" in text_lower:
            actions = self._evaluate_cancel_selection(context)
        elif "choose card from hand" in text_lower:
            actions = self._evaluate_hand_selection(context)
        elif "choose" in text_lower and "location" in text_lower and "deploy" in text_lower:
            actions = self._evaluate_location_deploy(context)
        elif "choose target" in text_lower:
            # Weapon/ability target selection - MUST select, don't cancel!
            actions = self._evaluate_target_selection(context)
        elif "or click 'done' to cancel" in text_lower or "or click 'Done' to cancel" in text:
            # Target selection with cancel option (e.g., "Choose Emperor, or click 'Done' to cancel")
            # We already committed to using this ability - MUST select, don't cancel!
            # This pattern appears when an action/ability requires choosing a specific target.
            actions = self._evaluate_target_selection(context)
            logger.info(f"🎯 Treating as target selection (has 'click Done to cancel'): {len(actions)} options")
        else:
            # Unknown card selection - create neutral actions
            actions = self._evaluate_unknown(context)

        return actions

    def _evaluate_sabacc_set_value(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Sabacc value setting - pick a RANDOM card each time.

        With min=1 we must select a card, can't pass. To avoid infinite loops
        where we keep picking the same card, we randomize the scores so a
        different card is picked each time. Eventually all wild cards will
        have their values set and the server will move on.
        """
        actions = []

        for card_id in context.card_ids:
            # Randomize scores so we pick different cards each time
            # This breaks the loop by cycling through all cards
            random_score = VERY_BAD_DELTA + random.uniform(0, 10)

            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SABACC,
                score=random_score,
                display_text=f"Set sabacc value (card {card_id})"
            )
            action.add_reasoning("Sabacc value (randomized to break loops)", random_score)
            actions.append(action)

        logger.info(f"Sabacc set value - randomizing selection among {len(actions)} cards")
        return actions

    def _evaluate_sabacc_clone(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Sabacc clone - we DON'T want to clone.

        Ported from C# AICSHandler: "choose...clone" -> veryBadActionDelta
        """
        actions = []

        for card_id in context.card_ids:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SABACC,
                score=VERY_BAD_DELTA,
                display_text=f"Clone sabacc value (card {card_id})"
            )
            action.add_reasoning("Avoid cloning sabacc cards", VERY_BAD_DELTA)
            actions.append(action)

        logger.info(f"Sabacc clone - marking all {len(actions)} options as very bad")
        return actions

    def _evaluate_deploy_location_fixed(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Choose where to deploy a card - FIXED version.

        Uses bs.get_location_by_card_id() to properly look up locations.

        CRITICAL: First checks deploy_planner for planned target location!

        CRITICAL RULES:
        1. Starships should NEVER deploy to docking bays (0 power!)
        2. Starships without pilots (and no permanent pilot icon) are weak
        3. Always prefer space systems over docking bays for starships
        """
        actions = []
        bs = context.board_state
        from ..card_loader import get_card

        # Extract the card being deployed from decision text
        deploying_card = None
        deploying_card_blueprint = self._extract_blueprint_from_text(context.decision_text)
        if deploying_card_blueprint:
            deploying_card = get_card(deploying_card_blueprint)

        # =====================================================
        # CHECK DEPLOY PLANNER FOR TARGET LOCATION
        # If the plan specifies where this card should go, follow it!
        # =====================================================
        planned_target_id = None
        planned_target_name = None
        if bs and hasattr(bs, 'current_deploy_plan') and bs.current_deploy_plan and deploying_card_blueprint:
            instruction = bs.current_deploy_plan.get_instruction_for_card(deploying_card_blueprint)
            if instruction and instruction.target_location_id:
                planned_target_id = instruction.target_location_id
                planned_target_name = instruction.target_location_name
                logger.info(f"📋 Deploy plan says: {deploying_card.title if deploying_card else deploying_card_blueprint} -> {planned_target_name}")

        is_starship = deploying_card and deploying_card.is_starship
        is_vehicle = deploying_card and deploying_card.is_vehicle and not deploying_card.is_starship
        is_droid = deploying_card and deploying_card.is_droid
        provides_presence = deploying_card and deploying_card.provides_presence

        # Check if starship has permanent pilot (can fly without a pilot aboard)
        has_permanent_pilot = False
        if deploying_card and is_starship:
            icons = deploying_card.icons or []
            has_permanent_pilot = any('pilot' in str(icon).lower() for icon in icons)
            logger.debug(f"Starship {deploying_card.title}: permanent_pilot={has_permanent_pilot}, icons={icons}")

        for card_id in context.card_ids:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=50.0,  # Base score
                display_text=f"Deploy to {card_id}"
            )

            if bs:
                # Use get_location_by_card_id for proper lookup
                location = bs.get_location_by_card_id(card_id)

                if location:
                    loc_name = location.site_name or location.system_name or location.blueprint_id
                    action.display_text = f"Deploy to {loc_name}"

                    # =====================================================
                    # FOLLOW THE DEPLOY PLAN!
                    # If planner specified a target, give big bonus to it
                    # =====================================================
                    if planned_target_id:
                        if card_id == planned_target_id:
                            action.add_reasoning(f"PLANNED TARGET: {planned_target_name}", +200.0)
                            logger.info(f"✅ {loc_name} is the PLANNED target (+200)")
                        else:
                            action.add_reasoning(f"Not planned target (want {planned_target_name})", -100.0)
                            logger.debug(f"❌ {loc_name} is NOT the planned target (-100)")

                    # Determine location type
                    is_docking_bay = location.is_space and getattr(location, 'is_ground', False)
                    is_pure_space = location.is_space and not getattr(location, 'is_ground', False)
                    is_ground = not location.is_space

                    # =====================================================
                    # CRITICAL: Starships at docking bays have 0 power!
                    # =====================================================
                    if is_starship:
                        if is_docking_bay:
                            # NEVER deploy starships to docking bays
                            action.add_reasoning("STARSHIP TO DOCKING BAY = 0 POWER!", VERY_BAD_DELTA)
                            logger.warning(f"⚠️  {deploying_card.title} would have 0 power at docking bay {loc_name}!")
                        elif is_pure_space:
                            # Space system - starship has power here (if piloted)
                            action.add_reasoning("Starship to space system - has power!", GOOD_DELTA * 3)
                            # Warn if no permanent pilot - will need a pilot aboard
                            if not has_permanent_pilot:
                                logger.info(f"ℹ️  {deploying_card.title} needs pilot aboard for power in space")
                        elif is_ground:
                            # Ground location - starship can't deploy here
                            action.add_reasoning("STARSHIP TO GROUND - invalid!", VERY_BAD_DELTA)

                    # =====================================================
                    # CRITICAL: Vehicles need EXTERIOR ground locations
                    # =====================================================
                    if is_vehicle:
                        if is_pure_space:
                            # Space location - vehicles can't deploy here
                            action.add_reasoning("VEHICLE TO SPACE - invalid!", VERY_BAD_DELTA)
                        elif is_ground or is_docking_bay:
                            # Check if location has exterior icon
                            loc_metadata = get_card(location.blueprint_id) if location.blueprint_id else None
                            has_exterior = loc_metadata.is_exterior if loc_metadata else True
                            has_interior_only = loc_metadata.is_interior and not has_exterior if loc_metadata else False

                            if has_interior_only:
                                action.add_reasoning("VEHICLE TO INTERIOR-ONLY - can't deploy!", VERY_BAD_DELTA)
                                logger.warning(f"⚠️  Vehicle {deploying_card.title} cannot deploy to interior site {loc_name}")
                            elif has_exterior:
                                action.add_reasoning("Vehicle to exterior ground - good", GOOD_DELTA)

                    # =====================================================
                    # CRITICAL: Droids don't provide presence
                    # =====================================================
                    if is_droid and not provides_presence:
                        we_have_presence = self._have_presence_at_location(bs, location)
                        opponent_has_presence = len(location.their_cards) > 0

                        if not we_have_presence:
                            if opponent_has_presence:
                                action.add_reasoning("DROID ALONE vs OPPONENT - can't counter!", VERY_BAD_DELTA)
                            else:
                                action.add_reasoning("Droid alone - no presence", BAD_DELTA * 3)

                    # Add location strategic value
                    # THEIR icons matter more - controlling denies opponent force!
                    location_metadata = get_card(location.blueprint_id) if location.blueprint_id else None
                    if location_metadata:
                        my_side = bs.my_side or "light"
                        if my_side == "dark":
                            their_icons = location_metadata.light_side_icons or 0
                        else:
                            their_icons = location_metadata.dark_side_icons or 0

                        if their_icons > 0:
                            action.add_reasoning(f"Deny {their_icons} opponent icons", their_icons * GOOD_DELTA * 2)
                else:
                    # Location not found - might be deploying to a vehicle/starship!
                    # Check if card_id is a vehicle/starship in play
                    target_card = bs.cards_in_play.get(card_id)
                    if target_card:
                        target_meta = get_card(target_card.blueprint_id) if target_card.blueprint_id else None
                        is_target_vehicle = target_meta and (target_meta.is_vehicle or target_meta.is_starship) if target_meta else False

                        if is_target_vehicle:
                            target_name = target_card.card_title or card_id
                            action.display_text = f"Deploy aboard {target_name}"

                            # Check if deploying card is a pilot
                            is_pilot = deploying_card and deploying_card.is_pilot if deploying_card else False

                            if is_pilot:
                                # Pilot deploying aboard - check if vehicle needs pilot
                                # Check BOTH attached pilots AND permanent pilot (like Gold Leader In Gold 1)
                                has_pilot_aboard = bool(target_card.attached_cards)
                                has_permanent_pilot = target_meta and getattr(target_meta, 'has_permanent_pilot', False)
                                is_already_piloted = has_pilot_aboard or has_permanent_pilot

                                # Check if this is a RE-PILOT plan (plan says "aboard:card_id:ship_name")
                                is_repilot_plan = planned_target_name and planned_target_name.startswith("aboard:")
                                if is_repilot_plan:
                                    # Extract ship card_id from "aboard:card_id:ship_name" format
                                    parts = planned_target_name.split(":", 2)
                                    planned_ship_id = parts[1] if len(parts) > 1 else None
                                    planned_ship_name = parts[2] if len(parts) > 2 else "ship"

                                    if str(card_id) == str(planned_ship_id):
                                        # This IS the ship the plan wants us to pilot!
                                        action.add_reasoning(f"RE-PILOT PLAN: Deploy aboard {planned_ship_name}!", 150.0)
                                        logger.info(f"🎯 Following RE-PILOT plan: {deploying_card.title if deploying_card else 'pilot'} → {planned_ship_name}")
                                    else:
                                        # Plan says pilot a DIFFERENT ship
                                        action.add_reasoning(f"PLAN SAYS PILOT {planned_ship_name} - not this ship!", -200.0)
                                        logger.warning(f"⚠️ Plan wants pilot aboard {planned_ship_name}, not {target_name}")
                                # CRITICAL: Check if deploy plan says to go somewhere else (non-repilot plan)!
                                # If plan says "deploy to ground location X", don't pilot the ship!
                                elif planned_target_id and planned_target_id != card_id:
                                    # Plan says go elsewhere - penalize boarding ship
                                    action.add_reasoning(f"PLAN SAYS GO TO {planned_target_name} - not aboard ship!", -200.0)
                                    logger.warning(f"⚠️ Plan wants {deploying_card.title if deploying_card else 'card'} at {planned_target_name}, not aboard {target_name}")
                                elif not is_already_piloted:
                                    action.add_reasoning(f"Pilot aboard unpiloted {target_name} - GOOD!", VERY_GOOD_DELTA)
                                else:
                                    reason = "permanent pilot" if has_permanent_pilot else "pilot aboard"
                                    action.add_reasoning(f"Already piloted ({reason}) - extra pilot is marginal", -20.0)
                            else:
                                # NON-PILOT deploying aboard = PASSENGER - almost always bad!
                                # Characters as passengers waste their power and are vulnerable
                                action.add_reasoning(f"NON-PILOT AS PASSENGER - wastes character!", VERY_BAD_DELTA * 2)
                                logger.warning(f"⚠️ Penalizing {deploying_card.title if deploying_card else 'character'} as passenger on {target_name}")
                        else:
                            action.add_reasoning("Location not found in board state", -5.0)
                    else:
                        action.add_reasoning("Location not found in board state", -5.0)

            actions.append(action)

        return actions

    def _evaluate_deploy_location(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Choose where to deploy a card.

        Consider (in priority order):
        1. CRITICAL: Starships should NEVER deploy to docking bays (0 power!)
        2. Force icons - MORE ICONS = BETTER (for force generation)
        3. Force drain potential (opponent icons = we can drain there)
        4. Power differential at location
        5. Whether we already have presence
        6. Whether enemy is present
        """
        actions = []
        bs = context.board_state
        from ..card_loader import get_card

        # Extract the card being deployed from decision text
        # Format: "Choose where to deploy <div class='cardHint' value='109_8'>•Boba Fett In Slave I</div>"
        deploying_card = None
        deploying_card_blueprint = self._extract_blueprint_from_text(context.decision_text)
        if deploying_card_blueprint:
            deploying_card = get_card(deploying_card_blueprint)

        is_starship = deploying_card and deploying_card.is_starship
        is_vehicle = deploying_card and deploying_card.is_vehicle and not deploying_card.is_starship
        is_droid = deploying_card and deploying_card.is_droid
        provides_presence = deploying_card and deploying_card.provides_presence

        for card_id in context.card_ids:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=0.0,
                display_text=f"Deploy to {card_id}"
            )

            if bs:
                # Try to find location info
                card = bs.cards_in_play.get(card_id)
                if card:
                    loc_idx = card.location_index
                    if loc_idx >= 0 and loc_idx < len(bs.locations):
                        loc = bs.locations[loc_idx]
                        my_power = bs.my_power_at_location(loc_idx)
                        their_power = bs.their_power_at_location(loc_idx)

                        action.display_text = f"Deploy to {loc.site_name or loc.system_name}"

                        # =====================================================
                        # CRITICAL: Starships at docking bays have 0 power!
                        # Docking bays are is_space=True AND is_ground=True
                        # Pure space (systems/sectors) is is_space=True, is_ground=False
                        # =====================================================
                        is_docking_bay = loc.is_space and getattr(loc, 'is_ground', False)
                        is_pure_space = loc.is_space and not getattr(loc, 'is_ground', False)
                        is_ground = not loc.is_space

                        if is_starship:
                            if is_docking_bay:
                                # NEVER deploy starships to docking bays - they have 0 power!
                                action.add_reasoning("STARSHIP TO DOCKING BAY = 0 POWER!", VERY_BAD_DELTA)
                                logger.warning(f"⚠️  {deploying_card.title} would have 0 power at docking bay!")
                            elif is_pure_space:
                                # Pure space location - starship has power here
                                action.add_reasoning("Starship to space - has power", GOOD_DELTA * 2)
                            elif is_ground:
                                # Ground-only location - starship usually can't deploy here
                                action.add_reasoning("STARSHIP TO GROUND - invalid!", VERY_BAD_DELTA)

                        # =====================================================
                        # CRITICAL: Vehicles need EXTERIOR ground locations
                        # =====================================================
                        if is_vehicle:
                            if is_pure_space:
                                action.add_reasoning("VEHICLE TO SPACE - invalid!", VERY_BAD_DELTA)
                            elif is_ground or is_docking_bay:
                                loc_metadata = get_card(loc.blueprint_id) if loc.blueprint_id else None
                                has_exterior = loc_metadata.is_exterior if loc_metadata else True
                                has_interior_only = loc_metadata.is_interior and not has_exterior if loc_metadata else False

                                if has_interior_only:
                                    action.add_reasoning("VEHICLE TO INTERIOR-ONLY - can't deploy!", VERY_BAD_DELTA)
                                    logger.warning(f"⚠️  Vehicle {deploying_card.title} cannot deploy to interior site {loc.site_name}")
                                elif has_exterior:
                                    action.add_reasoning("Vehicle to exterior ground - good", GOOD_DELTA)

                        # =====================================================
                        # CRITICAL: Droids (ability=0) don't provide presence!
                        # Without presence you can't prevent force drains or initiate battles.
                        # Deploying a droid alone to "counter" an opponent is useless.
                        # =====================================================
                        if is_droid and not provides_presence:
                            # Check if we have existing presence at this location
                            we_have_presence = self._have_presence_at_location(bs, loc)
                            opponent_has_presence = len(loc.their_cards) > 0

                            if not we_have_presence:
                                if opponent_has_presence:
                                    # Opponent has presence, we don't - droid can't counter them!
                                    action.add_reasoning("DROID ALONE vs OPPONENT - can't counter!", VERY_BAD_DELTA)
                                    logger.warning(f"⚠️  {deploying_card.title} (droid) alone can't counter opponent!")
                                else:
                                    # Empty location - droid alone still can't control or prevent drains
                                    action.add_reasoning("Droid alone - no presence to control", BAD_DELTA * 3)

                        # =====================================================
                        # FORCE ICONS - Most important factor for economy!
                        # Controlling more icons = more force activation = better
                        # =====================================================
                        location_metadata = get_card(loc.blueprint_id) if loc.blueprint_id else None
                        if location_metadata:
                            # Determine which icons are "mine" based on side
                            my_side = bs.my_side or "dark"
                            if my_side == "dark":
                                my_icons = location_metadata.dark_side_icons or 0
                                their_icons = location_metadata.light_side_icons or 0
                            else:
                                my_icons = location_metadata.light_side_icons or 0
                                their_icons = location_metadata.dark_side_icons or 0

                            # BIG bonus for locations with more of my force icons
                            # Each icon = 1 force per turn = very valuable!
                            if my_icons > 0:
                                icon_bonus = my_icons * GOOD_DELTA * 2  # +20 per icon
                                action.add_reasoning(f"{my_icons} {my_side} icon(s) for activation", icon_bonus)

                            # Bonus for opponent icons (force drain potential)
                            if their_icons > 0:
                                drain_bonus = their_icons * GOOD_DELTA  # +10 per icon
                                action.add_reasoning(f"{their_icons} opponent icon(s) = drain potential", drain_bonus)

                        # Also check runtime icon data from board state
                        elif loc.my_icons or loc.their_icons:
                            try:
                                my_icon_count = int(loc.my_icons.replace("*", "").strip() or "0") if loc.my_icons else 0
                                their_icon_count = int(loc.their_icons.replace("*", "").strip() or "0") if loc.their_icons else 0

                                if my_icon_count > 0:
                                    icon_bonus = my_icon_count * GOOD_DELTA * 2
                                    action.add_reasoning(f"{my_icon_count} icon(s) for activation", icon_bonus)
                                if their_icon_count > 0:
                                    drain_bonus = their_icon_count * GOOD_DELTA
                                    action.add_reasoning(f"{their_icon_count} opponent icon(s) = drain", drain_bonus)
                            except ValueError:
                                pass

                        # =====================================================
                        # Power differential - secondary consideration
                        # =====================================================
                        if their_power > 0:
                            # Enemy present
                            power_diff = my_power - their_power
                            if power_diff >= 0:
                                action.add_reasoning(f"We match or exceed enemy power ({my_power} vs {their_power})", GOOD_DELTA * 2)
                            elif power_diff >= -6:
                                action.add_reasoning(f"Can catch up to enemy ({my_power} vs {their_power})", GOOD_DELTA)
                            else:
                                action.add_reasoning(f"Enemy too strong here ({my_power} vs {their_power})", BAD_DELTA)
                        else:
                            # Enemy not present
                            if my_power >= 12:
                                action.add_reasoning("Already have strong presence", BAD_DELTA)
                            elif my_power > 0:
                                action.add_reasoning("Bolster existing presence", GOOD_DELTA)
                            else:
                                action.add_reasoning("New location - spread out", GOOD_DELTA)
                else:
                    action.add_reasoning("Location info not found", 0.0)
            else:
                action.add_reasoning("No board state", 0.0)

            actions.append(action)

        return actions

    def _evaluate_force_loss(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Choose which card/pile to lose Force from.

        SMART FORCE LOSS STRATEGY:
        - When reserve is healthy (>= 10): prefer losing from reserve/piles
        - When reserve is low (< 10) AND hand is big (> 6): prefer losing from hand
          - First: cards that can't deploy (deploy cost > total force)
          - Second: lowest value cards (by forfeit)
          - Third: effects/interrupts (bot can't use well)
        - Always try to keep hand at 6+ cards before going back to reserve
        """
        actions = []
        bs = context.board_state
        from ..card_loader import get_card

        # Get resource levels for strategic decisions
        total_reserve = bs.total_reserve_force() if bs else 20
        hand_size = bs.hand_size if bs else 0
        total_force = total_reserve + (bs.force_pile if bs else 0)

        # Strategic thresholds
        LOW_RESERVE_THRESHOLD = 10  # Below this, start losing from hand
        MIN_HAND_SIZE = 6           # Don't go below this hand size

        # Should we prefer losing from hand?
        prefer_hand_loss = (total_reserve < LOW_RESERVE_THRESHOLD and hand_size > MIN_HAND_SIZE)

        if prefer_hand_loss:
            logger.info(f"💀 Low reserve ({total_reserve}) with hand {hand_size} - prefer losing hand cards")

        for card_id in context.card_ids:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=50.0,  # Base score
                display_text=f"Lose card {card_id}"
            )

            if bs:
                card = bs.cards_in_play.get(card_id)

                # Check if this is a placeholder for a pile (blueprint -1_X)
                blueprint = card.blueprint_id if card else ""
                if blueprint.startswith("-1_") or not card:
                    # This represents losing from a pile, not a specific card
                    action.display_text = "Lose Force"

                    if prefer_hand_loss:
                        # When low on reserve, AVOID losing more from piles
                        action.add_reasoning(f"Pile loss - reserve critical ({total_reserve})", BAD_DELTA * 3)
                    else:
                        # Normal: acceptable to lose from pile
                        action.add_reasoning("Force from pile (unknown cards)", GOOD_DELTA * 3)
                    actions.append(action)
                    continue

                if card:
                    card_title = card.card_title or card_id
                    action.display_text = f"Lose {card_title}"

                    # Get card metadata for type and deploy cost
                    card_meta = get_card(card.blueprint_id) if card.blueprint_id else None
                    card_type = card_meta.card_type if card_meta else (card.card_type or "")

                    # Check zone
                    zone = card.zone.upper() if card.zone else ""

                    if zone == "HAND":
                        # Hand cards - evaluate based on deployability and value
                        deploy_cost = card_meta.deploy_value if card_meta else 99
                        forfeit_val = card_meta.forfeit_value if card_meta else 0

                        is_deployable = False
                        if card_meta:
                            is_deployable = (
                                card_meta.is_character or
                                card_meta.is_vehicle or
                                card_meta.is_starship or
                                card_meta.is_weapon or
                                card_meta.is_location
                            )
                        else:
                            # Fallback to card_type string
                            deployable_types = ["Character", "Vehicle", "Starship", "Weapon", "Location"]
                            is_deployable = any(dt.lower() in card_type.lower() for dt in deployable_types)

                        # Check if card can actually be deployed with remaining force
                        can_afford = deploy_cost <= total_force

                        # Check if this is a priority card we should protect
                        blueprint = card.blueprint_id if card else ""
                        protection = get_protection_score(blueprint)
                        if protection == 0 and card_title:
                            protection = get_protection_score_by_title(card_title)

                        if prefer_hand_loss:
                            # LOW RESERVE MODE: Aggressively lose hand cards
                            if is_deployable and not can_afford:
                                # BEST: Can't afford to deploy anyway - lose this first!
                                action.add_reasoning(f"Can't afford (costs {deploy_cost}, have {total_force})", GOOD_DELTA * 8)
                                logger.info(f"💀 Force loss: {card_title} unaffordable ({deploy_cost}) - BEST to lose")
                            elif protection > 0:
                                # PRIORITY CARD - protect it even though it's effect/interrupt!
                                penalty = -protection * 0.8  # Strong penalty for losing priority cards
                                action.add_reasoning(f"PRIORITY CARD - protect! (score {protection})", penalty)
                                logger.info(f"🛡️ Force loss: {card_title} is PRIORITY - protecting!")
                            elif not is_deployable:
                                # Non-priority Effects/Interrupts - OK to lose
                                action.add_reasoning(f"Effect/Interrupt - bot can't use well", GOOD_DELTA * 6)
                                logger.debug(f"💀 Force loss: {card_title} ({card_type}) - good to lose")
                            elif is_deployable and can_afford:
                                # Can afford this - lose based on forfeit value
                                # Lower forfeit = more expendable
                                forfeit_bonus = (8 - forfeit_val) * 5  # 0 forfeit = +40, 8 forfeit = 0
                                action.add_reasoning(f"Deployable (forfeit {forfeit_val}) - preserve if valuable", forfeit_bonus)
                                logger.debug(f"💀 Force loss: {card_title} forfeit {forfeit_val} - score bonus {forfeit_bonus}")
                        else:
                            # NORMAL MODE: Preserve hand, lose from piles
                            if is_deployable:
                                # AVOID losing valuable deployable cards
                                action.add_reasoning(f"Deployable card in hand ({card_type})", BAD_DELTA * 4)
                                logger.debug(f"Force loss: {card_title} is deployable ({card_type}) - avoid")
                            elif protection > 0:
                                # PRIORITY CARD - protect it!
                                penalty = -protection * 0.8
                                action.add_reasoning(f"PRIORITY CARD - protect! (score {protection})", penalty)
                                logger.info(f"🛡️ Force loss: {card_title} is PRIORITY - protecting!")
                            else:
                                # Non-priority Effects/Interrupts - OK to lose
                                action.add_reasoning(f"Effect/Interrupt - bot can't use well, lose this", GOOD_DELTA * 5)
                                logger.debug(f"Force loss: {card_title} ({card_type}) - OK to lose")

                    elif zone == "RESERVE_DECK" or zone == "RESERVE":
                        if prefer_hand_loss:
                            # AVOID reserve when low
                            action.add_reasoning(f"Reserve pile - preserve (only {total_reserve} left)", BAD_DELTA * 2)
                        else:
                            action.add_reasoning("Reserve pile - unknown cards", GOOD_DELTA * 3)

                    elif zone == "USED_PILE" or zone == "USED":
                        if prefer_hand_loss:
                            action.add_reasoning("Used pile - better than reserve", GOOD_DELTA)
                        else:
                            action.add_reasoning("Used pile - already spent", GOOD_DELTA * 2)

                    elif zone == "FORCE_PILE" or zone == "FORCE":
                        if prefer_hand_loss:
                            action.add_reasoning("Force pile - bad, need for deploy", BAD_DELTA * 2)
                        else:
                            action.add_reasoning("Force pile - reduces activation", GOOD_DELTA)

                    else:
                        # Card in play - use forfeit value
                        forfeit = card.forfeit if hasattr(card, 'forfeit') else 0
                        forfeit = forfeit if isinstance(forfeit, (int, float)) else 0
                        # Prefer forfeiting low-value cards
                        forfeit_bonus = (10 - forfeit) * 2
                        action.add_reasoning(f"In play (forfeit {forfeit})", forfeit_bonus)

            actions.append(action)

        return actions

    def _evaluate_move_destination(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Choose where to move a card.

        Prefer:
        - Locations where we have power advantage
        - Locations with enemy icons (to force drain)
        Avoid:
        - Locations where enemy is much stronger
        """
        actions = []
        bs = context.board_state

        for card_id in context.card_ids:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.MOVE,
                score=0.0,
                display_text=f"Move to {card_id}"
            )

            if bs:
                # Find destination location
                card = bs.cards_in_play.get(card_id)
                if card and card.location_index >= 0:
                    loc_idx = card.location_index
                    if loc_idx < len(bs.locations):
                        loc = bs.locations[loc_idx]
                        my_power = bs.my_power_at_location(loc_idx)
                        their_power = bs.their_power_at_location(loc_idx)

                        action.display_text = f"Move to {loc.site_name or loc.system_name}"

                        if my_power >= their_power and their_power > 0:
                            action.add_reasoning("We have power advantage", GOOD_DELTA)
                        elif their_power - my_power <= 2 and their_power > 0:
                            action.add_reasoning("Can help out here", GOOD_DELTA)
                        elif their_power == 0:
                            action.add_reasoning("Unoccupied - can force drain", GOOD_DELTA)
                        else:
                            action.add_reasoning(f"Enemy too strong ({their_power} power)", BAD_DELTA * their_power)

            actions.append(action)

        return actions

    def _evaluate_forfeit(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Choose which card to forfeit in battle.

        Strategic considerations:
        1. Forfeit cards with LOWEST forfeit value first (satisfies damage more efficiently)
        2. ALWAYS forfeit pilots before their ships (when ship is forfeited, pilots are lost too)
        3. Consider attrition remaining - if we can satisfy it with a low-value card, do so
        4. Prefer forfeiting low-power cards to keep high-power ones

        From the rulebook: "Cards are forfeited from the battle to satisfy attrition
        and battle damage up to their forfeit value"
        """
        actions = []
        bs = context.board_state
        is_optional = "if desired" in context.decision_text.lower()
        from ..card_loader import get_card

        # Get attrition remaining from board state
        attrition_remaining = 0
        if bs:
            # Dark side attrition remaining is what the bot (dark) needs to satisfy
            my_side = bs.my_side or "dark"
            if my_side == "dark":
                attrition_remaining = getattr(bs, 'dark_attrition_remaining', 0)
            else:
                attrition_remaining = getattr(bs, 'light_attrition_remaining', 0)

        # First pass: collect card info for strategic decisions
        card_info = []
        for card_id in context.card_ids:
            card = bs.cards_in_play.get(card_id) if bs else None

            # Get forfeit value - try card first, then fall back to card_loader
            forfeit = 0
            power = 0
            card_title = card_id
            is_pilot_on_ship = False
            is_ship_with_pilots = False
            card_meta = None

            if card:
                card_title = card.card_title or card_id
                forfeit = getattr(card, 'forfeit', 0) or 0
                power = getattr(card, 'power', 0) or 0

                # If forfeit is 0, try loading from card_loader (metadata might not have loaded)
                if forfeit == 0 and card.blueprint_id:
                    card_meta = get_card(card.blueprint_id)
                    if card_meta:
                        forfeit = card_meta.forfeit_value or 0
                        if power == 0:
                            power = card_meta.power_value or 0

                # Check if this is a pilot attached to a ship
                if card.target_card_id:
                    is_pilot_on_ship = True

                # Check if this is a ship with pilots aboard
                if card.attached_cards:
                    is_ship_with_pilots = True

            card_info.append({
                'card_id': card_id,
                'card': card,
                'card_title': card_title,
                'forfeit': forfeit,
                'power': power,
                'is_pilot_on_ship': is_pilot_on_ship,
                'is_ship_with_pilots': is_ship_with_pilots,
                'card_meta': card_meta or (get_card(card.blueprint_id) if card and card.blueprint_id else None)
            })

        # Sort by forfeit value for logging
        sorted_by_forfeit = sorted(card_info, key=lambda x: x['forfeit'])
        logger.info(f"🎯 Forfeit options (sorted by value): {[(c['card_title'], c['forfeit']) for c in sorted_by_forfeit]}")
        if attrition_remaining > 0:
            logger.info(f"🎯 Attrition remaining to satisfy: {attrition_remaining}")

        for info in card_info:
            card_id = info['card_id']
            forfeit = info['forfeit']
            power = info['power']
            card_title = info['card_title']

            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=0.0,
                display_text=f"Forfeit {card_title}"
            )

            if is_optional:
                # Optional forfeit - avoid it
                action.score = VERY_BAD_DELTA
                action.add_reasoning("Optional forfeit - avoid", VERY_BAD_DELTA)
                actions.append(action)
                continue

            # BASE SCORE: Lower forfeit value = higher score
            # Formula: Score decreases as forfeit increases
            # forfeit=0 -> +100, forfeit=7 -> +30, forfeit=10 -> 0
            base_score = max(0, 100 - (forfeit * 10))
            action.add_reasoning(f"Forfeit value {forfeit}", base_score)

            # CRITICAL: Hit cards should ALWAYS be forfeited first!
            # They're already damaged - no reason to keep them around
            if bs and bs.is_card_hit(card_id):
                action.add_reasoning("ALREADY HIT - forfeit first!", +150.0)
                logger.info(f"🎯 {card_title} is HIT - prioritizing for forfeit")

            # BONUS: Pilots on ships should be forfeited FIRST
            # (when ship dies, pilots die too - so save the ship by forfeiting pilot)
            if info['is_pilot_on_ship']:
                action.add_reasoning("PILOT ON SHIP - forfeit first!", +50.0)

            # PENALTY: Ships with pilots should NOT be forfeited until pilots are gone
            if info['is_ship_with_pilots']:
                action.add_reasoning("Ship has pilots - forfeit pilots first!", -100.0)

            # BONUS: Low power cards are less valuable to keep
            if power <= 2:
                action.add_reasoning(f"Low power ({power}) - less valuable", +15.0)
            elif power >= 5:
                action.add_reasoning(f"High power ({power}) - try to keep", -20.0)

            # BONUS: If this card exactly or minimally covers attrition, prefer it
            if attrition_remaining > 0 and forfeit >= attrition_remaining:
                waste = forfeit - attrition_remaining
                if waste == 0:
                    action.add_reasoning(f"Exactly covers attrition ({attrition_remaining})", +30.0)
                elif waste <= 2:
                    action.add_reasoning(f"Minimally covers attrition (waste={waste})", +15.0)

            # PENALTY: Unique high-value characters should be kept if possible
            card_meta = info['card_meta']
            if card_meta and card_meta.is_unique:
                # Check if it's a major character (high ability or power)
                ability = card_meta.ability_value or 0
                if ability >= 5 or power >= 5:
                    action.add_reasoning("Valuable unique character", -25.0)

            actions.append(action)

        return actions

    def _evaluate_force_loss_or_forfeit(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Choose between losing Force from reserve deck OR forfeiting a card in battle.

        CRITICAL RULE: Attrition MUST be satisfied by forfeiting cards!
        - Battle damage can ONLY be satisfied by losing Force
        - Attrition can be satisfied by EITHER losing Force OR forfeiting cards
        - Therefore: ALWAYS forfeit cards for attrition first, THEN lose Force for battle damage

        Strategic considerations:
        1. If attrition remaining > 0: STRONGLY prefer forfeiting cards
        2. If attrition = 0 (only battle damage left): lose Force
        3. Pilots attached to ships - ALWAYS forfeit pilots before their ships
        4. Reserve deck size - if reserve is low, prefer forfeiting to save deck

        Ported from C# AICSHandler.cs battle damage assignment logic.
        """
        actions = []
        bs = context.board_state
        from ..card_loader import get_card

        # Get attrition and damage remaining
        # CRITICAL: If we have attrition remaining, we MUST forfeit cards for it
        my_attrition_remaining = 0
        my_damage_remaining = 0
        if bs:
            my_side = getattr(bs, 'my_side', 'dark').lower()
            if my_side == 'dark':
                my_attrition_remaining = getattr(bs, 'dark_attrition_remaining', 0)
                my_damage_remaining = getattr(bs, 'dark_damage_remaining', 0)
            else:
                my_attrition_remaining = getattr(bs, 'light_attrition_remaining', 0)
                my_damage_remaining = getattr(bs, 'light_damage_remaining', 0)

        logger.debug(f"Battle damage: attrition={my_attrition_remaining}, damage={my_damage_remaining}")

        # Get battle location info for power differential
        power_diff = 0
        battle_location_idx = -1
        if bs:
            # Try to find the battle location from board state
            # The bot should track which location the battle is at
            battle_location_idx = getattr(bs, 'current_battle_location', -1)
            if battle_location_idx >= 0:
                my_power = bs.my_power_at_location(battle_location_idx)
                their_power = bs.their_power_at_location(battle_location_idx)
                power_diff = my_power - their_power
                logger.debug(f"Battle at location {battle_location_idx}: my={my_power}, their={their_power}, diff={power_diff}")

        # Get reserve deck size
        reserve_size = bs.reserve_deck if bs else 30

        # CRITICAL: If attrition > 0, we MUST prefer forfeiting cards
        # Battle damage can only be satisfied by Force loss, but attrition can be forfeited
        has_attrition = my_attrition_remaining > 0

        # Separate cards into Force (from reserve) vs cards in battle vs cards from hand
        force_options = []  # Cards representing Force loss (usually -1_2 blueprint)
        battle_cards = []   # Actual cards in battle to forfeit (at battle location)
        hand_cards = []     # Cards from hand - can only be lost, NOT forfeited

        for card_id in context.card_ids:
            card = bs.cards_in_play.get(card_id) if bs else None

            # Check if this is a Force card (blueprint -1_2 or similar)
            # Force cards represent losing from reserve deck
            blueprint = card.blueprint_id if card else ""
            if blueprint.startswith("-1_") or blueprint == "":
                # This is a Force loss option (not a real card)
                force_options.append(card_id)
            elif card is None:
                # Card not in play - likely from hand
                hand_cards.append((card_id, None))
            elif battle_location_idx >= 0 and hasattr(card, 'location_index') and card.location_index == battle_location_idx:
                # Card is at the battle location - can be forfeited
                battle_cards.append((card_id, card))
            elif battle_location_idx < 0:
                # Don't know battle location - assume all cards in play are in battle
                battle_cards.append((card_id, card))
            else:
                # Card is in play but NOT at battle location - probably from hand
                # or GEMP included cards from other locations
                hand_cards.append((card_id, card))

        logger.debug(f"Force loss options: {len(force_options)}, Battle cards: {len(battle_cards)}, Hand cards: {len(hand_cards)}")

        # Identify pilots attached to ships (should be forfeited first)
        pilots_on_ships = []
        ships_with_pilots = []
        standalone_cards = []

        for card_id, card in battle_cards:
            if card:
                card_meta = get_card(card.blueprint_id)
                is_pilot = card_meta and card_meta.is_pilot if card_meta else False
                is_ship = card_meta and (card_meta.is_starship or card_meta.is_vehicle) if card_meta else False

                # Check if this card is attached to something (pilot on ship)
                if card.target_card_id:
                    pilots_on_ships.append((card_id, card, card_meta))
                elif is_ship and card.attached_cards:
                    # Ship with pilots - should forfeit pilots first
                    ships_with_pilots.append((card_id, card, card_meta))
                else:
                    standalone_cards.append((card_id, card, card_meta))
            else:
                standalone_cards.append((card_id, None, None))

        # Determine base strategy based on power differential
        # If we're badly losing, prefer forfeiting cards to save reserve deck
        prefer_forfeit = power_diff <= -5 or reserve_size <= 15

        # Score Force loss options
        for card_id in force_options:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=50.0,  # Base score
                display_text=f"Lose Force from reserve"
            )

            # CRITICAL: If attrition remaining > 0, we should NOT lose Force!
            # Attrition should be satisfied by forfeiting cards first
            if has_attrition:
                action.add_reasoning(f"ATTRITION REMAINING ({my_attrition_remaining}) - forfeit cards first!", -100.0)
            elif prefer_forfeit:
                action.add_reasoning("Badly losing battle - prefer forfeit", -30.0)
            else:
                action.add_reasoning("Force loss is acceptable (no attrition)", 0.0)

            if reserve_size <= 10:
                action.add_reasoning(f"Low reserve ({reserve_size}) - avoid force loss", -40.0)
            elif reserve_size <= 20:
                action.add_reasoning(f"Medium reserve ({reserve_size})", -10.0)

            actions.append(action)

        # Score pilots attached to ships (ALWAYS forfeit these first when on ships)
        for card_id, card, card_meta in pilots_on_ships:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=100.0,  # High base score - pilots should go first
                display_text=f"Forfeit pilot {card.card_title if card else card_id}"
            )
            action.add_reasoning("PILOT ON SHIP - forfeit first!", +50.0)

            # Still prefer low forfeit value pilots
            if card:
                forfeit = getattr(card, 'forfeit', 0) or 0
                forfeit_bonus = 20 - forfeit
                action.add_reasoning(f"Forfeit value {forfeit}", forfeit_bonus)

            actions.append(action)

        # Score ships with pilots (should NOT be forfeited until pilots are gone)
        for card_id, card, card_meta in ships_with_pilots:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=-50.0,  # Low score - don't forfeit ship while pilots attached
                display_text=f"Forfeit ship {card.card_title if card else card_id}"
            )
            action.add_reasoning("Ship has pilots - forfeit pilots first!", -100.0)
            actions.append(action)

        # Score standalone cards (characters, unpiloted ships, etc.)
        for card_id, card, card_meta in standalone_cards:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=40.0,  # Base score
                display_text=f"Forfeit {card.card_title if card else card_id}"
            )

            # CRITICAL: If attrition remaining > 0, boost forfeit score
            if has_attrition:
                action.add_reasoning(f"ATTRITION REMAINING ({my_attrition_remaining}) - forfeit to satisfy!", +60.0)
            elif prefer_forfeit:
                action.add_reasoning("Badly losing - forfeit preferred", +20.0)

            if card:
                forfeit = getattr(card, 'forfeit', 0) or 0
                # Prefer low forfeit value cards
                forfeit_bonus = (10 - forfeit) * 2
                action.add_reasoning(f"Forfeit value {forfeit}", forfeit_bonus)

                # CRITICAL: Hit cards should ALWAYS be forfeited first!
                if bs.is_card_hit(card_id):
                    action.add_reasoning("ALREADY HIT - forfeit first!", +150.0)
                    card_title = card.card_title or card_id
                    logger.info(f"🎯 {card_title} is HIT - prioritizing for forfeit")

                # Penalize forfeiting high-value cards (unique characters, ships)
                if card_meta:
                    if card_meta.is_starship or card_meta.is_vehicle:
                        if not prefer_forfeit:
                            action.add_reasoning("Ship/vehicle - prefer force loss", -15.0)
                    if card_meta.is_unique:
                        action.add_reasoning("Unique card - valuable", -10.0)

            actions.append(action)

        # Score hand cards - HEAVILY penalize losing cards from hand
        # Hand cards can only be "lost" (worth 1 force each), not "forfeited" (worth forfeit value)
        # Should almost always prefer forfeiting battle cards or losing force instead
        for card_id, card in hand_cards:
            card_title = card.card_title if card else card_id
            card_meta = get_card(card.blueprint_id) if card and card.blueprint_id else None

            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=-50.0,  # Very low base score - hand cards should be last resort
                display_text=f"Lose {card_title} from hand"
            )

            action.add_reasoning("FROM HAND - can only lose, not forfeit!", -50.0)

            # Check if this is a priority card
            blueprint = card.blueprint_id if card else ""
            protection = get_protection_score(blueprint) if blueprint else 0

            if protection > 0:
                # PRIORITY CARD - heavily penalize losing from hand
                penalty = -protection * 1.0  # Full penalty for losing priority cards
                action.add_reasoning(f"PRIORITY CARD - protect! (score {protection})", penalty)
                logger.info(f"🛡️ Battle loss: {card_title} is PRIORITY - protecting!")
            elif card_meta:
                card_type = card_meta.card_type or ""
                if card_type.lower() in ['effect', 'interrupt', 'used interrupt', 'lost interrupt']:
                    # Non-priority effects/interrupts - less bad to lose
                    action.add_reasoning(f"{card_type} - less valuable in hand", +30.0)
                elif card_meta.is_unique:
                    action.add_reasoning("Unique card - very bad to lose!", -30.0)

            actions.append(action)

        return actions

    def _evaluate_optional_action(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Optional action with "if desired" - usually want to pass.
        """
        actions = []

        for card_id in context.card_ids:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=VERY_BAD_DELTA,
                display_text=f"Optional action (card {card_id})"
            )
            action.add_reasoning("Optional action - prefer to pass", VERY_BAD_DELTA)
            actions.append(action)

        return actions

    def _evaluate_pilot_selection(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Choose a pilot to deploy aboard a ship.
        """
        actions = []
        bs = context.board_state

        for card_id in context.card_ids:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.DEPLOY,
                score=VERY_GOOD_DELTA,
                display_text=f"Deploy pilot {card_id}"
            )

            if bs:
                card = bs.cards_in_play.get(card_id)
                if card:
                    action.display_text = f"Deploy pilot {card.card_title or card_id}"
                    action.add_reasoning("Pilot deployment is good", VERY_GOOD_DELTA)

            actions.append(action)

        return actions

    def _evaluate_cancel_selection(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Choose which card to cancel.

        Cancel opponent's cards, not ours.
        """
        actions = []
        bs = context.board_state
        my_name = bs.my_player_name if bs else "rando_cal"

        for card_id in context.card_ids:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.CANCEL,
                score=0.0,
                display_text=f"Cancel card {card_id}"
            )

            if bs:
                card = bs.cards_in_play.get(card_id)
                if card:
                    if card.owner == my_name:
                        action.add_reasoning("Don't cancel own cards", BAD_DELTA)
                    else:
                        action.add_reasoning("Cancel opponent's cards", GOOD_DELTA)
                    action.display_text = f"Cancel {card.card_title or card_id}"

            actions.append(action)

        return actions

    def _evaluate_hand_selection(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Choose a card from hand - usually for game effects.
        """
        actions = []

        for card_id in context.card_ids:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=GOOD_DELTA,
                display_text=f"Select card {card_id} from hand"
            )
            action.add_reasoning("Selecting card from hand", GOOD_DELTA)
            actions.append(action)

        return actions

    def _evaluate_location_deploy(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Choose a location to deploy - for special deployment effects.
        """
        actions = []

        for card_id in context.card_ids:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=GOOD_DELTA,
                display_text=f"Deploy to location {card_id}"
            )
            action.add_reasoning("Location deployment", GOOD_DELTA)
            actions.append(action)

        return actions

    def _evaluate_target_selection(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Choose a target for a weapon or ability, OR select a defensive shield.

        IMPORTANT: Once we've decided to use an ability, we MUST select a target.
        Canceling wastes the action and can cause game flow issues.

        For defensive shields (from Starting Effect):
        - Use shield_strategy scoring to pick the best shield
        - Shields aren't in cards_in_play, so we use context.blueprints

        For weapon/ability targets:
        - Enemy cards (always target enemies!)
        - High-value targets (more power/forfeit = more damage to opponent)
        - Characters over droids/vehicles (usually more valuable)
        """
        actions = []
        bs = context.board_state
        my_name = bs.my_player_name if bs else "rando_cal"
        from ..card_loader import get_card

        logger.info(f"Target selection: {len(context.card_ids)} potential targets")

        # =============================================================
        # DEFENSIVE SHIELD DETECTION
        # When selecting shields from Starting Effect, cards aren't in
        # cards_in_play. Check if we have blueprints and they're shields.
        # =============================================================
        is_shield_selection = False
        shield_count = 0

        # Debug: Log what blueprints we have
        non_empty_blueprints = [b for b in context.blueprints if b and b != "inPlay" and not b.startswith("-1_")]
        if non_empty_blueprints:
            logger.info(f"🔍 Have {len(non_empty_blueprints)} non-empty blueprints for target selection")

        # Check if context.blueprints contains defensive shields
        if context.blueprints and len(context.blueprints) > 0:
            for i, blueprint in enumerate(context.blueprints):
                if blueprint and blueprint != "inPlay" and not blueprint.startswith("-1_"):
                    card_meta = get_card(blueprint)
                    if card_meta and card_meta.is_defensive_shield:
                        shield_count += 1

            # If majority of options are shields, treat as shield selection
            if shield_count > 0 and shield_count >= len(context.blueprints) * 0.5:
                is_shield_selection = True
                logger.info(f"🛡️ Detected DEFENSIVE SHIELD selection: {shield_count}/{len(context.blueprints)} shields")

        # Get turn number and side for shield scoring
        turn_number = context.turn_number if context.turn_number else 1
        my_side = bs.my_side if bs else "light"

        for i, card_id in enumerate(context.card_ids):
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=100.0,  # High base score - we WANT to select a target
                display_text=f"Target {card_id}"
            )

            # Get blueprint for this card (if available)
            blueprint = context.blueprints[i] if i < len(context.blueprints) else None

            # =============================================================
            # SHIELD SELECTION PATH
            # Use shield_strategy scoring for defensive shields
            # =============================================================
            if is_shield_selection and blueprint and blueprint != "inPlay" and not blueprint.startswith("-1_"):
                card_meta = get_card(blueprint)
                if card_meta and card_meta.is_defensive_shield:
                    action.display_text = f"Shield: {card_meta.title}"
                    action.card_name = card_meta.title
                    action.blueprint_id = blueprint

                    # Use comprehensive shield strategy scoring
                    # Set score directly (don't add to base) so shield strategy fully controls priority
                    shield_score, shield_reason = score_shield_for_deployment(
                        blueprint, card_meta.title, turn_number, my_side, bs
                    )
                    action.score = shield_score  # Override base score entirely
                    action.add_reasoning(f"Shield: {shield_reason}")  # Log reason without adding to score
                    logger.debug(f"🛡️ {card_meta.title}: score={shield_score:.0f} ({shield_reason})")
                    actions.append(action)
                    continue  # Skip generic target evaluation

            # =============================================================
            # STANDARD TARGET SELECTION PATH
            # For weapons, abilities, etc.
            # =============================================================
            if bs:
                card = bs.cards_in_play.get(card_id)
                if card:
                    action.display_text = f"Target {card.card_title or card_id}"

                    # CRITICAL: Always prefer enemy targets
                    if card.owner != my_name:
                        action.add_reasoning("ENEMY target - good!", +50.0)

                        # Check if this card has already been hit this battle
                        # Hit cards shouldn't be targeted again - it's wasteful
                        if bs.is_card_hit(card_id):
                            action.add_reasoning("ALREADY HIT - don't waste fire!", -500.0)
                            actions.append(action)
                            continue  # Skip further evaluation

                        # Prefer high-value enemy cards
                        card_meta = get_card(card.blueprint_id) if card.blueprint_id else None
                        if card_meta:
                            # Target high power cards
                            power = card_meta.power_value or 0
                            if power >= 5:
                                action.add_reasoning(f"High power ({power})", +20.0)
                            elif power >= 3:
                                action.add_reasoning(f"Medium power ({power})", +10.0)

                            # Target unique characters (more valuable)
                            if card_meta.is_unique:
                                action.add_reasoning("Unique card - valuable target", +15.0)

                            # Target characters over other card types
                            if card_meta.is_character:
                                action.add_reasoning("Character target", +5.0)
                            elif card_meta.is_starship:
                                action.add_reasoning("Starship target", +3.0)

                            # =================================================
                            # BLASTER DEFLECTION AWARENESS (25% of LS decks)
                            # When firing blasters at Light Side opponents,
                            # avoid targeting characters with ability > 4.
                            # Blaster Deflection can cancel or redirect!
                            # =================================================
                            decision_text_lower = (context.decision_text or "").lower()
                            is_blaster = "blaster" in decision_text_lower
                            opponent_is_ls = bs.my_side == "dark"  # We're dark = they're light

                            if is_blaster and opponent_is_ls and card_meta.is_character:
                                target_ability = card_meta.ability_value or 0
                                if target_ability > 4:
                                    # High ability - Blaster Deflection can cancel!
                                    action.add_reasoning(
                                        f"BLASTER DEFLECTION RISK: ability {target_ability} > 4",
                                        -30.0
                                    )
                                    logger.debug(
                                        f"⚡ Blaster Deflection risk: {card.card_title} "
                                        f"has ability {target_ability}"
                                    )
                    else:
                        # Our own card - DON'T target if we have enemy options
                        action.add_reasoning("OUR card - avoid targeting!", -200.0)
                else:
                    # Card not in our tracking - check if we have a blueprint
                    # This handles shields from Starting Effect that weren't detected above
                    if blueprint and blueprint != "inPlay" and not blueprint.startswith("-1_"):
                        card_meta = get_card(blueprint)
                        if card_meta:
                            action.display_text = f"Select {card_meta.title}"
                            action.card_name = card_meta.title
                            action.blueprint_id = blueprint

                            # Check if this is a defensive shield
                            if card_meta.is_defensive_shield:
                                shield_score, shield_reason = score_shield_for_deployment(
                                    blueprint, card_meta.title, turn_number, my_side, bs
                                )
                                action.score = shield_score  # Override base score entirely
                                action.add_reasoning(f"Shield: {shield_reason}")  # Log reason without adding
                                logger.info(f"🛡️ Fallback shield scoring: {card_meta.title} = {shield_score:.0f}")
                            else:
                                action.add_reasoning(f"Card from blueprint: {card_meta.title}", +10.0)
                        else:
                            action.add_reasoning("Unknown target - proceed", +10.0)
                    else:
                        action.add_reasoning("Unknown target - proceed", +10.0)
            else:
                action.add_reasoning("No board state - select anyway", +10.0)

            actions.append(action)

        return actions

    def _evaluate_unknown(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Unknown card selection - default to selecting cards when allowed.

        Key principle: If min=0 but max>0, we CAN select and usually SHOULD.
        Passing should be the exception (handled by specific evaluators), not default.
        Give cards a base score that beats PassEvaluator (~5-20) to prefer action.

        For ARBITRARY_CARDS: add randomization and type-based preferences.
        """
        actions = []
        from ..card_loader import get_card

        is_arbitrary = context.decision_type == 'ARBITRARY_CARDS'
        log_level = logger.info if is_arbitrary else logger.warning
        log_level(f"Unknown {context.decision_type}: '{context.decision_text}'")

        # Check if we can select cards (max > 0)
        max_select = context.extra.get('max', 1)  # Default to 1 if not specified
        min_select = context.extra.get('min', 0)

        # Determine base score:
        # - If max > 0: we can select, so give positive score to beat PassEvaluator
        # - If max = 0: we can't select anything, neutral score
        if max_select > 0:
            base_score = 30.0  # Beats PassEvaluator's ~5-20 score
            reason = f"Unknown selection (min={min_select}, max={max_select})"
        else:
            base_score = 0.0
            reason = "Unknown selection with max=0 - can't select"

        # Check if this looks like a "lose" or "place" decision (for type preferences)
        text_lower = context.decision_text.lower()
        is_loss_decision = any(x in text_lower for x in ["lose", "lost", "place in", "put on"])

        for i, card_id in enumerate(context.card_ids):
            blueprint = context.blueprints[i] if i < len(context.blueprints) else ""

            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.UNKNOWN,
                score=base_score,
                display_text=f"Select card {card_id}"
            )
            action.add_reasoning(reason, 0.0)

            # For ARBITRARY_CARDS: add randomization and type preferences
            if is_arbitrary:
                # Add randomization to avoid predictable patterns
                random_factor = random.uniform(-10, 15)
                action.add_reasoning("Random factor", random_factor)

                # Apply type-based preferences if we have metadata
                if blueprint and not blueprint.startswith("-1_"):
                    card_meta = get_card(blueprint)
                    if card_meta:
                        action.display_text = f"Select {card_meta.title}"
                        card_type = (card_meta.card_type or "").lower()

                        if is_loss_decision:
                            # For loss decisions: prefer effects/interrupts
                            if "effect" in card_type or "interrupt" in card_type:
                                action.add_reasoning("Effect/Interrupt - OK to lose", 25.0)
                            elif card_meta.is_character:
                                action.add_reasoning("Character - avoid losing", -15.0)
                            elif card_meta.is_starship:
                                action.add_reasoning("Starship - avoid losing", -15.0)
                            elif card_meta.is_vehicle:
                                action.add_reasoning("Vehicle - avoid losing", -10.0)
                            elif card_meta.is_location:
                                action.add_reasoning("Location - avoid losing", -20.0)
                        else:
                            # For gain/select decisions: prefer deployables
                            if card_meta.is_character:
                                action.add_reasoning("Character - valuable", 10.0)
                            elif card_meta.is_starship:
                                action.add_reasoning("Starship - valuable", 8.0)
                            elif card_meta.is_location:
                                action.add_reasoning("Location - valuable", 10.0)

            actions.append(action)

        return actions

    # =====================================================
    # ARBITRARY_CARDS handlers
    # =====================================================

    def _evaluate_starting_deploy(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Choose card to deploy during starting cards phase.

        This is called when the objective requires deploying specific cards
        (e.g., "Hunt For The Droid General" requires Clone Command Center,
        Cloning Cylinders, Grievous Will Run And Hide, etc.)

        Uses the objective handler to identify which cards should be prioritized.
        """
        actions = []
        from ..card_loader import get_card
        from ..objective_handler import get_objective_handler

        objective_handler = get_objective_handler()

        logger.info(f"🎯 Starting card deploy selection, {len(context.card_ids)} options")

        for i, card_id in enumerate(context.card_ids):
            blueprint = context.blueprints[i] if i < len(context.blueprints) else ""

            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=50.0,  # Base score
                display_text=f"Deploy {card_id}"
            )

            if blueprint and not blueprint.startswith("-1_"):
                card_meta = get_card(blueprint)
                if card_meta:
                    card_title = card_meta.title or ""
                    action.display_text = f"Deploy {card_title}"

                    # Check if this card is required by the objective
                    obj_bonus = objective_handler.score_starting_card(blueprint, card_title)
                    if obj_bonus > 0:
                        action.add_reasoning(f"OBJECTIVE REQUIREMENT", obj_bonus)
                        logger.info(f"🎯 {card_title} is required by objective! (+{obj_bonus})")
                    else:
                        # Not a required card - lower priority during starting phase
                        action.add_reasoning("Not objective requirement", -20.0)

                    # Additional scoring based on card type
                    if card_meta.is_location:
                        # Locations are often needed for objectives
                        action.add_reasoning("Location - may be needed", 20.0)

                        # Check for battleground icons
                        is_battleground = "battleground" in (card_meta.sub_type or "").lower()
                        if is_battleground:
                            action.add_reasoning("Battleground location", 30.0)
                    elif card_meta.is_effect:
                        # Effects like Cloning Cylinders are often objective requirements
                        action.add_reasoning("Effect - may be objective card", 15.0)
            else:
                # Unknown card - add randomization
                action.add_reasoning("Unknown card", random.uniform(-5, 5))

            actions.append(action)

        return actions

    def _evaluate_starting_location(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Choose starting location - pick location with most force icons for our side.

        Starting locations set up our force generation for the entire game,
        so we want locations with the most icons for our side (Dark or Light).
        """
        actions = []
        bs = context.board_state
        from ..card_loader import get_card

        my_side = (bs.my_side or "dark").lower() if bs else "dark"
        logger.info(f"🏠 Starting location selection for {my_side} side, {len(context.card_ids)} options")

        for i, card_id in enumerate(context.card_ids):
            blueprint = context.blueprints[i] if i < len(context.blueprints) else ""

            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=50.0,  # Base score
                display_text=f"Start at {card_id}"
            )

            # Look up location metadata for force icons
            if blueprint and not blueprint.startswith("-1_"):
                card_meta = get_card(blueprint)
                if card_meta:
                    action.display_text = f"Start at {card_meta.title}"

                    # Get icons for our side
                    if my_side == "dark":
                        my_icons = card_meta.dark_side_icons or 0
                        their_icons = card_meta.light_side_icons or 0
                    else:
                        my_icons = card_meta.light_side_icons or 0
                        their_icons = card_meta.dark_side_icons or 0

                    # Strong bonus for our icons (force generation)
                    if my_icons > 0:
                        icon_bonus = my_icons * 30.0  # +30 per icon
                        action.add_reasoning(f"{my_icons} {my_side} icon(s) for activation", icon_bonus)

                    # Small bonus for opponent icons (drain potential)
                    if their_icons > 0:
                        drain_bonus = their_icons * 10.0
                        action.add_reasoning(f"{their_icons} opponent icon(s) = drain potential", drain_bonus)

                    logger.debug(f"  {card_meta.title}: {my_icons} my icons, {their_icons} their icons")
                else:
                    # Add small random factor if no metadata
                    action.add_reasoning("Unknown location", random.uniform(0, 10))
            else:
                action.add_reasoning("Unknown location", random.uniform(0, 10))

            actions.append(action)

        return actions

    def _evaluate_take_into_hand(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Choose card to take into hand - from search/draw effects.

        For ARBITRARY_CARDS "take into hand" decisions, we generally want:
        1. High-value deployable cards (characters, starships)
        2. Cards we can actually afford to deploy
        3. Some randomization since we can't perfectly evaluate all cards

        Since we don't always know what's best, add randomization to avoid
        predictable patterns.
        """
        actions = []
        bs = context.board_state
        from ..card_loader import get_card

        total_force = (bs.force_pile + bs.total_reserve_force()) if bs else 20

        logger.info(f"🃏 Take into hand selection, {len(context.card_ids)} options")

        for i, card_id in enumerate(context.card_ids):
            blueprint = context.blueprints[i] if i < len(context.blueprints) else ""

            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=50.0,  # Base score
                display_text=f"Take {card_id}"
            )

            # Add randomization to avoid predictable patterns
            random_factor = random.uniform(-10, 10)
            action.add_reasoning("Random factor", random_factor)

            if blueprint and not blueprint.startswith("-1_"):
                card_meta = get_card(blueprint)
                if card_meta:
                    action.display_text = f"Take {card_meta.title}"

                    # Prefer deployable cards
                    if card_meta.is_character:
                        action.add_reasoning("Character - deployable", 15.0)
                        # Bonus for high power
                        power = card_meta.power_value or 0
                        if power >= 5:
                            action.add_reasoning(f"High power ({power})", 10.0)
                    elif card_meta.is_starship:
                        action.add_reasoning("Starship - deployable", 12.0)
                    elif card_meta.is_vehicle:
                        action.add_reasoning("Vehicle - deployable", 10.0)
                    elif card_meta.is_weapon:
                        action.add_reasoning("Weapon - deployable", 8.0)
                    elif card_meta.is_location:
                        action.add_reasoning("Location - opens options", 15.0)

                    # Slight penalty for cards we can't afford
                    deploy_cost = card_meta.deploy_value or 0
                    if deploy_cost > total_force:
                        action.add_reasoning(f"Can't afford (costs {deploy_cost})", -5.0)

            actions.append(action)

        return actions

    def _evaluate_lost_pile_selection(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Choose card to put on Lost Pile - for battle damage.

        Similar to forfeit logic: prefer low-value cards.
        Prefer effects/interrupts over deployable cards.
        """
        actions = []
        bs = context.board_state
        from ..card_loader import get_card

        logger.info(f"💀 Lost Pile selection (battle damage), {len(context.card_ids)} options")

        for i, card_id in enumerate(context.card_ids):
            blueprint = context.blueprints[i] if i < len(context.blueprints) else ""

            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=50.0,  # Base score
                display_text=f"Lose {card_id}"
            )

            if blueprint and not blueprint.startswith("-1_"):
                card_meta = get_card(blueprint)
                if card_meta:
                    action.display_text = f"Lose {card_meta.title}"
                    card_type = (card_meta.card_type or "").lower()

                    # Check if this is a priority card
                    protection = get_protection_score(blueprint)

                    if protection > 0:
                        # PRIORITY CARD - don't lose it!
                        penalty = -protection * 0.8
                        action.add_reasoning(f"PRIORITY CARD - protect! (score {protection})", penalty)
                        logger.info(f"🛡️ Lost pile: {card_meta.title} is PRIORITY - protecting!")
                    elif "effect" in card_type or "interrupt" in card_type:
                        # Non-priority effects/interrupts - OK to lose
                        action.add_reasoning(f"{card_meta.card_type} - OK to lose", 40.0)
                    # AVOID losing deployable cards
                    elif card_meta.is_character:
                        forfeit = card_meta.forfeit_value or 0
                        power = card_meta.power_value or 0
                        # Penalty based on value
                        penalty = -10.0 - (power * 3) - (forfeit * 2)
                        action.add_reasoning(f"Character (power {power}, forfeit {forfeit})", penalty)
                    elif card_meta.is_starship:
                        action.add_reasoning("Starship - avoid losing", -25.0)
                    elif card_meta.is_vehicle:
                        action.add_reasoning("Vehicle - avoid losing", -20.0)
                    elif card_meta.is_weapon:
                        action.add_reasoning("Weapon - slight avoid", -10.0)
                    elif card_meta.is_location:
                        action.add_reasoning("Location - avoid losing", -30.0)
                    else:
                        # Unknown type - neutral with randomization
                        action.add_reasoning("Unknown type", random.uniform(-5, 5))
            else:
                # Hidden/unknown card - add randomization
                action.add_reasoning("Unknown card", random.uniform(0, 20))

            actions.append(action)

        return actions

    def _evaluate_play_from_lost_pile(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Choose Interrupt to play from Lost Pile - recursion effect.

        For effects like Sense that let us play interrupts from Lost Pile,
        randomization is fine since we don't have deep interrupt evaluation.
        """
        actions = []
        from ..card_loader import get_card

        logger.info(f"♻️ Play from Lost Pile selection, {len(context.card_ids)} options")

        for i, card_id in enumerate(context.card_ids):
            blueprint = context.blueprints[i] if i < len(context.blueprints) else ""

            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=50.0,  # Base score
                display_text=f"Play {card_id}"
            )

            # Add randomization
            random_factor = random.uniform(-5, 15)
            action.add_reasoning("Random selection", random_factor)

            if blueprint and not blueprint.startswith("-1_"):
                card_meta = get_card(blueprint)
                if card_meta:
                    action.display_text = f"Play {card_meta.title}"
                    # Small bonus just for having metadata
                    action.add_reasoning("Known card", 5.0)

            actions.append(action)

        return actions

    def _evaluate_pile_placement(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Choose card to place in a pile - generic pile placement.

        When we need to lose/place cards, prefer effects/interrupts
        over deployable cards (characters, vehicles, starships, weapons).
        """
        actions = []
        from ..card_loader import get_card

        logger.info(f"📚 Pile placement selection, {len(context.card_ids)} options")

        for i, card_id in enumerate(context.card_ids):
            blueprint = context.blueprints[i] if i < len(context.blueprints) else ""

            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=50.0,  # Base score
                display_text=f"Place {card_id}"
            )

            if blueprint and not blueprint.startswith("-1_"):
                card_meta = get_card(blueprint)
                if card_meta:
                    action.display_text = f"Place {card_meta.title}"
                    card_type = (card_meta.card_type or "").lower()

                    # Check if this is a priority card
                    protection = get_protection_score(blueprint)

                    if protection > 0:
                        # PRIORITY CARD - don't place it!
                        penalty = -protection * 0.6  # Slightly less severe than losing
                        action.add_reasoning(f"PRIORITY CARD - protect! (score {protection})", penalty)
                        logger.info(f"🛡️ Pile placement: {card_meta.title} is PRIORITY - protecting!")
                    elif "effect" in card_type or "interrupt" in card_type:
                        # Non-priority effects/interrupts - OK to place
                        action.add_reasoning(f"{card_meta.card_type} - OK to place", 30.0)
                    # AVOID placing deployable cards
                    elif card_meta.is_character:
                        action.add_reasoning("Character - avoid placing", -20.0)
                    elif card_meta.is_starship:
                        action.add_reasoning("Starship - avoid placing", -20.0)
                    elif card_meta.is_vehicle:
                        action.add_reasoning("Vehicle - avoid placing", -15.0)
                    elif card_meta.is_weapon:
                        action.add_reasoning("Weapon - slight avoid", -10.0)
                    elif card_meta.is_location:
                        action.add_reasoning("Location - avoid placing", -25.0)
                    else:
                        action.add_reasoning("Unknown type", random.uniform(-5, 10))
            else:
                action.add_reasoning("Unknown card", random.uniform(0, 15))

            actions.append(action)

        return actions

    def _extract_blueprint_from_text(self, text: str) -> str:
        """
        Extract blueprint ID from decision text HTML.

        Example: "Choose where to deploy <div class='cardHint' value='109_8'>•Boba Fett In Slave I</div>"
        Returns: "109_8"
        """
        match = re.search(r"value='([^']+)'", text)
        if match:
            return match.group(1)
        return ""

    def _have_presence_at_location(self, board_state, location) -> bool:
        """
        Check if we have 'presence' at a location.

        In SWCCG, presence requires a character with ability > 0.
        Droids (ability = 0) do NOT provide presence on their own.
        Without presence you cannot:
        - Prevent opponent's force drains
        - Initiate battles
        - Control the location

        Args:
            board_state: Current board state
            location: The location to check

        Returns:
            True if we have at least one character with ability > 0 there
        """
        from ..card_loader import get_card

        for card in location.my_cards:
            card_meta = get_card(card.blueprint_id)
            if card_meta and card_meta.provides_presence:
                return True
        return False
