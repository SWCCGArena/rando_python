"""
Deploy Phase Evaluator

Evaluates deployment decisions:
- Which cards to deploy from hand
- Where to deploy them (location selection)
- Whether to activate Force instead

Includes strategic improvements:
- Location priority scoring from GameStrategy
- Force generation deficit bonus for locations
- Cross-turn focus bonus for matching card types

Ported from Unity C# AICACHandler.cs and AICSHandler.cs
"""

from typing import List, Optional
import logging
from .base import ActionEvaluator, DecisionContext, EvaluatedAction, ActionType
from ..card_loader import get_card
from ..game_strategy import GameStrategy, ThreatLevel

logger = logging.getLogger(__name__)


class DeployEvaluator(ActionEvaluator):
    """
    Evaluates deployment actions during Deploy phase.

    Scoring factors:
    - Card value (power + ability) vs deploy cost
    - Strategic importance (unique characters, effects)
    - Resource availability (do we have Force to afford this?)
    - Board position (do we need more power?)
    - Location priority from GameStrategy
    - Force generation deficit bonus for locations
    - Cross-turn focus bonus for matching card types
    """

    def __init__(self):
        super().__init__("Deploy")

    def _get_game_strategy(self, context: DecisionContext) -> Optional[GameStrategy]:
        """Get GameStrategy from board_state's strategy_controller"""
        if context.board_state and context.board_state.strategy_controller:
            return context.board_state.strategy_controller.game_strategy
        return None

    def can_evaluate(self, context: DecisionContext) -> bool:
        """Applies to Deploy phase decisions"""
        # For CARD_SELECTION, only evaluate if decision text mentions deploying
        # (avoids incorrectly handling sabacc, forfeit, etc. as deploy decisions)
        if context.decision_type == "CARD_SELECTION":
            if context.decision_text:
                text_lower = context.decision_text.lower()
                return "deploy" in text_lower or "where to" in text_lower
            return False

        # For other decision types, check phase or decision text
        return (
            context.phase and "deploy" in context.phase.lower()
        ) or (
            context.decision_text and "deploy" in context.decision_text.lower()
        )

    def evaluate(self, context: DecisionContext) -> List[EvaluatedAction]:
        """Evaluate all deploy actions"""
        actions = []

        # Handle different decision types
        if context.decision_type == "CARD_ACTION_CHOICE":
            actions = self._evaluate_deploy_actions(context)
        elif context.decision_type == "CARD_SELECTION":
            actions = self._evaluate_location_selection(context)
        elif context.decision_type == "ARBITRARY_CARDS":
            actions = self._evaluate_card_selection(context)

        return actions

    def _evaluate_deploy_actions(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Evaluate CARD_ACTION_CHOICE deploy decisions.

        Example: "Choose Deploy action or Pass" with multiple deploy options
        Only evaluates actions that contain "Deploy" - leaves other actions for other evaluators.
        """
        actions = []
        bs = context.board_state

        for i, action_id in enumerate(context.action_ids):
            action_text = context.action_texts[i] if i < len(context.action_texts) else "Unknown"

            # Only handle deploy-related actions, leave others for other evaluators
            if "Deploy" not in action_text and "Reserve Deck" not in action_text:
                continue

            action = EvaluatedAction(
                action_id=action_id,
                action_type=ActionType.DEPLOY,
                score=50.0,  # Base score
                display_text=action_text
            )

            # Penalize "Take card from Reserve Deck" - risky and often leads to loops
            if "Reserve Deck" in action_text:
                action.add_reasoning("Reserve Deck deploy - risky", -30.0)
                actions.append(action)
                continue

            # Check if this is a deploy action
            if "Deploy" in action_text:
                # Try to extract blueprint ID from action text
                # Format: "Deploy <div class='cardHint' value='7_305'>•OS-72-1</div>"
                blueprint_id = self._extract_blueprint_from_action(action_text)

                if blueprint_id:
                    card_metadata = get_card(blueprint_id)
                    if card_metadata:
                        action.card_name = card_metadata.title
                        action.deploy_cost = card_metadata.deploy_value

                        # Score based on card value (with strategic bonuses)
                        game_strategy = self._get_game_strategy(context)
                        score = self._score_card_deployment(card_metadata, bs, game_strategy)
                        action.score += score
                        action.add_reasoning(f"Card: {card_metadata.title}")

                        # Check if we can afford it
                        if bs and bs.force_pile < card_metadata.deploy_value:
                            action.add_reasoning(f"Can't afford! Need {card_metadata.deploy_value}, have {bs.force_pile}", -100.0)
                    else:
                        action.add_reasoning("Card metadata not found", -10.0)
                else:
                    action.add_reasoning("Deploy action (card unknown)")

            actions.append(action)

        return actions

    def _evaluate_location_selection(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Evaluate CARD_SELECTION for choosing deployment location.

        Example: "Choose where to deploy •OS-72-1"
        """
        actions = []
        bs = context.board_state
        game_strategy = self._get_game_strategy(context)

        if not context.card_ids:
            logger.warning("No card IDs in CARD_SELECTION decision")
            return actions

        # Each card_id represents a location where we can deploy
        for card_id in context.card_ids:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=50.0,  # Base score
                display_text=f"Deploy to location (card {card_id})"
            )

            # Try to find this location in board state
            if bs:
                location = bs.get_location_by_card_id(card_id)
                if location:
                    action.display_text = f"Deploy to {location.site_name or location.system_name or location.blueprint_id}"

                    # Score based on strategic value (with GameStrategy)
                    score = self._score_deployment_location(location, bs, game_strategy)
                    action.score += score
                else:
                    action.add_reasoning("Location not found in board state", -5.0)

            actions.append(action)

        return actions

    def _evaluate_card_selection(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Evaluate ARBITRARY_CARDS for selecting cards to deploy/play.

        Example: "Choose starting location" or "Choose card from Reserve Deck"
        """
        actions = []

        # Only evaluate selectable cards
        for i, card_id in enumerate(context.card_ids):
            # Check if this card is selectable
            if i < len(context.selectable) and not context.selectable[i]:
                logger.debug(f"Skipping non-selectable card: {card_id}")
                continue

            blueprint = context.blueprints[i] if i < len(context.blueprints) else None

            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=50.0,
                display_text=f"Select card {card_id}"
            )

            if blueprint:
                card_metadata = get_card(blueprint)
                if card_metadata:
                    action.card_name = card_metadata.title
                    action.display_text = f"Select {card_metadata.title}"

                    # Score based on card type if deploying from Reserve Deck
                    if "Reserve Deck" in context.decision_text:
                        # Prefer locations and defensive shields
                        if card_metadata.is_location:
                            action.add_reasoning("Location card", +10.0)
                        elif card_metadata.is_defensive_shield:
                            action.add_reasoning("Defensive Shield", +5.0)

            actions.append(action)

        if not actions:
            logger.warning(f"No selectable cards found! Total cards: {len(context.card_ids)}, Selectable flags: {context.selectable}")

        return actions

    def _score_card_deployment(self, card, board_state, game_strategy: Optional[GameStrategy] = None) -> float:
        """
        Score a card for deployment based on strategic value.

        Ported from C# AICACHandler.cs RankDeployAction logic
        Enhanced with strategic bonuses from GameStrategy.
        """
        score = 0.0

        # C# Priority 1: Locations are always high priority
        if card.is_location:
            score += 999.0  # veryGoodActionDelta

            # Strategic bonus: Add force generation deficit bonus
            if game_strategy:
                location_bonus = game_strategy.get_location_deploy_bonus()
                if location_bonus > 0:
                    score += location_bonus
                    logger.debug(f"Location {card.title}: +{location_bonus:.1f} (force gen deficit)")

            return score

        # C# Priority 2: Creatures are always high priority
        if card.card_type == "Creature":
            score += 999.0  # veryGoodActionDelta
            return score

        # C# Priority 3: Weapons/Devices - only if we have warriors
        if card.is_weapon or card.is_device:
            if board_state and self._have_empty_warriors(board_state):
                score += 10.0  # goodActionDelta
            else:
                score += -10.0  # badActionDelta - no warriors to equip
            return score

        # C# Priority 4: Starships/Vehicles without permanent pilot
        if card.is_starship or card.is_vehicle:
            if not card.has_permanent_pilot:
                # Check if we have space locations (for starships)
                if card.is_starship and board_state:
                    has_space = any(loc.is_space for loc in board_state.locations)
                    if not has_space:
                        score += -999.0  # veryBadActionDelta - no space to deploy
                        return score

                # Unpiloted - check if we have pilot in hand
                if board_state and self._have_pilot_in_hand(board_state, card.deploy_value):
                    score += 10.0 * card.power_value  # goodActionDelta * power
                else:
                    score += -10.0  # badActionDelta - no pilot
                return score

        # C# Priority 5: Characters/Vehicles - the main deploy logic
        # Check if this is a "pure pilot" (low power pilot we should avoid)
        is_pure_pilot = False
        is_land_deploy = card.is_vehicle or card.is_character

        if card.is_pilot and not card.is_warrior and is_land_deploy and card.power_value <= 4:
            is_pure_pilot = True
        if card.is_warrior and card.is_pilot and card.power_value <= 3:
            is_pure_pilot = True
        if card.is_vehicle or card.is_starship:
            is_pure_pilot = False

        # Check if we can afford and have force left over
        if board_state:
            force_after = board_state.force_pile - card.deploy_value
            if force_after >= 1 and not is_pure_pilot:
                # Good to deploy
                score += 10.0 * card.power_value  # goodActionDelta * power
            elif force_after < 1 and not is_pure_pilot:
                score += -10.0  # badActionDelta - reserve force
            elif is_pure_pilot:
                score += -10.0  # badActionDelta - avoid pure pilots

        # Base value bonuses
        if card.power_value >= 5:
            score += 10.0
        elif card.power_value >= 3:
            score += 5.0

        if card.ability_value >= 4:
            score += 8.0
        elif card.ability_value >= 2:
            score += 4.0

        # Strategic focus bonus: Prefer cards that match our current strategy
        if game_strategy and card.card_type:
            focus_bonus = game_strategy.get_focus_deploy_bonus(card.card_type)
            if focus_bonus > 0:
                score += focus_bonus
                logger.debug(f"{card.title}: +{focus_bonus:.1f} (matches {game_strategy.current_focus.value} focus)")

        return score

    def _have_empty_warriors(self, board_state) -> bool:
        """Check if we have warriors on the board without weapons"""
        # Simplified - just check if we have any warriors
        for card in board_state.cards_in_play.values():
            if card.owner == board_state.my_player_name:
                metadata = get_card(card.blueprint_id)
                if metadata and metadata.is_warrior:
                    # Check if warrior has no attached weapons
                    has_weapon = any(
                        get_card(ac.blueprint_id) and get_card(ac.blueprint_id).is_weapon
                        for ac in card.attached_cards
                    )
                    if not has_weapon:
                        return True
        return False

    def _have_pilot_in_hand(self, board_state, max_cost: int) -> bool:
        """Check if we have a pilot in hand we could deploy"""
        for card in board_state.cards_in_hand:
            metadata = get_card(card.blueprint_id)
            if metadata and metadata.is_pilot and metadata.deploy_value <= max_cost:
                return True
        return False

    def _score_deployment_location(self, location, board_state, game_strategy: Optional[GameStrategy] = None) -> float:
        """
        Score a location for deploying a card.

        Factors:
        - Force drain potential (opponent force icons = we can drain there)
        - GameStrategy location priority (if available)
        - Do we already have cards there? (concentration vs spreading)
        - Do they have cards there? (battle opportunity)
        - Power differential at location
        - Threat level (avoid dangerous locations)
        """
        score = 0.0

        # IMPORTANT: Force drain potential - locations where opponent has force icons
        # In SWCCG, you can only drain at locations where YOU have presence AND
        # there are OPPONENT force icons. Prioritize these locations!
        their_icons = location.their_icons or ""
        if their_icons and their_icons != "0":
            # Parse icon count (could be "2" or "*2" format)
            try:
                icon_count = int(their_icons.replace("*", "").strip() or "0")
            except ValueError:
                icon_count = 1 if their_icons else 0

            if icon_count > 0:
                # Big bonus for locations where we can drain
                drain_bonus = 15.0 + (icon_count * 5.0)  # Base 15 + 5 per icon
                score += drain_bonus
                logger.debug(f"Location {location.site_name}: +{drain_bonus:.1f} (can drain {icon_count} icons)")

        # Penalty for locations where we CAN'T drain (no opponent icons)
        # Only apply if there's no drain potential
        if not their_icons or their_icons == "0":
            # Check if we already have presence but can't drain
            if len(location.my_cards) > 0:
                score -= 5.0
                logger.debug(f"Location {location.site_name}: -5 (have presence but can't drain)")

        # Use GameStrategy location priority if available
        if game_strategy:
            priority = game_strategy.get_location_priority(location.location_index)
            if priority:
                # Use pre-calculated priority score (scaled down to avoid dominating)
                score += priority.score * 0.5
                logger.debug(f"Location {location.site_name}: priority score {priority.score:.1f}")

                # Reduce score for dangerous locations
                if priority.threat_level == ThreatLevel.DANGEROUS:
                    score -= 15.0
                    logger.debug(f"Location {location.site_name}: -15 (dangerous)")
                elif priority.threat_level == ThreatLevel.RETREAT:
                    score -= 30.0
                    logger.debug(f"Location {location.site_name}: -30 (retreat)")

        # Calculate power differential
        my_power = board_state.my_power_at_location(location.location_index)
        their_power = board_state.their_power_at_location(location.location_index)
        power_diff = my_power - their_power

        # OVERKILL PENALTY: If we already have overwhelming control (+4 or more),
        # strongly penalize deploying more here - spread to other locations instead
        if power_diff >= 8:
            overkill_penalty = -40.0 - (power_diff - 8) * 2  # -40 base, worse as gap grows
            score += overkill_penalty
            logger.debug(f"Location {location.site_name}: {overkill_penalty:.1f} (overkill, +{power_diff} power)")
        elif power_diff >= 4:
            overkill_penalty = -20.0 - (power_diff - 4) * 2  # -20 base
            score += overkill_penalty
            logger.debug(f"Location {location.site_name}: {overkill_penalty:.1f} (already controlling +{power_diff})")

        # CONTESTED BONUS: If opponent has presence but we don't control yet
        # This is a key strategic location to contest
        if len(location.their_cards) > 0:
            if power_diff < 0:
                # We're behind - prioritize this location
                contest_bonus = 15.0 + abs(power_diff) * 1.5
                score += contest_bonus
                logger.debug(f"Location {location.site_name}: +{contest_bonus:.1f} (contest opponent)")
            elif power_diff < 4:
                # Close contest - still valuable
                score += 8.0
                logger.debug(f"Location {location.site_name}: +8.0 (close contest)")
        elif len(location.my_cards) > 0:
            # We have presence, opponent doesn't - minor bonus only if we can drain
            if their_icons and their_icons != "0":
                score += 3.0  # Small bonus - we control and can drain
            # No bonus for locations we control but can't drain

        # EMPTY LOCATION: Deploying to establish presence
        if len(location.my_cards) == 0 and len(location.their_cards) == 0:
            # Slight bonus for expanding - prefer locations with drain potential
            if their_icons and their_icons != "0":
                score += 10.0
                logger.debug(f"Location {location.site_name}: +10.0 (establish presence with drain)")
            else:
                score += 2.0  # Minor bonus for presence without drain potential

        return score

    def _extract_blueprint_from_action(self, action_text: str) -> str:
        """
        Extract blueprint ID from action text HTML.

        Example: "Deploy <div class='cardHint' value='7_305'>•OS-72-1</div>"
        Returns: "7_305"
        """
        import re
        match = re.search(r"value='([^']+)'", action_text)
        if match:
            return match.group(1)
        return ""
