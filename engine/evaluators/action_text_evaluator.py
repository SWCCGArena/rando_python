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
                    # Check if we still need to activate more
                    how_much_we_want = bs.force_to_activate(bs.activation)
                    if bs.force_pile < how_much_we_want:
                        action.score = VERY_GOOD_DELTA
                        action.add_reasoning("Need to activate more force", VERY_GOOD_DELTA)
                    else:
                        # We have enough, rank low to reserve for destiny
                        action.score = VERY_BAD_DELTA
                        action.add_reasoning("Reserving force for destiny draws", VERY_BAD_DELTA)
                else:
                    action.score = GOOD_DELTA
                    action.add_reasoning("Default activate", GOOD_DELTA)

            # ========== Force Drain ==========
            elif action_text == "Force drain":
                action.action_type = ActionType.FORCE_DRAIN

                # Check for Battle Order rules (force drains cost extra)
                under_battle_order = False
                if bs and hasattr(bs, 'strategy_controller') and bs.strategy_controller:
                    under_battle_order = bs.strategy_controller.under_battle_order_rules

                if under_battle_order:
                    # Under Battle Order - avoid low force drains
                    # TODO: Get actual drain amount from location check
                    # For now, assume drain amount is 1-2 (typical)
                    # C# logic: if forceDrainAmount < 2 && underBattleOrderRules -> bad
                    action.score = BAD_DELTA
                    action.add_reasoning("Under Battle Order - drain costs extra", BAD_DELTA)
                else:
                    action.score = VERY_GOOD_DELTA
                    action.add_reasoning("Force drain is good", VERY_GOOD_DELTA)

            # ========== Race Destiny ==========
            elif action_text == "Draw race destiny":
                action.action_type = ActionType.RACE_DESTINY
                action.score = VERY_GOOD_DELTA
                action.add_reasoning("Race destiny always high priority", VERY_GOOD_DELTA)

            # ========== Battle ==========
            elif action_text == "Initiate battle":
                action.action_type = ActionType.BATTLE
                # Let BattleEvaluator handle detailed logic
                action.score = GOOD_DELTA
                action.add_reasoning("Battle option available", GOOD_DELTA)

            # ========== Fire Weapons ==========
            elif "Fire" in action_text:
                action.action_type = ActionType.FIRE_WEAPON
                action.score = VERY_GOOD_DELTA
                action.add_reasoning("Firing weapons always high priority", VERY_GOOD_DELTA)

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
            elif "react" in text_lower:
                action.action_type = ActionType.REACT
                action.score = VERY_GOOD_DELTA
                action.add_reasoning("Reacting is always good", VERY_GOOD_DELTA)

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

            # ========== Cancel Actions (Neutral) ==========
            elif text_lower.startswith("cancel") or "to cancel" in text_lower:
                action.action_type = ActionType.CANCEL
                # Let these be rare - don't rank high or low
                action.add_reasoning("Cancel action - neutral", 0.0)

            # ========== Cancel Battle Damage ==========
            elif "Cancel all remaining battle damage" in action_text:
                action.action_type = ActionType.CANCEL_DAMAGE
                # TODO: Check remaining battle damage amount
                # For now, rank it moderately
                action.score = GOOD_DELTA
                action.add_reasoning("Cancelling battle damage", GOOD_DELTA)

            # ========== Take Card Into Hand ==========
            elif "Take" in action_text and "into hand" in action_text:
                # Check for dangerous cards
                if "palpatine" in text_lower:
                    action.score = BAD_DELTA
                    action.add_reasoning("Avoid taking Palpatine", BAD_DELTA)
                else:
                    action.score = GOOD_DELTA
                    action.add_reasoning("Taking card into hand", GOOD_DELTA)

            # ========== Prevent Battle/Move ==========
            elif "Prevent" in action_text and "from battling or moving" in action_text:
                action.score = GOOD_DELTA
                action.add_reasoning("Preventing opponent actions", GOOD_DELTA)

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
            elif action_text == "Draw card into hand from Force Pile":
                action.action_type = ActionType.DRAW
                action.score = GOOD_DELTA
                action.add_reasoning("Drawing cards is good", GOOD_DELTA)

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
            elif "Embark" in action_text:
                # Embarking is situational - let it be neutral
                action.action_type = ActionType.MOVE
                action.add_reasoning("Embark action", 0.0)

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
            elif "Grab" in action_text:
                # Grabbing is usually good if it's opponent's card
                action.score = GOOD_DELTA
                action.add_reasoning("Grabbing card", GOOD_DELTA)

            # ========== Break cover (spies) ==========
            elif "Break cover" in action_text:
                # Default to neutral - breaking own spy is bad, theirs is good
                action.add_reasoning("Break cover - check whose spy", 0.0)

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

            # ========== Default/Unknown ==========
            else:
                # Unknown action - leave at base score
                action.add_reasoning(f"Unknown action type", 0.0)
                logger.debug(f"Unrecognized action: {action_text}")

            actions.append(action)

        return actions
