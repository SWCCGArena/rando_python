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
        """Handle CARD_SELECTION decisions"""
        return context.decision_type == 'CARD_SELECTION'

    def evaluate(self, context: DecisionContext) -> List[EvaluatedAction]:
        """Evaluate card selection options based on decision text"""
        actions = []
        bs = context.board_state
        text = context.decision_text
        text_lower = text.lower()

        # Get all card IDs from the decision
        card_ids = context.card_ids

        if not card_ids:
            logger.warning("No card IDs in CARD_SELECTION decision")
            return []

        # Determine the type of card selection
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
                    # Location not found - use neutral score
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

        Priority order (BEST to WORST):
        1. Hand (Effects/Interrupts) - BEST! Known cards the bot can't use well
        2. Reserve pile - unknown cards, might lose something useful
        3. Used pile - already spent
        4. Force pile - reduces activation potential
        5. Hand (Characters/Vehicles/Starships/Weapons/Locations) - AVOID, these are deployable

        Key insight: Effects/Interrupts in hand are KNOWN useless cards for the bot,
        while reserve pile cards are unknown and might contain characters/weapons we could use.
        """
        actions = []
        bs = context.board_state
        from ..card_loader import get_card

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
                    # Acceptable but not as good as losing known useless cards
                    action.display_text = "Lose Force"
                    action.add_reasoning("Force from pile (unknown cards)", GOOD_DELTA * 3)
                    actions.append(action)
                    continue

                if card:
                    card_title = card.card_title or card_id
                    action.display_text = f"Lose {card_title}"

                    # Get card metadata for type checking
                    card_meta = get_card(card.blueprint_id) if card.blueprint_id else None
                    card_type = card_meta.card_type if card_meta else (card.card_type or "")

                    # Check zone
                    zone = card.zone.upper() if card.zone else ""

                    if zone == "HAND":
                        # Hand cards - depends on card type
                        # BEST to lose: Effects, Interrupts (bot can't use these well)
                        # KEEP: Characters, Vehicles, Starships, Weapons, Locations (deployable)

                        is_valuable = False
                        if card_meta:
                            is_valuable = (
                                card_meta.is_character or
                                card_meta.is_vehicle or
                                card_meta.is_starship or
                                card_meta.is_weapon or
                                card_meta.is_location
                            )
                        else:
                            # Fallback to card_type string
                            valuable_types = ["Character", "Vehicle", "Starship", "Weapon", "Location"]
                            is_valuable = any(vt.lower() in card_type.lower() for vt in valuable_types)

                        if is_valuable:
                            # AVOID losing valuable deployable cards from hand
                            action.add_reasoning(f"Deployable card in hand ({card_type})", BAD_DELTA * 4)
                            logger.debug(f"Force loss: {card_title} is deployable ({card_type}) - avoid")
                        else:
                            # Effects/Interrupts - BEST to lose! Bot can't use these well
                            action.add_reasoning(f"Effect/Interrupt - bot can't use, lose this!", GOOD_DELTA * 5)
                            logger.debug(f"Force loss: {card_title} ({card_type}) - BEST to lose")

                    elif zone == "RESERVE_DECK" or zone == "RESERVE":
                        # Reserve pile - unknown cards, might lose something useful
                        action.add_reasoning("Reserve pile - unknown cards", GOOD_DELTA * 3)

                    elif zone == "USED_PILE" or zone == "USED":
                        # Used pile - already spent, acceptable
                        action.add_reasoning("Used pile - already spent", GOOD_DELTA * 2)

                    elif zone == "FORCE_PILE" or zone == "FORCE":
                        # Force pile - reduces activation potential
                        action.add_reasoning("Force pile - reduces activation", GOOD_DELTA)

                    else:
                        # Card in play - use forfeit value
                        forfeit = card.forfeit if hasattr(card, 'forfeit') else 0
                        forfeit = forfeit if isinstance(forfeit, (int, float)) else 0
                        # Prefer forfeiting low-value cards
                        forfeit_bonus = (10 - forfeit) * 2
                        action.add_reasoning(f"In play (forfeit {forfeit})", forfeit_bonus)

                # Adjust based on overall life force
                total_reserve = bs.total_reserve_force()
                if total_reserve < 10:
                    action.add_reasoning("Low life - be careful", BAD_DELTA)

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

        Strategic considerations:
        1. Power differential at battle location - if we're badly losing (5+ power behind),
           prefer to forfeit cards since they'll just get beat up again next turn
        2. Reserve deck size - if reserve is low, prefer forfeiting to save deck
        3. Pilots attached to ships - ALWAYS forfeit pilots before their ships
        4. Card value - prefer losing low-value force over forfeiting high-value cards

        Ported from C# AICSHandler.cs battle damage assignment logic.
        """
        actions = []
        bs = context.board_state
        from ..card_loader import get_card

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

            # Adjust based on strategic situation
            if prefer_forfeit:
                action.add_reasoning("Badly losing battle - prefer forfeit", -30.0)
            else:
                action.add_reasoning("Force loss is acceptable", 0.0)

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

            if prefer_forfeit:
                action.add_reasoning("Badly losing - forfeit preferred", +20.0)

            if card:
                forfeit = getattr(card, 'forfeit', 0) or 0
                # Prefer low forfeit value cards
                forfeit_bonus = (10 - forfeit) * 2
                action.add_reasoning(f"Forfeit value {forfeit}", forfeit_bonus)

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

            # Effects and interrupts from hand are slightly less bad to lose
            # since they can't be used anyway if we're losing the battle
            if card_meta:
                card_type = card_meta.card_type or ""
                if card_type.lower() in ['effect', 'interrupt', 'used interrupt', 'lost interrupt']:
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
        Choose a target for a weapon or ability.

        IMPORTANT: Once we've decided to use an ability, we MUST select a target.
        Canceling wastes the action and can cause game flow issues.

        Prefer:
        - Enemy cards (always target enemies!)
        - High-value targets (more power/forfeit = more damage to opponent)
        - Characters over droids/vehicles (usually more valuable)
        """
        actions = []
        bs = context.board_state
        my_name = bs.my_player_name if bs else "rando_cal"
        from ..card_loader import get_card

        logger.info(f"Target selection: {len(context.card_ids)} potential targets")

        for card_id in context.card_ids:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=100.0,  # High base score - we WANT to select a target
                display_text=f"Target {card_id}"
            )

            if bs:
                card = bs.cards_in_play.get(card_id)
                if card:
                    action.display_text = f"Target {card.card_title or card_id}"

                    # CRITICAL: Always prefer enemy targets
                    if card.owner != my_name:
                        action.add_reasoning("ENEMY target - good!", +50.0)

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
                    else:
                        # Our own card - DON'T target if we have enemy options
                        action.add_reasoning("OUR card - avoid targeting!", -200.0)
                else:
                    # Card not in our tracking - still give it a reasonable score
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
        """
        actions = []

        logger.warning(f"Unknown CARD_SELECTION type: '{context.decision_text}'")

        # Check if we can select cards (max > 0)
        max_select = context.extra.get('max', 1)  # Default to 1 if not specified
        min_select = context.extra.get('min', 0)

        # Determine base score:
        # - If max > 0: we can select, so give positive score to beat PassEvaluator
        # - If max = 0: we can't select anything, neutral score
        if max_select > 0:
            base_score = 30.0  # Beats PassEvaluator's ~5-20 score
            reason = f"Unknown selection (min={min_select}, max={max_select}) - prefer selecting"
        else:
            base_score = 0.0
            reason = "Unknown selection with max=0 - can't select"

        for card_id in context.card_ids:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.UNKNOWN,
                score=base_score,
                display_text=f"Select card {card_id}"
            )
            action.add_reasoning(reason, 0.0)  # Score already set in base_score
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
