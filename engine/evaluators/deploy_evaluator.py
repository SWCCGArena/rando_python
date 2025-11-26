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
        # Track cards we've already tried deploying this turn to avoid retry loops
        # Ported from C# BotAIHelper.pendingDeployCards
        self.pending_deploy_card_ids: set = set()
        self._last_turn_number = -1

    def reset_pending_deploys(self):
        """Reset pending deploy tracking (call at turn start)"""
        self.pending_deploy_card_ids.clear()

    def track_deploy(self, card_id: str):
        """Track that we tried deploying this card"""
        self.pending_deploy_card_ids.add(card_id)

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

        # Reset pending deploy tracking at the start of each turn
        if context.turn_number != self._last_turn_number:
            self.reset_pending_deploys()
            self._last_turn_number = context.turn_number

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

            # Check if we already tried deploying this card (avoid loops)
            card_id = context.card_ids[i] if i < len(context.card_ids) else None
            if card_id and card_id in self.pending_deploy_card_ids:
                action.add_reasoning("Already tried deploying this card this turn", -500.0)
                actions.append(action)
                continue

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

                        # LOCATIONS ALWAYS DEPLOY FIRST
                        # This is critical because deploying a location creates new options
                        # for characters/ships that we can't evaluate until location exists
                        if card_metadata.is_location:
                            action.add_reasoning("LOCATION - always deploy first!", +2000.0)

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
        Evaluate CARD_SELECTION for choosing deployment location/target.

        Example: "Choose where to deploy •Boba Fett In Slave I"
        Example: "Choose where to deploy •X-wing Laser Cannon"

        IMPORTANT rules:
        - Starships should ONLY deploy to space locations (0 power at ground)
        - Vehicles are fine at ground locations
        - Weapons should prefer targets WITHOUT existing weapons
        - PILOTS should prefer unpiloted vehicles/starships over ground locations!
        """
        actions = []
        bs = context.board_state
        game_strategy = self._get_game_strategy(context)

        if not context.card_ids:
            logger.warning("No card IDs in CARD_SELECTION decision")
            return actions

        # Extract the card being deployed from decision text
        # Format: "Choose where to deploy <div class='cardHint' value='109_8'>•Boba Fett In Slave I</div>"
        deploying_card_blueprint = self._extract_blueprint_from_action(context.decision_text)
        deploying_card = get_card(deploying_card_blueprint) if deploying_card_blueprint else None

        # Check card type being deployed
        is_starship = deploying_card and deploying_card.is_starship
        is_vehicle = deploying_card and deploying_card.is_vehicle
        is_weapon = deploying_card and deploying_card.is_weapon
        is_device = deploying_card and deploying_card.is_device
        is_pilot = deploying_card and deploying_card.is_pilot

        if deploying_card:
            logger.debug(f"Deploying {deploying_card.title}: starship={is_starship}, vehicle={is_vehicle}, weapon={is_weapon}, pilot={is_pilot}")

        # Each card_id represents a target where we can deploy (location or card)
        for card_id in context.card_ids:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=50.0,  # Base score
                display_text=f"Deploy to (card {card_id})"
            )

            if bs:
                # For weapons/devices, target is a card (character, starship, vehicle)
                if is_weapon or is_device:
                    target_card = bs.cards_in_play.get(card_id)
                    if target_card:
                        target_meta = get_card(target_card.blueprint_id)
                        target_name = target_card.card_title or target_card.blueprint_id
                        action.display_text = f"Deploy to {target_name}"

                        # Check if target already has a weapon attached
                        has_existing_weapon = any(
                            get_card(ac.blueprint_id) and get_card(ac.blueprint_id).is_weapon
                            for ac in target_card.attached_cards
                        )

                        if has_existing_weapon:
                            # Target already has a weapon - VERY BAD
                            action.add_reasoning("TARGET ALREADY HAS WEAPON!", -500.0)
                            logger.warning(f"⚠️  {target_name} already has a weapon attached")
                        else:
                            # Target has no weapon - good!
                            action.add_reasoning("Target has no weapon - good", +20.0)

                        # Prefer our own cards over opponent's (if weapon can go on either)
                        if target_card.owner == bs.my_player_name:
                            action.add_reasoning("Our card", +10.0)
                    else:
                        action.add_reasoning("Target card not found", -5.0)

                # For pilots, check if target is a vehicle/starship that needs a pilot
                elif is_pilot:
                    # First check if target is a vehicle/starship we can pilot
                    target_card = bs.cards_in_play.get(card_id)
                    if target_card and target_card.owner == bs.my_player_name:
                        target_meta = get_card(target_card.blueprint_id)
                        if target_meta and (target_meta.is_vehicle or target_meta.is_starship):
                            target_name = target_card.card_title or target_card.blueprint_id
                            action.display_text = f"Pilot aboard {target_name}"

                            # Check if vehicle/starship already has a pilot
                            has_pilot = self._card_has_pilot(target_card, target_meta)

                            if not has_pilot:
                                # UNPILOTED vehicle/starship - HIGH PRIORITY!
                                # This gives the vehicle/starship power
                                action.add_reasoning("PILOT UNPILOTED VEHICLE/STARSHIP!", +150.0)
                                logger.info(f"🎯 {deploying_card.title} can pilot unpiloted {target_name}")
                            else:
                                # Already has a pilot - lower priority (adds ability but redundant)
                                action.add_reasoning("Vehicle already has pilot", -20.0)
                            actions.append(action)
                            continue  # Skip location check for this target

                    # Not a vehicle/starship, check if it's a location
                    location = bs.get_location_by_card_id(card_id)
                    if location:
                        action.display_text = f"Deploy to {location.site_name or location.system_name or location.blueprint_id}"

                        # Pilot deploying to ground - check if we have unpiloted vehicles there
                        has_unpiloted = self._has_unpiloted_vehicle_at_location(bs, location)
                        if has_unpiloted:
                            # We have an unpiloted vehicle here - deploying pilot to ground wastes potential!
                            action.add_reasoning("Have unpiloted vehicle here - pilot it instead!", -50.0)
                        else:
                            # No unpiloted vehicles - ground deploy is fine
                            action.add_reasoning("Pilot to ground (no vehicles to pilot)", +5.0)

                        # Score based on strategic value
                        score = self._score_deployment_location(location, bs, game_strategy)
                        action.score += score
                    else:
                        action.add_reasoning("Target not found", -5.0)

                # For starships/vehicles/characters (non-pilot), target is a location
                else:
                    location = bs.get_location_by_card_id(card_id)
                    if location:
                        action.display_text = f"Deploy to {location.site_name or location.system_name or location.blueprint_id}"

                        # CRITICAL: Starships have 0 power at ground locations!
                        # - Pure space (systems, sectors): is_space=True, is_ground=False -> has power
                        # - Docking bays: is_space=True, is_ground=True -> can deploy, but 0 power!
                        # - Pure ground sites: is_space=False, is_ground=True -> usually can't deploy
                        if is_starship:
                            is_pure_space = location.is_space and not getattr(location, 'is_ground', False)
                            is_docking_bay = location.is_space and getattr(location, 'is_ground', False)

                            if is_pure_space:
                                # System or Sector - starship has power here
                                action.add_reasoning("Starship to space - has power!", +20.0)
                            elif is_docking_bay:
                                # Docking bay - starship can deploy but has 0 power
                                action.add_reasoning("STARSHIP TO DOCKING BAY - 0 power!", -500.0)
                                logger.warning(f"⚠️  Starship {deploying_card.title} would have 0 power at docking bay {location.site_name}")
                            else:
                                # Pure ground site - starship shouldn't deploy here
                                action.add_reasoning("STARSHIP TO GROUND - invalid!", -500.0)
                                logger.warning(f"⚠️  Starship {deploying_card.title} cannot deploy to ground site {location.site_name}")

                        # Vehicles are fine at ground locations but not space
                        if is_vehicle and not is_starship:
                            if not location.is_space:
                                action.add_reasoning("Vehicle to ground location - good", +10.0)
                            else:
                                action.add_reasoning("Vehicle to space - check if valid", 0.0)

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

        Includes specific logic ported from C# BotAIHelper.ParseArbritraryCardDecision:
        - Main Power Generators priority
        - Massassi Throne Room priority
        - "This Deal Is Getting Worse" deck detection
        - "Slip Sliding Away" deck detection
        - Priority defensive shields list
        """
        actions = []
        text_lower = context.decision_text.lower()
        is_setup = "starting location" in text_lower or context.turn_number <= 1

        # Track if we're playing defensive shields
        is_playing_shields = False

        # Priority defensive shields (from C# logic)
        PRIORITY_SHIELDS = [
            "aim high",
            "secret plans",
            "allegations of corruption",
            "come here you big coward",
            "goldenrod",
            "simple tricks and nonsense",
            "tragedy has occurred",
        ]

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
                    title_lower = card_metadata.title.lower()

                    # === SETUP LOGIC (ported from C#) ===
                    if is_setup:
                        # Main Power Generators is always best starting location
                        if "main power generators" in title_lower:
                            action.add_reasoning("Main Power Generators - ideal start", +500.0)

                        # Massassi Throne Room priority
                        if "massassi throne room" in title_lower:
                            action.add_reasoning("Massassi Throne Room priority", +400.0)

                        # Slip Sliding Away bonus (blueprint 212_4) - prefer 2 dark icon sites
                        # that aren't Imperial Square or Palace
                        if blueprint == "212_4" or "slip sliding away" in title_lower:
                            action.add_reasoning("Slip Sliding Away card", +300.0)

                        # For decks with "This Deal Is Getting Worse" - prefer 2+ dark icon locations
                        if card_metadata.dark_side_icons >= 2:
                            action.add_reasoning(f"{card_metadata.dark_side_icons} dark icons", +50.0)

                        # Prefer sites that aren't Imperial Square or Palace for SSA decks
                        if (card_metadata.subtype and "site" in card_metadata.subtype.lower() and
                            card_metadata.dark_side_icons == 2):
                            if "imperial square" not in title_lower and "palace" not in title_lower:
                                action.add_reasoning("Good 2-icon site for deck", +30.0)

                    # === DEFENSIVE SHIELD LOGIC ===
                    if card_metadata.is_defensive_shield:
                        is_playing_shields = True
                        # Check if it's a priority shield
                        for shield_name in PRIORITY_SHIELDS:
                            if shield_name in title_lower:
                                action.add_reasoning(f"Priority shield: {shield_name}", +100.0)
                                break
                        else:
                            # Not a priority shield - lower score
                            action.add_reasoning("Non-priority defensive shield", +20.0)

                    # === RESERVE DECK DEPLOY LOGIC ===
                    if "reserve deck" in text_lower:
                        # Prefer locations and defensive shields
                        if card_metadata.is_location:
                            action.add_reasoning("Location card from Reserve", +10.0)
                        elif card_metadata.is_defensive_shield:
                            action.add_reasoning("Defensive Shield from Reserve", +5.0)

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
        # CRITICAL: Without a pilot, these have 0 POWER - essentially useless!
        if card.is_starship or card.is_vehicle:
            if not card.has_permanent_pilot:
                # Check if we have space locations (for starships)
                if card.is_starship and board_state:
                    has_space = any(loc.is_space for loc in board_state.locations)
                    if not has_space:
                        score += -999.0  # veryBadActionDelta - no space to deploy
                        return score

                # Unpiloted vehicle/starship has 0 POWER without a pilot!
                # Only deploy if we have a pilot we can deploy this turn
                if board_state:
                    available_force = board_state.force_pile - card.deploy_value
                    if self._have_pilot_in_hand(board_state, available_force):
                        # We can afford to deploy both vehicle AND pilot this turn
                        # Give moderate bonus based on the card's stats when piloted
                        piloted_power = card.maneuver or card.armor or "3"  # Estimate power contribution
                        try:
                            power_estimate = int(piloted_power) if piloted_power.isdigit() else 3
                        except:
                            power_estimate = 3
                        score += 10.0 + power_estimate * 2
                        logger.debug(f"{card.title}: unpiloted but have pilot in hand (+{10 + power_estimate * 2})")
                    else:
                        # NO PILOT AVAILABLE - deploying this is a waste!
                        # 0 power means it contributes nothing to battles
                        score += -200.0  # Strong penalty - don't deploy 0 power cards!
                        logger.debug(f"{card.title}: NO PILOT - would have 0 power! (-200)")
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
        """
        Check if we have warriors/starships/vehicles on the board without weapons.

        Weapons can be attached to:
        - Warriors (characters)
        - Starships (e.g., X-wing Laser Cannon on X-wing)
        - Vehicles
        """
        for card in board_state.cards_in_play.values():
            if card.owner == board_state.my_player_name:
                metadata = get_card(card.blueprint_id)
                if metadata:
                    # Check warriors, starships, and vehicles
                    can_have_weapon = (
                        metadata.is_warrior or
                        metadata.is_starship or
                        metadata.is_vehicle
                    )
                    if can_have_weapon:
                        # Check if card has no attached weapons
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

        Key principle: Don't throw characters into lost battles!
        - FREE BATTLEGROUND (no enemy) = safe drain, high priority
        - CONTESTABLE (can win/tie) = worth fighting for
        - LOSING BADLY (can't catch up) = avoid, deploy elsewhere

        Factors:
        - Force drain potential (opponent force icons = we can drain there)
        - GameStrategy location priority (if available)
        - Power differential and whether we can realistically contest
        - Free battleground bonus (no enemy presence)
        - Threat level (avoid dangerous locations)
        """
        score = 0.0
        loc_name = location.site_name or location.system_name or str(location.location_index)

        # Calculate power differential
        my_power = board_state.my_power_at_location(location.location_index)
        their_power = board_state.their_power_at_location(location.location_index)
        power_diff = my_power - their_power

        # Check if opponent has presence
        opponent_has_presence = len(location.their_cards) > 0 or their_power > 0

        # IMPORTANT: Force drain potential - locations where opponent has force icons
        # In SWCCG, you can only drain at locations where YOU have presence AND
        # there are OPPONENT force icons. Prioritize these locations!
        their_icons = location.their_icons or ""
        can_drain = False
        icon_count = 0
        if their_icons and their_icons != "0":
            # Parse icon count (could be "2" or "*2" format)
            try:
                icon_count = int(their_icons.replace("*", "").strip() or "0")
            except ValueError:
                icon_count = 1 if their_icons else 0
            can_drain = icon_count > 0

        # =====================================================================
        # FREE BATTLEGROUND: No enemy presence
        # Good for establishing presence and draining, but don't pile on!
        # =====================================================================
        if not opponent_has_presence:
            if my_power == 0:
                # Empty location - worth establishing presence
                if can_drain:
                    # Can drain here - high priority to establish!
                    score += 80.0 + (icon_count * 10.0)
                    logger.debug(f"Location {loc_name}: +{80 + icon_count * 10:.1f} (establish presence, can drain {icon_count})")
                else:
                    # No drain but still worth having presence
                    score += 15.0
                    logger.debug(f"Location {loc_name}: +15.0 (establish presence)")
            elif my_power < 4:
                # Light presence - might want to reinforce slightly
                if can_drain:
                    score += 30.0
                    logger.debug(f"Location {loc_name}: +30.0 (light presence {my_power}, can drain)")
                else:
                    score += 5.0
                    logger.debug(f"Location {loc_name}: +5.0 (light presence, no drain)")
            else:
                # We already have solid presence (4+ power) with no enemy
                # DON'T pile on - deploy elsewhere!
                overkill_penalty = -30.0 - (my_power - 4) * 5
                score += overkill_penalty
                logger.debug(f"Location {loc_name}: {overkill_penalty:.1f} (OVERKILL - already {my_power} power, no enemy!)")

            # Use GameStrategy priority for free locations
            if game_strategy:
                priority = game_strategy.get_location_priority(location.location_index)
                if priority:
                    score += priority.score * 0.3
            return score

        # =====================================================================
        # CONTESTED LOCATION: Enemy has presence
        # Only deploy here if we can reasonably win or tie the battle!
        # =====================================================================

        # Force drain bonus (if we have presence and can drain)
        if can_drain and my_power > 0:
            drain_bonus = 10.0 + (icon_count * 3.0)
            score += drain_bonus
            logger.debug(f"Location {loc_name}: +{drain_bonus:.1f} (can drain {icon_count})")

        # Use GameStrategy location priority if available
        if game_strategy:
            priority = game_strategy.get_location_priority(location.location_index)
            if priority:
                score += priority.score * 0.3
                logger.debug(f"Location {loc_name}: priority {priority.score:.1f}")

                # Extra penalty for dangerous/retreat locations
                if priority.threat_level == ThreatLevel.DANGEROUS:
                    score -= 20.0
                    logger.debug(f"Location {loc_name}: -20 (dangerous)")
                elif priority.threat_level == ThreatLevel.RETREAT:
                    score -= 40.0
                    logger.debug(f"Location {loc_name}: -40 (retreat)")

        # =====================================================================
        # POWER DIFFERENTIAL SCORING
        # Key insight: Don't reinforce lost causes! If we can't win, deploy elsewhere.
        # =====================================================================

        if power_diff >= 8:
            # OVERKILL: We have overwhelming control, deploy elsewhere
            overkill_penalty = -50.0 - (power_diff - 8) * 3
            score += overkill_penalty
            logger.debug(f"Location {loc_name}: {overkill_penalty:.1f} (overkill +{power_diff})")

        elif power_diff >= 4:
            # Comfortable lead - less need to reinforce
            score -= 25.0
            logger.debug(f"Location {loc_name}: -25.0 (comfortable lead +{power_diff})")

        elif power_diff >= 0:
            # WINNING or TIE - good to maintain/extend lead
            score += 20.0 + power_diff * 2
            logger.debug(f"Location {loc_name}: +{20 + power_diff * 2:.1f} (winning/tie by {power_diff})")

        elif power_diff >= -4:
            # CLOSE CONTEST (-1 to -4): We might catch up with deployment
            # This is worth fighting for!
            score += 25.0 + (4 + power_diff) * 3  # +25 at -4, +37 at -1
            logger.debug(f"Location {loc_name}: +{25 + (4 + power_diff) * 3:.1f} (close contest, losing by {abs(power_diff)})")

        else:
            # LOSING BADLY (-5 or worse): Check if we can actually catch up this turn!
            # Calculate: we'd need to deploy (their_power - my_power + 1) power to win
            power_needed_to_win = their_power - my_power + 1

            # Calculate total deployable power from hand this turn
            deployable_power = self._calculate_deployable_power(board_state, location)
            logger.debug(f"Location {loc_name}: need {power_needed_to_win} power to win, have {deployable_power} deployable")

            if deployable_power >= power_needed_to_win:
                # We CAN catch up this turn! This is actually a GOOD play.
                catch_up_bonus = 30.0 + (deployable_power - power_needed_to_win) * 3
                score += catch_up_bonus
                logger.debug(f"Location {loc_name}: +{catch_up_bonus:.1f} (CAN WIN - {deployable_power} power deployable vs {power_needed_to_win} needed)")
            elif deployable_power >= power_needed_to_win - 2:
                # Close - might be worth trying if we draw well or get destiny
                score += 10.0
                logger.debug(f"Location {loc_name}: +10.0 (close to catching up)")
            elif power_needed_to_win <= 6:
                # Might be able to catch up with a strong character
                catch_up_bonus = 15.0 - (power_needed_to_win * 2)  # +3 at need 6, +13 at need 1
                score += catch_up_bonus
                logger.debug(f"Location {loc_name}: +{catch_up_bonus:.1f} (might catch up, need {power_needed_to_win} power)")
            else:
                # Need too much power - this is a lost cause
                lost_cause_penalty = -40.0 - (power_needed_to_win - 6) * 5
                score += lost_cause_penalty
                logger.debug(f"Location {loc_name}: {lost_cause_penalty:.1f} (LOST CAUSE - need {power_needed_to_win} power, only {deployable_power} available)")

        return score

    def _calculate_deployable_power(self, board_state, location) -> int:
        """
        Calculate total power we could deploy to this location this turn.

        Considers:
        - Cards in hand that can deploy to this location type
        - Available Force to pay deploy costs
        - Whether cards are characters/vehicles (ground) or starships (space)

        Returns estimated total deployable power.
        """
        if not board_state or not board_state.cards_in_hand:
            return 0

        available_force = board_state.force_pile
        total_power = 0

        # Sort hand by power-to-cost ratio (best value first)
        deployable_cards = []
        for card in board_state.cards_in_hand:
            metadata = get_card(card.blueprint_id)
            if not metadata:
                continue

            # Check if card can deploy to this location type
            can_deploy_here = False
            if location.is_space and not location.is_ground:
                # Pure space - only starships
                can_deploy_here = metadata.is_starship
            elif location.is_ground:
                # Ground or docking bay - characters, vehicles, (starships at 0 power)
                can_deploy_here = metadata.is_character or metadata.is_vehicle
                # Don't count starships at docking bays - they have 0 power there
            else:
                # Default - assume characters/vehicles can deploy
                can_deploy_here = metadata.is_character or metadata.is_vehicle

            if can_deploy_here and metadata.deploy_value > 0:
                deployable_cards.append({
                    'power': metadata.power_value or 0,
                    'cost': metadata.deploy_value,
                    'name': metadata.title
                })

        # Sort by power (highest first) to maximize power deployed
        deployable_cards.sort(key=lambda x: x['power'], reverse=True)

        # "Deploy" cards until we run out of Force
        remaining_force = available_force
        for card in deployable_cards:
            if card['cost'] <= remaining_force:
                total_power += card['power']
                remaining_force -= card['cost']
                logger.debug(f"  Could deploy {card['name']} (power {card['power']}, cost {card['cost']})")

        return total_power

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

    def _card_has_pilot(self, card, card_meta) -> bool:
        """
        Check if a vehicle/starship already has a pilot.

        A vehicle/starship is piloted if:
        1. It has the permanent pilot icon, OR
        2. It has a pilot character attached/aboard

        Args:
            card: The card in play (from board_state.cards_in_play)
            card_meta: The card metadata (from card_loader)

        Returns:
            True if the card has a pilot
        """
        # Check for permanent pilot
        if card_meta and card_meta.has_permanent_pilot:
            return True

        # Check for attached pilot characters
        if hasattr(card, 'attached_cards'):
            for attached in card.attached_cards:
                attached_meta = get_card(attached.blueprint_id)
                if attached_meta and attached_meta.is_pilot:
                    return True

        return False

    def _has_unpiloted_vehicle_at_location(self, board_state, location) -> bool:
        """
        Check if we have any unpiloted vehicles/starships at a location.

        Args:
            board_state: Current board state
            location: The location to check

        Returns:
            True if we have an unpiloted vehicle/starship there
        """
        for card in location.my_cards:
            card_meta = get_card(card.blueprint_id)
            if card_meta and (card_meta.is_vehicle or card_meta.is_starship):
                if not self._card_has_pilot(card, card_meta):
                    return True
        return False
