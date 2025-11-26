"""
Card Selection Evaluator

Handles CARD_SELECTION decisions - choosing cards from a list.
Ported from C# AICSHandler.cs

Decision types handled:
- "choose card to set sabacc value" -> PASS (don't set value)
- "choose where to deploy" -> Pick best location
- "choose force to lose" -> Pick best card to lose
- "move/transport/transit" -> Pick best destination
- "choose a card from battle to forfeit" -> Pick lowest forfeit value
- "choose a pilot" -> Pick best pilot
- "choose card to cancel" -> Cancel opponent's cards, not ours
- "choose...clone" -> PASS (don't clone sabacc cards)
"""

import logging
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
            actions = self._evaluate_deploy_location(context)
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
        else:
            # Unknown card selection - create neutral actions
            actions = self._evaluate_unknown(context)

        return actions

    def _evaluate_sabacc_set_value(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Sabacc value setting - we DON'T want to set a value.

        If min=0, we can pass. If min=1, pick the first option but rank low.
        Ported from C# AICSHandler: "choose card to set sabacc value" -> veryBadActionDelta
        """
        actions = []

        for card_id in context.card_ids:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SABACC,
                score=VERY_BAD_DELTA,
                display_text=f"Set sabacc value (card {card_id})"
            )
            action.add_reasoning("Avoid setting sabacc value", VERY_BAD_DELTA)
            actions.append(action)

        logger.info(f"Sabacc set value - marking all {len(actions)} options as very bad")
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

    def _evaluate_deploy_location(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Choose where to deploy a card.

        Consider (in priority order):
        1. Force icons - MORE ICONS = BETTER (for force generation)
        2. Force drain potential (opponent icons = we can drain there)
        3. Power differential at location
        4. Whether we already have presence
        5. Whether enemy is present
        """
        actions = []
        bs = context.board_state
        from ..card_loader import get_card

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
        Choose which card to lose.

        Prefer:
        - Cards from hand (if we have many)
        - Low-value interrupts/effects
        - Low forfeit value cards
        Avoid:
        - High-value characters
        - Cards from force pile
        - Cards when low on life
        """
        actions = []
        bs = context.board_state

        for card_id in context.card_ids:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=0.0,
                display_text=f"Lose card {card_id}"
            )

            if bs:
                card = bs.cards_in_play.get(card_id)
                if card:
                    action.display_text = f"Lose {card.card_title or card_id}"

                    # Check zone
                    if card.zone == "HAND":
                        if bs.hand_size >= 15:
                            action.add_reasoning("Many cards in hand", GOOD_DELTA)
                        elif bs.hand_size <= 5:
                            action.add_reasoning("Few cards in hand", BAD_DELTA)

                        # Prefer losing interrupts/effects/weapons from hand
                        if card.card_type in ["Interrupt", "Effect", "Weapon"]:
                            action.add_reasoning("Low-value card type in hand", GOOD_DELTA)

                    elif card.zone == "FORCE_PILE":
                        action.add_reasoning("Avoid losing from force pile", BAD_DELTA)

                    else:
                        # Card in play - use forfeit value
                        forfeit = card.forfeit if hasattr(card, 'forfeit') else 0
                        forfeit = forfeit if isinstance(forfeit, int) else 0
                        # Prefer forfeiting low-value cards
                        forfeit_bonus = (20 - forfeit)
                        action.add_reasoning(f"Forfeit value {forfeit}", forfeit_bonus)

                # Check overall life
                total_reserve = bs.total_reserve_force()
                if total_reserve < 10:
                    action.add_reasoning("Low on life - be careful", BAD_DELTA)
                elif total_reserve >= 30:
                    action.add_reasoning("Plenty of life left", GOOD_DELTA)

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

        Prefer lowest forfeit value cards.
        """
        actions = []
        bs = context.board_state
        is_optional = "if desired" in context.decision_text.lower()

        for card_id in context.card_ids:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=0.0,
                display_text=f"Forfeit {card_id}"
            )

            if is_optional:
                # Optional forfeit - avoid it
                action.score = VERY_BAD_DELTA
                action.add_reasoning("Optional forfeit - avoid", VERY_BAD_DELTA)
            elif bs:
                card = bs.cards_in_play.get(card_id)
                if card:
                    forfeit = card.forfeit if hasattr(card, 'forfeit') else 0
                    forfeit = forfeit if isinstance(forfeit, int) else 0
                    # Higher bonus for lower forfeit value
                    forfeit_bonus = GOOD_DELTA * (20 - forfeit)
                    action.add_reasoning(f"Forfeit value {forfeit}", forfeit_bonus)
                    action.display_text = f"Forfeit {card.card_title or card_id}"

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
        reserve_size = bs.reserve_deck_size if bs else 30

        # Separate cards into Force (from reserve) vs cards in battle
        force_options = []  # Cards representing Force loss (usually -1_2 blueprint)
        battle_cards = []   # Actual cards in battle to forfeit

        for card_id in context.card_ids:
            card = bs.cards_in_play.get(card_id) if bs else None

            # Check if this is a Force card (blueprint -1_2 or similar)
            # Force cards represent losing from reserve deck
            blueprint = card.blueprint_id if card else ""
            if blueprint.startswith("-1_") or blueprint == "":
                # This is a Force loss option (not a real card)
                force_options.append(card_id)
            else:
                battle_cards.append((card_id, card))

        logger.debug(f"Force loss options: {len(force_options)}, Battle cards: {len(battle_cards)}")

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
                    if card_meta.uniqueness and "*" in card_meta.uniqueness:
                        action.add_reasoning("Unique card - valuable", -10.0)

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

    def _evaluate_unknown(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Unknown card selection - create neutral-scored actions.
        """
        actions = []

        logger.warning(f"Unknown CARD_SELECTION type: '{context.decision_text}'")

        for card_id in context.card_ids:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.UNKNOWN,
                score=0.0,
                display_text=f"Select card {card_id}"
            )
            action.add_reasoning("Unknown selection type - neutral", 0.0)
            actions.append(action)

        return actions
