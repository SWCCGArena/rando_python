"""
Action Text Evaluator

Handles text-based action ranking by pattern matching action text.
Ported from C# AICACHandler.cs - the large if/else block that matches
specific action texts like "Draw race destiny", "Activate Force", etc.

This evaluator provides baseline rankings for common SWCCG actions.

Includes strategic improvements:
- Reserve deck check limiting (max 2 per turn)
"""

import logging
import re
from typing import List, Optional
from .base import ActionEvaluator, DecisionContext, EvaluatedAction, ActionType
from ..game_strategy import GameStrategy
from ..card_loader import get_card

logger = logging.getLogger(__name__)


# Rank deltas (ported from C# BotAIHelper)
VERY_GOOD_DELTA = 50.0
GOOD_DELTA = 30.0
BAD_DELTA = -30.0
VERY_BAD_DELTA = -50.0


class ActionTextEvaluator(ActionEvaluator):
    """
    Evaluates actions based on their text content.

    This is the "catch-all" evaluator that handles actions not covered
    by more specific evaluators (DeployEvaluator, BattleEvaluator, etc.).

    Ported from C# AICACHandler.cs text-matching logic.
    Enhanced with reserve deck check limiting.
    """

    def __init__(self):
        super().__init__("ActionText")

    def _extract_blueprint_from_text(self, action_text: str) -> Optional[str]:
        """
        Extract blueprint ID from action text HTML.

        Example: "Embark <div class='cardHint' value='7_305'>•OS-72-1</div>"
        Returns: "7_305"
        """
        match = re.search(r"value='([^']+)'", action_text)
        if match:
            return match.group(1)
        return None

    def _get_card_owner_from_context(self, context: DecisionContext, card_id: str) -> Optional[str]:
        """
        Get the owner of a card from the board state.

        Returns the player name who owns the card, or None if not found.
        """
        bs = context.board_state
        if bs and card_id:
            card = bs.cards_in_play.get(card_id)
            if card:
                return card.owner
        return None

    def _is_my_card(self, context: DecisionContext, card_id: str) -> bool:
        """Check if a card belongs to us"""
        bs = context.board_state
        if not bs:
            return False
        owner = self._get_card_owner_from_context(context, card_id)
        return owner == bs.my_player_name if owner else False

    def _extract_card_name_from_prevent_text(self, action_text: str) -> Optional[str]:
        """
        Extract card name from barrier card text like:
        "Prevent Han With Heavy Blaster Pistol from battling or moving"

        Returns the card name (e.g., "Han With Heavy Blaster Pistol")
        """
        # Pattern: "Prevent <CARD NAME> from battling or moving"
        if "Prevent" in action_text and "from battling or moving" in action_text:
            # Extract the middle part
            start_idx = action_text.find("Prevent") + len("Prevent ")
            end_idx = action_text.find(" from battling or moving")
            if start_idx > 0 and end_idx > start_idx:
                return action_text[start_idx:end_idx].strip()
        return None

    def _get_target_from_action_text(self, action_text: str, context: DecisionContext) -> dict:
        """
        Try to identify the target of an action from the action text.

        Returns dict with blueprint, card_metadata, is_mine, card_id
        """
        blueprint = self._extract_blueprint_from_text(action_text)
        result = {
            'blueprint': blueprint,
            'card_metadata': None,
            'is_mine': False,
            'card_id': None,
            'is_vehicle': False,
            'is_starship': False,
            'has_pilot': False,
            'is_spy': False,
        }

        if blueprint:
            result['card_metadata'] = get_card(blueprint)
            if result['card_metadata']:
                result['is_vehicle'] = result['card_metadata'].is_vehicle
                result['is_starship'] = result['card_metadata'].is_starship
                result['is_spy'] = result['card_metadata'].is_spy if hasattr(result['card_metadata'], 'is_spy') else False

        # Try to find the card in board state
        bs = context.board_state
        if bs and blueprint:
            # Search for cards with this blueprint
            for card_id, card in bs.cards_in_play.items():
                if card.blueprint_id == blueprint:
                    result['card_id'] = card_id
                    result['is_mine'] = (card.owner == bs.my_player_name)
                    # Check if vehicle/starship has a pilot attached
                    if (result['is_vehicle'] or result['is_starship']) and card.attached_cards:
                        for attached in card.attached_cards:
                            attached_meta = get_card(attached.blueprint_id)
                            if attached_meta and attached_meta.is_pilot:
                                result['has_pilot'] = True
                                break
                    break

        return result

    def _get_game_strategy(self, context: DecisionContext) -> Optional[GameStrategy]:
        """Get GameStrategy from board_state's strategy_controller"""
        if context.board_state and context.board_state.strategy_controller:
            return context.board_state.strategy_controller.game_strategy
        return None

    def can_evaluate(self, context: DecisionContext) -> bool:
        """This evaluator can handle any CARD_ACTION_CHOICE or ACTION_CHOICE"""
        return context.decision_type in ['CARD_ACTION_CHOICE', 'ACTION_CHOICE']

    def evaluate(self, context: DecisionContext) -> List[EvaluatedAction]:
        """Evaluate actions based on text patterns"""
        actions = []
        bs = context.board_state
        game_strategy = self._get_game_strategy(context)

        for i, action_id in enumerate(context.action_ids):
            action_text = context.action_texts[i] if i < len(context.action_texts) else ""
            text_lower = action_text.lower()

            action = EvaluatedAction(
                action_id=action_id,
                action_type=ActionType.UNKNOWN,
                score=0.0,  # Base score
                display_text=action_text
            )

            # ========== Reserve Deck Check Limiting ==========
            # Penalize reserve deck checks if we've already checked this turn
            if game_strategy and ("reserve" in text_lower or "peek" in text_lower):
                if not game_strategy.should_check_reserve():
                    action.score += BAD_DELTA
                    action.add_reasoning(
                        f"Already checked reserve {game_strategy.reserve_checks_this_turn}x this turn",
                        BAD_DELTA
                    )

            # ========== Force Activation ==========
            if action_text == "Activate Force":
                action.action_type = ActionType.ACTIVATE
                if bs:
                    # Check if we'd actually activate any force
                    # This prevents loops where we choose to activate but then activate 0
                    reserve_size = bs.reserve_deck if hasattr(bs, 'reserve_deck') else 20
                    force_pile = bs.force_pile if hasattr(bs, 'force_pile') else 0
                    force_activated = getattr(bs, 'force_activated_this_turn', 0)
                    used_pile = getattr(bs, 'used_pile', 0)
                    life_force = reserve_size + force_pile + used_pile

                    # Constants from force_activation_evaluator
                    MAX_FORCE_PILE = 20
                    RESERVE_FOR_DESTINY = 3
                    RESERVE_FOR_DESTINY_ENDGAME = 2

                    # Calculate if we'd actually want to activate any force
                    would_activate_zero = False
                    skip_reason = None

                    # Check 1: Force pile already at cap
                    if force_pile >= MAX_FORCE_PILE:
                        would_activate_zero = True
                        skip_reason = f"Force pile at max ({force_pile}/{MAX_FORCE_PILE})"

                    # Check 2: Reserve too low for destiny draws
                    elif life_force < 10:
                        # Endgame - need 2 cards for destiny
                        if reserve_size <= RESERVE_FOR_DESTINY_ENDGAME:
                            would_activate_zero = True
                            skip_reason = f"Endgame: reserve ({reserve_size}) needed for destiny"
                    else:
                        # Normal game - need 3 cards for destiny
                        if reserve_size <= RESERVE_FOR_DESTINY:
                            would_activate_zero = True
                            skip_reason = f"Reserve ({reserve_size}) needed for destiny draws"

                    # Check 3: Force pile high and already activated enough
                    if not would_activate_zero and force_pile > 12:
                        max_more_to_activate = max(0, 2 - force_activated)
                        if max_more_to_activate == 0:
                            would_activate_zero = True
                            skip_reason = f"Force pile high ({force_pile}), already activated {force_activated}"
                        # Also check if force pile would exceed cap with even 1 more
                        elif force_pile >= MAX_FORCE_PILE - 1:
                            would_activate_zero = True
                            skip_reason = f"Force pile near max ({force_pile}/{MAX_FORCE_PILE})"

                    # Now score based on whether we'd actually activate
                    if would_activate_zero:
                        # Would activate 0 - should Pass instead to avoid loop
                        action.score = BAD_DELTA
                        action.add_reasoning(f"Skip activation: {skip_reason}", BAD_DELTA)
                    elif reserve_size < 5:
                        # Very low reserve - save for destiny draws
                        action.score = BAD_DELTA
                        action.add_reasoning(f"Reserve critically low ({reserve_size}) - save for destiny", BAD_DELTA)
                    elif force_activated >= bs.activation:
                        # Already activated all available this turn
                        action.score = 0.0
                        action.add_reasoning("Already activated full generation this turn", 0.0)
                    else:
                        # We should activate! Force is free and useful.
                        remaining_to_activate = bs.activation - force_activated
                        action.score = VERY_GOOD_DELTA
                        action.add_reasoning(f"Activate force ({remaining_to_activate} of {bs.activation} remaining)", VERY_GOOD_DELTA)
                else:
                    action.score = GOOD_DELTA
                    action.add_reasoning("Default activate", GOOD_DELTA)

            # ========== Force Drain ==========
            elif action_text == "Force drain":
                action.action_type = ActionType.FORCE_DRAIN

                # Get the location card_id for this drain action
                location_card_id = context.card_ids[i] if i < len(context.card_ids) else None

                # Check actual drain amount from location check data
                drain_amount = -1  # -1 = unknown
                location = None
                location_name = "unknown location"
                if bs and location_card_id:
                    location = bs.get_location_by_card_id(location_card_id)
                    if location:
                        location_name = location.site_name or location.system_name or location.blueprint_id
                        if hasattr(location, 'my_drain_amount') and location.my_drain_amount:
                            drain_str = location.my_drain_amount
                            try:
                                drain_amount = int(drain_str)
                            except ValueError:
                                drain_amount = -1
                            logger.debug(f"Force drain at {location_name}: drain_amount={drain_amount} (from location check)")

                # Fallback: Use static card database force icons if drain amount unknown
                if drain_amount == -1 and location:
                    card_meta = get_card(location.blueprint_id)
                    if card_meta:
                        # Get my force icons from static card data
                        my_side = bs.my_side if bs else "dark"
                        if my_side.lower() == "dark":
                            static_icons = card_meta.dark_side_icons
                        else:
                            static_icons = card_meta.light_side_icons

                        # If static card shows 0 icons for my side, drain will be 0
                        if static_icons == 0:
                            drain_amount = 0
                            logger.info(f"⚠️  Force drain at {location_name}: 0 icons (from card database)")
                        elif static_icons > 0:
                            # Use static icons as estimate (actual may be higher with bonuses)
                            drain_amount = static_icons
                            logger.debug(f"Force drain at {location_name}: ~{drain_amount} icons (from card database)")

                # If drain amount is 0, strongly avoid this drain
                if drain_amount == 0:
                    action.score = VERY_BAD_DELTA * 2  # Extra penalty for pointless drains
                    action.add_reasoning(f"Drain at {location_name} is 0 - pointless!", VERY_BAD_DELTA * 2)
                    # Still append action but with very bad score
                    actions.append(action)
                    continue  # Skip further evaluation for this action

                # Check for Battle Order rules (force drains cost extra)
                under_battle_order = False
                if bs and hasattr(bs, 'strategy_controller') and bs.strategy_controller:
                    under_battle_order = bs.strategy_controller.under_battle_order_rules

                if under_battle_order:
                    # Under Battle Order - avoid low force drains (< 2)
                    if drain_amount >= 0 and drain_amount < 2:
                        action.score = VERY_BAD_DELTA
                        action.add_reasoning(f"Under Battle Order - drain {drain_amount} too low", VERY_BAD_DELTA)
                    elif drain_amount >= 2:
                        action.score = GOOD_DELTA
                        action.add_reasoning(f"Under Battle Order - drain {drain_amount} worth it", GOOD_DELTA)
                    else:
                        # Unknown drain amount - be cautious under battle order
                        action.score = BAD_DELTA
                        action.add_reasoning("Under Battle Order - drain costs extra", BAD_DELTA)
                else:
                    # Not under battle order - drain is good if amount > 0
                    if drain_amount > 0:
                        action.score = VERY_GOOD_DELTA
                        action.add_reasoning(f"Force drain {drain_amount} is good", VERY_GOOD_DELTA)
                    elif drain_amount == -1:
                        # Unknown amount - be cautious, might be 0
                        action.score = BAD_DELTA
                        action.add_reasoning("Force drain (amount unknown - cautious)", BAD_DELTA)
                    # drain_amount == 0 already handled above

            # ========== Race Destiny ==========
            elif action_text == "Draw race destiny":
                action.action_type = ActionType.RACE_DESTINY
                action.score = VERY_GOOD_DELTA
                action.add_reasoning("Race destiny always high priority", VERY_GOOD_DELTA)

            # ========== Play a Card (generic) ==========
            # This action leads to choosing which card to play
            # Penalize if we have little/no Force (most cards cost 2+ Force)
            elif action_text == "Play a card":
                action.action_type = ActionType.PLAY_CARD
                if bs and bs.force_pile == 0:
                    action.score = VERY_BAD_DELTA
                    action.add_reasoning("No Force available - can't play cards!", VERY_BAD_DELTA)
                elif bs and bs.force_pile <= 1:
                    # Very low force - most cards cost 2+, unlikely to play anything
                    action.score = BAD_DELTA
                    action.add_reasoning(f"Very low Force ({bs.force_pile}) - unlikely to afford cards", BAD_DELTA)
                else:
                    # No strong preference - randomize to avoid loops
                    # Sometimes try playing, sometimes pass. Range: -15 to +15
                    import random
                    random_delta = random.uniform(-15.0, 15.0)
                    action.score = random_delta
                    action.add_reasoning(f"Generic play card - randomized ({random_delta:+.1f})", random_delta)

            # ========== Battle ==========
            elif action_text == "Initiate battle":
                action.action_type = ActionType.BATTLE
                # BattleEvaluator handles detailed logic - give minimal score here
                # so BattleEvaluator's analysis (power diff, flee option) takes precedence
                action.score = 0.0
                action.add_reasoning("Battle - see BattleEvaluator for detailed analysis", 0.0)

            # ========== Fire Weapons ==========
            elif "Fire" in action_text:
                action.action_type = ActionType.FIRE_WEAPON

                # Check if there are any valid (non-hit) targets at battle location
                # If all enemies are already hit, don't waste fire
                has_valid_target = True
                if bs and bs.in_battle and bs.current_battle_location >= 0:
                    battle_loc = bs.get_location_by_index(bs.current_battle_location)
                    if battle_loc and battle_loc.their_cards:
                        # Check if ANY enemy card at this location is NOT hit
                        has_valid_target = False
                        for enemy_card in battle_loc.their_cards:
                            if not bs.is_card_hit(enemy_card.card_id):
                                has_valid_target = True
                                break

                if has_valid_target:
                    action.score = VERY_GOOD_DELTA
                    action.add_reasoning("Firing weapons always high priority", VERY_GOOD_DELTA)
                else:
                    action.score = VERY_BAD_DELTA
                    action.add_reasoning("All targets already HIT - don't waste fire!", VERY_BAD_DELTA)

            # ========== Force Lightning / Reduce Defense ==========
            elif "Reduce target's defense value" in action_text or "reduce target's defense" in text_lower:
                # Force Lightning reduces defense - only useful if opponents present
                # Otherwise we'd reduce our own cards' defense!
                has_opponents = False
                if bs and hasattr(bs, 'locations'):
                    # Check if there are any opponent cards at any location where we have cards
                    for loc in bs.locations:
                        if loc.my_cards and loc.their_cards:
                            has_opponents = True
                            break

                if has_opponents:
                    action.score = GOOD_DELTA
                    action.add_reasoning("Force Lightning - opponents present", GOOD_DELTA)
                else:
                    action.score = VERY_BAD_DELTA
                    action.add_reasoning("Force Lightning - no opponents, would hurt own cards!", VERY_BAD_DELTA)

            # ========== Add Battle Destiny ==========
            elif "add" in text_lower and "battle destiny" in text_lower:
                action.action_type = ActionType.BATTLE_DESTINY
                action.score = VERY_GOOD_DELTA
                action.add_reasoning("Adding battle destiny is great", VERY_GOOD_DELTA)

            # ========== Substitute Destiny ==========
            elif "substitute destiny" in text_lower:
                action.action_type = ActionType.SUBSTITUTE_DESTINY
                action.score = GOOD_DELTA
                action.add_reasoning("Substituting destiny is good", GOOD_DELTA)

            # ========== React ==========
            # Bot doesn't understand react timing well, so avoid using reacts
            # Let normal deployment be preferred over reacting
            elif "react" in text_lower:
                action.action_type = ActionType.REACT
                action.score = BAD_DELTA
                action.add_reasoning("Avoid reacts (bot doesn't understand timing)", BAD_DELTA)

            # ========== Steal ==========
            elif "steal" in text_lower:
                action.action_type = ActionType.STEAL
                action.score = GOOD_DELTA
                action.add_reasoning("Stealing is good", GOOD_DELTA)

            # ========== Sabacc ==========
            elif "play sabacc" in text_lower:
                action.action_type = ActionType.SABACC
                action.score = GOOD_DELTA
                action.add_reasoning("Playing sabacc", GOOD_DELTA)

            # ========== Cancel Own Cards (Bad!) ==========
            elif "cancel your" in text_lower:
                action.action_type = ActionType.CANCEL
                action.score = VERY_BAD_DELTA
                action.add_reasoning("Never cancel own cards", VERY_BAD_DELTA)

            # ========== Sense - Cancel Opponent's Interrupt ==========
            # Sense: "Target one just-played Interrupt. If destiny < ability, cancel target Interrupt"
            # This appears when opponent plays an interrupt and we can respond with Sense.
            # Generally valuable to cancel opponent's interrupts, but save for important ones.
            elif ("cancel" in text_lower and "interrupt" in text_lower and
                  "your" not in text_lower):
                action.action_type = ActionType.CANCEL

                # Check what interrupt we're canceling (might be in action text)
                # High-value targets to cancel: Barrier, Houjix, Ghhhk, Sense itself
                high_value_target = False
                for target in ["barrier", "houjix", "ghhhk", "sense", "alter",
                              "nabrun", "elis", "hyper", "escape pod"]:
                    if target in text_lower:
                        high_value_target = True
                        break

                # Also check if during battle (canceling battle interrupts is valuable)
                in_battle = bs and getattr(bs, 'in_battle', False)

                if high_value_target:
                    action.score = VERY_GOOD_DELTA
                    action.add_reasoning("Cancel HIGH VALUE opponent interrupt!", VERY_GOOD_DELTA)
                elif in_battle:
                    action.score = GOOD_DELTA + 10.0
                    action.add_reasoning("Cancel opponent interrupt during battle", GOOD_DELTA + 10.0)
                elif not context.is_my_turn:
                    # During opponent's turn - their interrupts are usually important
                    action.score = GOOD_DELTA
                    action.add_reasoning("Cancel opponent interrupt (their turn)", GOOD_DELTA)
                else:
                    # During our turn - they're responding to us, might be worth canceling
                    action.score = 15.0  # Moderate priority
                    action.add_reasoning("Cancel opponent interrupt (our turn)", 15.0)

            # ========== Destiny Manipulation (Jedi Levitation / Sith Fury) ==========
            # USED: "If you just drew a character for destiny, take that card into hand"
            # This is triggered when we draw a character for destiny.
            # Take high-value characters into hand, redraw if destiny was low.
            elif "cancel and redraw" in text_lower and "destiny" in text_lower:
                # Option to cancel current destiny and redraw
                # Generally good if current destiny is low
                action.score = GOOD_DELTA
                action.add_reasoning("Redraw destiny (current may be low)", GOOD_DELTA)

            # ========== Cancel Actions (Neutral) ==========
            elif text_lower.startswith("cancel") or "to cancel" in text_lower:
                action.action_type = ActionType.CANCEL
                # Let these be rare - don't rank high or low
                action.add_reasoning("Cancel action - neutral", 0.0)

            # ========== Cancel Battle Damage (Houjix / Ghhhk) ==========
            # These are CRITICAL survival cards. Per the rules:
            # "If you lost battle and have no cards left to forfeit, cancel all remaining battle damage"
            # Use when:
            #   - We're in damage segment (in_battle should be true)
            #   - We have remaining damage to take
            #   - We have NO cards left to forfeit at battle location
            # Save when:
            #   - We still have cards to forfeit (use those first)
            #   - Damage remaining is minimal (save for bigger emergencies)
            elif "Cancel all remaining battle damage" in action_text:
                action.action_type = ActionType.CANCEL_DAMAGE

                # Get our damage remaining
                my_attrition = 0
                my_damage = 0
                cards_at_battle = 0

                if bs:
                    my_side = getattr(bs, 'my_side', 'dark').lower()
                    if my_side == 'dark':
                        my_attrition = getattr(bs, 'dark_attrition_remaining', 0)
                        my_damage = getattr(bs, 'dark_damage_remaining', 0)
                    else:
                        my_attrition = getattr(bs, 'light_attrition_remaining', 0)
                        my_damage = getattr(bs, 'light_damage_remaining', 0)

                    # Check if we have cards at battle location to forfeit
                    battle_loc_idx = getattr(bs, 'current_battle_location', -1)
                    if battle_loc_idx >= 0 and battle_loc_idx < len(bs.locations):
                        battle_loc = bs.locations[battle_loc_idx]
                        if battle_loc:
                            cards_at_battle = len(battle_loc.my_cards)

                total_damage = my_attrition + my_damage
                logger.info(f"🛡️ Houjix/Ghhhk analysis: attrition={my_attrition}, damage={my_damage}, "
                           f"total={total_damage}, cards_at_battle={cards_at_battle}")

                if total_damage <= 0:
                    # No damage to cancel - don't waste the card
                    action.score = VERY_BAD_DELTA
                    action.add_reasoning("No damage to cancel - save Houjix/Ghhhk!", VERY_BAD_DELTA)
                elif cards_at_battle > 0:
                    # We have cards we could forfeit instead - probably should do that
                    # But if damage is VERY high, might still want to use it
                    if total_damage >= 8:
                        action.score = GOOD_DELTA
                        action.add_reasoning(f"High damage ({total_damage}) - consider using despite {cards_at_battle} cards", GOOD_DELTA)
                    else:
                        action.score = BAD_DELTA
                        action.add_reasoning(f"Have {cards_at_battle} cards to forfeit - save Houjix/Ghhhk", BAD_DELTA)
                elif total_damage >= 5:
                    # CRITICAL: No cards to forfeit and significant damage!
                    action.score = VERY_GOOD_DELTA + 20.0
                    action.add_reasoning(f"CRITICAL: {total_damage} damage with NO forfeit options - USE NOW!", VERY_GOOD_DELTA + 20.0)
                elif total_damage >= 2:
                    # Moderate damage, no forfeit options - use it
                    action.score = VERY_GOOD_DELTA
                    action.add_reasoning(f"No forfeit options, {total_damage} damage - use Houjix/Ghhhk", VERY_GOOD_DELTA)
                else:
                    # Small damage (1) - might want to save for bigger emergency
                    action.score = GOOD_DELTA
                    action.add_reasoning(f"Small damage ({total_damage}) - could save for bigger emergency", GOOD_DELTA)

            # ========== Take Card Into Hand ==========
            # This includes destiny manipulation (Jedi Levitation / Sith Fury):
            # "Take [character just drawn for destiny] into hand"
            # Want to take high-value unique characters, avoid taking weak ones
            elif "Take" in action_text and "into hand" in action_text:
                # Check for dangerous cards
                if "palpatine" in text_lower:
                    action.score = BAD_DELTA
                    action.add_reasoning("Avoid taking Palpatine", BAD_DELTA)
                else:
                    # Try to identify the card being taken
                    target_info = self._get_target_from_action_text(action_text, context)
                    card_meta = target_info.get('card_metadata')

                    # Check if this is a character destiny (Jedi Levitation/Sith Fury scenario)
                    is_character_destiny = card_meta and card_meta.is_character

                    if is_character_destiny:
                        # Taking a character drawn for destiny into hand
                        destiny = card_meta.destiny_value or 0
                        power = card_meta.power_value or 0
                        is_unique = card_meta.is_unique

                        # High-value characters we want in hand
                        if is_unique and (power >= 5 or destiny >= 5):
                            action.score = VERY_GOOD_DELTA
                            action.add_reasoning(f"Take HIGH VALUE character (power {power}, destiny {destiny}) into hand!", VERY_GOOD_DELTA)
                        elif destiny >= 4:
                            # Good destiny - might want to keep as destiny
                            action.score = 10.0  # Neutral-ish, let it draw
                            action.add_reasoning(f"Destiny {destiny} is good - consider keeping as destiny", 10.0)
                        elif is_unique:
                            # Unique character, moderate value
                            action.score = GOOD_DELTA
                            action.add_reasoning(f"Take unique character into hand", GOOD_DELTA)
                        else:
                            # Non-unique, low destiny - probably keep as destiny
                            action.score = 5.0
                            action.add_reasoning(f"Low destiny ({destiny}) non-unique - might keep as destiny", 5.0)
                    else:
                        # Generic take into hand - usually good
                        action.score = GOOD_DELTA
                        action.add_reasoning("Taking card into hand", GOOD_DELTA)

            # ========== Prevent Battle/Move (Barrier Cards) ==========
            # Barrier cards (Imperial/Rebel Barrier) prevent opponent from battling or moving.
            # Use when:
            #   - Location IS contested (both players present)
            #   - Target is a significant threat (high power)
            #   - We're not already winning overwhelmingly
            # Save when:
            #   - Location not contested (no point)
            #   - We're already dominating the location
            elif "Prevent" in action_text and "from battling or moving" in action_text:
                target_card_name = self._extract_card_name_from_prevent_text(action_text)
                location_contested = False
                target_power = 0
                my_power = 0
                their_power = 0
                location_name = "unknown"

                if bs and target_card_name:
                    # Find the card being prevented and analyze the situation
                    for card_id, card in bs.cards_in_play.items():
                        if card.card_title and target_card_name.lower() in card.card_title.lower():
                            # Found the card - analyze its location
                            loc_idx = card.location_index
                            if loc_idx >= 0 and loc_idx < len(bs.locations):
                                loc = bs.locations[loc_idx]
                                if loc:
                                    location_name = loc.site_name or loc.system_name or "location"
                                    has_my_presence = len(loc.my_cards) > 0
                                    has_their_presence = len(loc.their_cards) > 0
                                    location_contested = has_my_presence and has_their_presence
                                    my_power = loc.my_power or 0
                                    their_power = loc.their_power or 0

                                    # Get target's power from card metadata
                                    target_meta = get_card(card.blueprint_id)
                                    if target_meta:
                                        target_power = target_meta.power_value or 0

                                    logger.info(f"🚧 Barrier analysis: {target_card_name} (power {target_power}) at {location_name}, "
                                               f"my_power={my_power}, their_power={their_power}, contested={location_contested}")
                            break

                if not location_contested:
                    # Location NOT contested - save barrier for when we need it
                    action.score = BAD_DELTA
                    action.add_reasoning(f"Save barrier - {location_name} not contested", BAD_DELTA)
                elif my_power >= their_power + 8:
                    # We're already dominating - don't waste the barrier
                    action.score = BAD_DELTA
                    action.add_reasoning(f"Save barrier - already dominating ({my_power} vs {their_power})", BAD_DELTA)
                elif target_power >= 5:
                    # High-power target at contested location - VERY valuable!
                    action.score = VERY_GOOD_DELTA
                    action.add_reasoning(f"Barrier on HIGH POWER target ({target_power}) at contested {location_name}!", VERY_GOOD_DELTA)
                elif their_power >= my_power:
                    # They're winning or tied - barrier is valuable
                    action.score = GOOD_DELTA + 10.0
                    action.add_reasoning(f"Barrier to protect at {location_name} (losing {my_power} vs {their_power})", GOOD_DELTA + 10.0)
                else:
                    # We're ahead but not dominating - still useful
                    action.score = GOOD_DELTA
                    action.add_reasoning(f"Barrier at contested {location_name}", GOOD_DELTA)

            # ========== Monnok-type (Reveal Hand) ==========
            elif "LOST: Reveal opponent's hand" in action_text:
                if bs and bs.their_hand_size > 6:
                    action.score = VERY_GOOD_DELTA
                    action.add_reasoning("Opponent has many cards - reveal worth it", VERY_GOOD_DELTA)
                else:
                    action.score = VERY_BAD_DELTA
                    action.add_reasoning("Opponent has few cards - save reveal", VERY_BAD_DELTA)

            # ========== Dangerous Cards ==========
            elif "stardust" in text_lower or "on the edge" in text_lower:
                action.score = VERY_BAD_DELTA
                action.add_reasoning("Known dangerous card", VERY_BAD_DELTA)

            # ========== Draw Into Hand ==========
            # Note: DrawEvaluator handles detailed scoring for draw actions,
            # including hand size caps and life force preservation.
            # We just mark the action type here - don't add score to avoid overriding DrawEvaluator's logic.
            elif action_text == "Draw card into hand from Force Pile":
                action.action_type = ActionType.DRAW
                # Let DrawEvaluator handle scoring - don't add score here
                action.add_reasoning("Draw option (see DrawEvaluator)", 0.0)

            # ========== Movement Actions ==========
            # Note: MoveEvaluator handles detailed scoring for movement.
            # We just mark the action type here - don't add score to avoid overriding MoveEvaluator's logic.
            elif any(x in action_text for x in ["Move using", "Shuttle", "Docking bay transit", "Transport"]):
                action.action_type = ActionType.MOVE
                # Let MoveEvaluator handle scoring - don't add score here
                action.add_reasoning("Movement option (see MoveEvaluator)", 0.0)
            elif action_text in ["Take off", "Land"]:
                action.action_type = ActionType.MOVE
                action.add_reasoning("Take off/Land option (see MoveEvaluator)", 0.0)

            # ========== Make Opponent Lose Force ==========
            elif "Make opponent lose" in action_text:
                action.score = GOOD_DELTA
                action.add_reasoning("Making opponent lose force", GOOD_DELTA)

            # ========== Deploy Docking Bay ==========
            elif "Deploy docking bay" in action_text:
                action.score = GOOD_DELTA
                action.add_reasoning("Deploying docking bay", GOOD_DELTA)

            # ========== Deploy From Reserve (Risky) ==========
            elif "Deploy" in action_text and "from" in action_text:
                # Deploying from reserve deck can be risky
                action.score = BAD_DELTA
                action.add_reasoning("Deploying from reserve - risky", BAD_DELTA)

            # ========== Very Rare Actions (Low Priority) ==========
            elif any(x in action_text for x in [
                "Naboo: Boss Nass", "Tatooine: Watto", "Tatooine: Mos Espa",
                "Lock s-foils", "Exchange card in hand"
            ]):
                action.add_reasoning("Rare action - neutral priority", 0.0)
            elif "place card from hand in used pile" in text_lower:
                action.add_reasoning("Rare action - neutral priority", 0.0)

            # ========== Embark (onto vehicles/ships) ==========
            # Ported from C# AICACHandler.cs lines 738-767
            elif "Embark" in action_text:
                action.action_type = ActionType.MOVE

                # Get card being embarked (should be from context)
                embarking_card_id = context.card_ids[i] if i < len(context.card_ids) else None
                embarking_card = None
                if bs and embarking_card_id:
                    embarking_card = bs.cards_in_play.get(embarking_card_id)

                # Get target vehicle/starship from action text
                target_info = self._get_target_from_action_text(action_text, context)

                # Check if embarking card is a pilot
                is_pilot = False
                if embarking_card:
                    embarking_meta = get_card(embarking_card.blueprint_id)
                    if embarking_meta:
                        is_pilot = embarking_meta.is_pilot

                # Embark logic from C#:
                # - Only embark if we're a pilot and target vehicle/starship needs a pilot
                if target_info['is_vehicle'] or target_info['is_starship']:
                    if is_pilot and not target_info['has_pilot']:
                        action.score = VERY_GOOD_DELTA
                        action.add_reasoning("Pilot embarking on unpiloted vehicle/starship", VERY_GOOD_DELTA)
                    elif target_info['has_pilot']:
                        action.score = BAD_DELTA
                        action.add_reasoning("Vehicle/starship already has pilot", BAD_DELTA)
                    else:
                        action.score = BAD_DELTA
                        action.add_reasoning("Non-pilot embarking (usually bad)", BAD_DELTA)
                else:
                    # Generic embark - neutral
                    action.add_reasoning("Generic embark action", 0.0)

            # ========== Disembark/Relocate/Transfer (usually bad) ==========
            elif any(x in action_text for x in ["Disembark", "Relocate", "Transfer"]):
                action.action_type = ActionType.MOVE
                action.score = VERY_BAD_DELTA
                action.add_reasoning("Usually avoid disembark/relocate/transfer", VERY_BAD_DELTA)

            # ========== Ship-dock (usually bad) ==========
            elif "Ship-dock" in action_text:
                action.score = VERY_BAD_DELTA
                action.add_reasoning("Avoid ship-docking", VERY_BAD_DELTA)

            # ========== Place in Lost Pile (bad) ==========
            elif "Place in Lost Pile" in action_text:
                action.score = VERY_BAD_DELTA
                action.add_reasoning("Avoid losing cards", VERY_BAD_DELTA)

            # ========== Grab opponent's card ==========
            # Ported from C# AICACHandler.cs lines 593-627
            # Only grab opponent's cards (different side), not our own
            elif "Grab" in action_text:
                target_info = self._get_target_from_action_text(action_text, context)

                if target_info['card_id']:
                    if target_info['is_mine']:
                        # Grabbing our own card - bad!
                        action.score = VERY_BAD_DELTA
                        action.add_reasoning("Don't grab own card", VERY_BAD_DELTA)
                    else:
                        # Grabbing opponent's card - good!
                        action.score = GOOD_DELTA
                        action.add_reasoning("Grab opponent's card", GOOD_DELTA)
                else:
                    # Can't determine owner - check by card side in metadata
                    if target_info['card_metadata']:
                        my_side = bs.my_side if bs else "unknown"
                        card_side = target_info['card_metadata'].side
                        if card_side and card_side.lower() == my_side.lower():
                            # Same side as us - probably our card
                            action.score = BAD_DELTA
                            action.add_reasoning("Grab appears to be same-side card", BAD_DELTA)
                        else:
                            # Different side - opponent's card
                            action.score = GOOD_DELTA
                            action.add_reasoning("Grab opponent-side card", GOOD_DELTA)
                    else:
                        # Unknown - be cautious
                        action.add_reasoning("Grab card (owner unknown)", 0.0)

            # ========== Break cover (spies) ==========
            # Ported from C# AICACHandler.cs lines 799-829
            # Breaking opponent's spy is good, breaking our spy is bad
            elif "Break cover" in action_text:
                target_info = self._get_target_from_action_text(action_text, context)

                if target_info['card_id']:
                    if target_info['is_mine']:
                        # Breaking our own spy - very bad!
                        action.score = VERY_BAD_DELTA
                        action.add_reasoning("Don't break cover of own spy!", VERY_BAD_DELTA)
                    else:
                        # Breaking opponent's spy - good!
                        action.score = GOOD_DELTA
                        action.add_reasoning("Break opponent's spy cover", GOOD_DELTA)
                else:
                    # Can't determine owner from board - check card side
                    if target_info['card_metadata']:
                        my_side = bs.my_side if bs else "unknown"
                        card_side = target_info['card_metadata'].side
                        if card_side and card_side.lower() == my_side.lower():
                            # Same side - probably our spy
                            action.score = VERY_BAD_DELTA
                            action.add_reasoning("Break cover appears to be own spy", VERY_BAD_DELTA)
                        else:
                            # Different side - opponent's spy
                            action.score = GOOD_DELTA
                            action.add_reasoning("Break opponent-side spy cover", GOOD_DELTA)
                    else:
                        # Unknown spy - be cautious, default to not doing it
                        action.score = BAD_DELTA
                        action.add_reasoning("Break cover (spy owner unknown - cautious)", BAD_DELTA)

            # ========== Retrieve force ==========
            elif "retrieve" in text_lower or "Place out of play to retrieve" in action_text:
                if bs and hasattr(bs, 'lost_pile') and bs.lost_pile > 15:
                    action.score = GOOD_DELTA
                    action.add_reasoning("High lost pile - retrieve worth it", GOOD_DELTA)
                else:
                    action.score = BAD_DELTA
                    action.add_reasoning("Low lost pile - save retrieve", BAD_DELTA)

            # ========== Objective actions ==========
            elif bs and hasattr(bs, 'cards_in_play'):
                # Check if action relates to objective card type
                # This is a heuristic - objectives are high priority
                if "Objective" in action_text:
                    action.score = VERY_GOOD_DELTA
                    action.add_reasoning("Objective action", VERY_GOOD_DELTA)

            # ========== Defensive Shields ==========
            elif "Play a Defensive Shield" in action_text:
                # During battle when it's NOT our turn, prefer to pass
                # Playing shields during opponent's battle often just triggers
                # a card selection that we cancel, creating loops
                bs = context.board_state
                in_battle = bs and getattr(bs, 'in_battle', False)
                is_our_turn = context.is_my_turn

                if in_battle and not is_our_turn:
                    # During opponent's battle - low priority, prefer passing
                    action.score = -10.0
                    action.add_reasoning("Defensive shield during opponent's battle - prefer pass", -10.0)
                else:
                    action.score = VERY_GOOD_DELTA
                    action.add_reasoning("Defensive shield", VERY_GOOD_DELTA)

            # ========== Deploy on (table/location) ==========
            elif action_text.startswith("Deploy on"):
                # Check for bad targets
                if "projection" in text_lower and "side" in text_lower:
                    action.score = VERY_BAD_DELTA
                    action.add_reasoning("Never put projection on side of table", VERY_BAD_DELTA)
                else:
                    action.score = GOOD_DELTA
                    action.add_reasoning("Deploy on location/table", GOOD_DELTA)

            # ========== Deploy unique (battleground rule) ==========
            elif action_text.startswith("Deploy unique"):
                action.score = GOOD_DELTA
                action.add_reasoning("Special battleground deploy", GOOD_DELTA)

            # ========== USED: Peek at top (card advantage) ==========
            elif action_text.startswith("USED: Peek at top"):
                action.score = GOOD_DELTA
                action.add_reasoning("Peek for card advantage", GOOD_DELTA)

            # ========== Add (generic) ==========
            elif "add " in text_lower and len(action_text) < 50:
                action.score = GOOD_DELTA
                action.add_reasoning("Add to something", GOOD_DELTA)

            # ========== Force Drain Cancellation (during opponent's turn) ==========
            elif "Cancel Force drain" in action_text:
                # Only cancel during opponent's turn
                if context.is_my_turn:
                    action.score = VERY_BAD_DELTA
                    action.add_reasoning("Don't cancel own force drain", VERY_BAD_DELTA)
                else:
                    action.score = GOOD_DELTA
                    action.add_reasoning("Cancel opponent's force drain", GOOD_DELTA)

            # ========== Peek at opponent's Reserve Deck ==========
            elif "Peek at top of opponent's Reserve Deck" in action_text:
                action.score = BAD_DELTA
                action.add_reasoning("Peeking rarely worth it", BAD_DELTA)

            # ========== "Use X Force" / "Lose X Force" Actions ==========
            # These cost force and are often optional - randomize with skew toward passing
            elif text_lower.startswith("use ") and " force " in text_lower:
                import random
                # Skew toward negative: 70% chance of negative score
                if random.random() < 0.7:
                    random_delta = random.uniform(-40.0, -5.0)  # Negative range
                else:
                    random_delta = random.uniform(-5.0, 20.0)   # Occasionally positive
                action.score = random_delta
                action.add_reasoning(f"'Use Force' action - randomized, skew pass ({random_delta:+.1f})", random_delta)

            elif text_lower.startswith("lose ") and " force " in text_lower:
                import random
                # Strongly skew toward negative - losing force is rarely good
                if random.random() < 0.85:
                    random_delta = random.uniform(-50.0, -10.0)  # Usually negative
                else:
                    random_delta = random.uniform(-10.0, 10.0)   # Rarely positive
                action.score = random_delta
                action.add_reasoning(f"'Lose Force' action - randomized, strong skew pass ({random_delta:+.1f})", random_delta)

            # ========== Default/Unknown ==========
            else:
                # Unknown action - leave at base score
                action.add_reasoning(f"Unknown action type", 0.0)
                logger.debug(f"Unrecognized action: {action_text}")

            actions.append(action)

        return actions
