"""
Deploy Phase Evaluator

Evaluates deployment decisions:
- Which cards to deploy from hand
- Where to deploy them (location selection)
- Whether to activate Force instead

Includes strategic improvements:
- Phase-level deployment planning (hold back vs deploy, target locations)
- Location priority scoring from GameStrategy
- Force generation deficit bonus for locations
- Cross-turn focus bonus for matching card types

Ported from Unity C# AICACHandler.cs and AICSHandler.cs
"""

from typing import List, Optional
import logging
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from .base import ActionEvaluator, DecisionContext, EvaluatedAction, ActionType
from ..card_loader import get_card
from ..game_strategy import GameStrategy, ThreatLevel
from ..deploy_planner import DeployPhasePlanner, DeployStrategy
from config import config

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
        # Phase-level planner for strategic deployment decisions
        self.planner = DeployPhasePlanner(deploy_threshold=config.DEPLOY_THRESHOLD)

    def reset_pending_deploys(self):
        """Reset pending deploy tracking (call at turn start)"""
        self.pending_deploy_card_ids.clear()

    def reset_for_new_game(self):
        """Reset all state for a new game"""
        self.pending_deploy_card_ids.clear()
        self._last_turn_number = -1
        self.planner.reset()

    def track_deploy(self, card_id: str):
        """Track that we tried deploying this card"""
        self.pending_deploy_card_ids.add(card_id)

    def _get_game_strategy(self, context: DecisionContext) -> Optional[GameStrategy]:
        """Get GameStrategy from board_state's strategy_controller"""
        if context.board_state and context.board_state.strategy_controller:
            return context.board_state.strategy_controller.game_strategy
        return None

    def _find_pilots_in_hand(self, bs) -> List[tuple]:
        """
        Find all pilot characters in hand.

        Returns list of (card_in_hand, card_metadata) tuples for pilots.
        """
        pilots = []
        if not bs or not bs.cards_in_hand:
            return pilots

        for card in bs.cards_in_hand:
            if card.blueprint_id:
                metadata = get_card(card.blueprint_id)
                if metadata and metadata.is_character and metadata.is_pilot:
                    pilots.append((card, metadata))

        return pilots

    def _can_pilot_ship(self, pilot_metadata, ship_metadata) -> bool:
        """
        Check if a pilot can pilot a specific ship/vehicle.

        In SWCCG:
        - Any character with Pilot icon can pilot any compatible ship
        - Matching pilots (Luke + Red 5) can deploy for free aboard matching ship
        - For simplicity, we assume any pilot can pilot any ship for now
        """
        if not pilot_metadata or not ship_metadata:
            return False

        # Must be a pilot character
        if not pilot_metadata.is_pilot:
            return False

        # Ship must be a starship or vehicle
        if not (ship_metadata.is_starship or ship_metadata.is_vehicle):
            return False

        # TODO: Add matching pilot logic for reduced deploy costs
        return True

    def _check_can_deploy_with_pilot(self, ship_metadata, bs) -> tuple:
        """
        Check if we can deploy a starship/vehicle AND a pilot for it.

        Returns:
            (can_deploy_with_pilot, pilot_metadata, total_cost, reason)
        """
        if not bs:
            return (False, None, 0, "No board state")

        # Check if ship needs a pilot
        if ship_metadata.has_permanent_pilot:
            return (True, None, ship_metadata.deploy_value, "Ship has permanent pilot")

        # Find available pilots in hand
        pilots_in_hand = self._find_pilots_in_hand(bs)
        if not pilots_in_hand:
            return (False, None, ship_metadata.deploy_value, "No pilots in hand!")

        # Find the cheapest compatible pilot
        cheapest_pilot = None
        cheapest_cost = float('inf')

        for card, pilot_meta in pilots_in_hand:
            if self._can_pilot_ship(pilot_meta, ship_metadata):
                pilot_cost = pilot_meta.deploy_value or 0
                if pilot_cost < cheapest_cost:
                    cheapest_cost = pilot_cost
                    cheapest_pilot = pilot_meta

        if not cheapest_pilot:
            return (False, None, ship_metadata.deploy_value, "No compatible pilots in hand!")

        # Calculate total cost
        ship_cost = ship_metadata.deploy_value or 0
        total_cost = ship_cost + cheapest_cost

        # Check if we can afford both
        if bs.force_pile >= total_cost:
            return (True, cheapest_pilot, total_cost,
                    f"Can afford ship ({ship_cost}) + pilot {cheapest_pilot.title} ({cheapest_cost})")
        else:
            return (False, cheapest_pilot, total_cost,
                    f"Can't afford ship ({ship_cost}) + pilot ({cheapest_cost}) = {total_cost}, have {bs.force_pile}")

    def _find_unpiloted_ship_on_board(self, bs) -> Optional[tuple]:
        """
        Find any unpiloted starship/vehicle on the board.

        Used to prioritize pilot deployment.

        Returns:
            (card_id, card_title, card_type) if found, None otherwise
            card_type is "starship" or "vehicle"
        """
        if not bs:
            return None

        logger.debug(f"Checking for unpiloted ships/vehicles. My player: {bs.my_player_name}, cards_in_play: {len(bs.cards_in_play)}")

        for card_id, card in bs.cards_in_play.items():
            # Log all our cards for debugging
            if card.owner == bs.my_player_name:
                logger.debug(f"  Our card #{card_id}: {card.card_title or 'no title'} (blueprint={card.blueprint_id})")

            if card.owner != bs.my_player_name:
                continue

            # Skip placeholder blueprints (hidden cards we can't see)
            # Server sends -1_1, -1_2 etc. for face-down/hidden opponent cards
            if card.blueprint_id and card.blueprint_id.startswith('-1_'):
                continue

            metadata = get_card(card.blueprint_id) if card.blueprint_id else None
            if not metadata:
                # Log cards with missing metadata - could be tracking issue
                # (but not for hidden cards which we already filtered above)
                logger.warning(f"  ⚠️  Card #{card_id} has no metadata! blueprint={card.blueprint_id}, title={card.card_title}")
                continue

            if metadata.is_starship or metadata.is_vehicle:
                card_type = "starship" if metadata.is_starship else "vehicle"

                # Check if it has permanent pilot
                if metadata.has_permanent_pilot:
                    logger.debug(f"  ✓ {card_type.title()} {metadata.title} (#{card_id}): has permanent pilot - piloted")
                    continue

                # Check if it has a pilot aboard (attached)
                has_pilot_aboard = False
                pilot_name = None
                for attached in card.attached_cards:
                    attached_meta = get_card(attached.blueprint_id) if attached.blueprint_id else None
                    if attached_meta and attached_meta.is_pilot:
                        has_pilot_aboard = True
                        pilot_name = attached_meta.title
                        break

                if has_pilot_aboard:
                    logger.debug(f"  ✓ {card_type.title()} {metadata.title} (#{card_id}): has pilot aboard ({pilot_name}) - piloted")
                    continue

                # This ship/vehicle is unpiloted!
                logger.warning(f"⚠️  Found UNPILOTED {card_type}: {metadata.title} (#{card_id}, blueprint={card.blueprint_id})")
                logger.warning(f"    type={metadata.card_type}, is_starship={metadata.is_starship}, is_vehicle={metadata.is_vehicle}")
                logger.warning(f"    has_permanent_pilot={metadata.has_permanent_pilot}, icons={metadata.icons}")
                logger.warning(f"    attached_cards={len(card.attached_cards)}")
                return (card_id, metadata.title, card_type)

        logger.debug("  No unpiloted ships/vehicles found")
        return None

    def _has_unpiloted_ship_on_board(self, bs) -> bool:
        """Check if we have any unpiloted starship/vehicle on the board."""
        return self._find_unpiloted_ship_on_board(bs) is not None

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

        Uses DeployPhasePlanner to make phase-level strategic decisions:
        - Creates a deployment plan at the start of each deploy phase
        - If plan is HOLD_BACK, all deploy actions get strong penalty
        - Target locations get bonuses based on the plan
        """
        actions = []
        bs = context.board_state

        # Reset pending deploy tracking at the start of each turn
        if context.turn_number != self._last_turn_number:
            self.reset_pending_deploys()
            self._last_turn_number = context.turn_number

        # === PHASE-LEVEL PLANNING ===
        # Create or retrieve the deployment plan for this phase
        # This makes one strategic decision for the whole phase instead of per-card
        deploy_plan = None
        if bs:
            deploy_plan = self.planner.create_plan(bs)
            logger.info(f"📋 Deploy plan: {deploy_plan.strategy.value} - {deploy_plan.reason}")

            # Store plan on board_state so other evaluators can access it
            # (e.g., card_selection_evaluator needs to know target locations)
            bs.current_deploy_plan = deploy_plan

            # Store plan summary on board_state for admin UI display
            bs.deploy_plan_summary = self.planner.get_plan_summary()

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

            # === APPLY PHASE-LEVEL PLAN ===
            # If the planner decided to HOLD BACK, penalize ALL deploy actions
            # This ensures we don't deploy piecemeal when we should save up
            if deploy_plan and deploy_plan.strategy == DeployStrategy.HOLD_BACK:
                action.add_reasoning(f"HOLD BACK: {deploy_plan.reason}", -500.0)
                actions.append(action)
                continue  # Skip individual card evaluation - plan says don't deploy

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
                # Try to get card metadata
                blueprint_id = self._extract_blueprint_from_action(action_text)
                card_metadata = None

                if blueprint_id:
                    card_metadata = get_card(blueprint_id)

                # Fallback: Use cardId to look up card
                if not card_metadata and card_id and bs:
                    tracked_card = bs.cards_in_play.get(card_id)
                    if tracked_card and tracked_card.blueprint_id:
                        blueprint_id = tracked_card.blueprint_id
                        card_metadata = get_card(blueprint_id)

                if card_metadata:
                    action.card_name = card_metadata.title
                    action.deploy_cost = card_metadata.deploy_value

                    # =======================================================
                    # USE THE PLANNER'S DECISION - NO SECOND GUESSING!
                    # The planner already figured out the optimal deployment
                    # We just check if this card is in the plan
                    # =======================================================
                    if deploy_plan and blueprint_id:
                        plan_score, plan_reason = self.planner.get_card_score(blueprint_id)
                        action.add_reasoning(plan_reason, plan_score)

                        # If card is in plan, that's all we need
                        if plan_score > 0:
                            logger.info(f"✅ {card_metadata.title} IN PLAN: {plan_reason}")
                        else:
                            logger.info(f"❌ {card_metadata.title} NOT in plan: {plan_reason}")
                    else:
                        # No plan or no blueprint - use basic scoring
                        if card_metadata.is_location:
                            action.add_reasoning("LOCATION - deploy first!", +200.0)
                        else:
                            action.add_reasoning(f"Card: {card_metadata.title}", 0.0)

                    # Always check affordability
                    if bs and bs.force_pile < card_metadata.deploy_value:
                        action.add_reasoning(f"Can't afford! Need {card_metadata.deploy_value}, have {bs.force_pile}", -1000.0)
                else:
                    logger.warning(f"⚠️  Deploy action with unknown card: cardId={card_id}")
                    action.add_reasoning(f"Deploy action (card unknown)", -50.0)

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
        - FOLLOW THE DEPLOY PLAN if one exists!
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

        # Check card type being deployed
        is_starship = deploying_card and deploying_card.is_starship
        is_vehicle = deploying_card and deploying_card.is_vehicle
        is_weapon = deploying_card and deploying_card.is_weapon
        is_device = deploying_card and deploying_card.is_device
        is_pilot = deploying_card and deploying_card.is_pilot
        is_droid = deploying_card and deploying_card.is_droid
        provides_presence = deploying_card and deploying_card.provides_presence

        if deploying_card:
            logger.debug(f"Deploying {deploying_card.title}: starship={is_starship}, vehicle={is_vehicle}, weapon={is_weapon}, pilot={is_pilot}, droid={is_droid}")

        # Each card_id represents a target where we can deploy (location or card)
        for card_id in context.card_ids:
            action = EvaluatedAction(
                action_id=card_id,
                action_type=ActionType.SELECT_CARD,
                score=50.0,  # Base score
                display_text=f"Deploy to (card {card_id})"
            )

            # =====================================================
            # FOLLOW THE DEPLOY PLAN!
            # If planner specified a target, give big bonus to it
            # =====================================================
            if planned_target_id:
                if card_id == planned_target_id:
                    action.add_reasoning(f"PLANNED TARGET: {planned_target_name}", +200.0)
                    logger.info(f"✅ Card {card_id} is the PLANNED target (+200)")
                else:
                    action.add_reasoning(f"Not planned target (want {planned_target_name})", -100.0)

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

                        # Vehicles are fine at EXTERIOR ground locations but not space or interior
                        if is_vehicle and not is_starship:
                            if not location.is_space:
                                # Check if location has exterior icon - vehicles need exterior
                                loc_metadata = get_card(location.blueprint_id) if location.blueprint_id else None
                                has_exterior = loc_metadata.is_exterior if loc_metadata else True  # Default to True if unknown
                                has_interior_only = loc_metadata.is_interior and not has_exterior if loc_metadata else False

                                if has_interior_only:
                                    action.add_reasoning("VEHICLE TO INTERIOR-ONLY - can't deploy!", -500.0)
                                    logger.warning(f"⚠️  Vehicle {deploying_card.title} cannot deploy to interior site {location.site_name}")
                                elif has_exterior:
                                    action.add_reasoning("Vehicle to exterior ground - good", +10.0)
                                else:
                                    action.add_reasoning("Vehicle to ground location", +5.0)
                            else:
                                action.add_reasoning("VEHICLE TO SPACE - invalid!", -500.0)
                                logger.warning(f"⚠️  Vehicle {deploying_card.title} cannot deploy to space location {location.site_name or location.system_name}")

                        # CRITICAL: Droids (ability=0) don't provide presence!
                        # Without presence you can't prevent force drains or initiate battles.
                        # Deploying a droid alone to "counter" an opponent is useless.
                        if is_droid and not provides_presence:
                            # Check if we have existing presence at this location
                            we_have_presence = self._have_presence_at_location(bs, location)
                            opponent_has_presence = len(location.their_cards) > 0

                            if not we_have_presence:
                                if opponent_has_presence:
                                    # Opponent has presence, we don't - droid can't counter them!
                                    action.add_reasoning("DROID ALONE vs OPPONENT - can't counter drains/battles!", -100.0)
                                    logger.warning(f"⚠️  {deploying_card.title} (droid) alone can't counter opponent at {location.site_name}")
                                else:
                                    # Empty location - droid alone still can't control or prevent drains
                                    action.add_reasoning("Droid alone - no presence to control location", -30.0)

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
                        if (card_metadata.sub_type and "site" in card_metadata.sub_type.lower() and
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

    def _score_card_deployment(self, card, board_state, game_strategy: Optional[GameStrategy] = None,
                                card_id: str = "") -> float:
        """
        Score a card for deployment based on strategic value.

        Ported from C# AICACHandler.cs RankDeployAction logic.

        The scoring follows a clear priority order:
        1. Locations - always deploy first (creates options)
        2. Creatures - always deploy (special rules)
        3. Weapons/Devices - only if we have targets
        4. Pilots for unpiloted ships - high priority
        5. Ships/Vehicles - only with pilot available
        6. Characters - only if we meet power threshold

        Args:
            card: Card metadata from card_loader
            board_state: Current board state
            game_strategy: Optional game strategy for bonuses
            card_id: The card's game instance ID

        Returns a score where:
        - Positive = good to deploy
        - Negative = avoid deploying
        - Very negative (-999) = definitely don't deploy

        NOTE: Threshold check is done by DeployPhasePlanner at phase start.
        This method assumes the planner already approved deploying.
        """
        score = 0.0

        # =================================================================
        # TIER 1: ALWAYS DEPLOY (no threshold check needed)
        # =================================================================

        # Locations are always highest priority - they create new options
        if card.is_location:
            score = 999.0
            if game_strategy:
                location_bonus = game_strategy.get_location_deploy_bonus()
                if location_bonus > 0:
                    score += location_bonus
                    logger.debug(f"Location {card.title}: +{location_bonus:.1f} (force gen deficit)")
            return score

        # Creatures have special rules, always deploy
        if card.card_type == "Creature":
            return 999.0

        # =================================================================
        # TIER 2: EQUIPMENT (deploy if we have valid targets)
        # =================================================================

        if card.is_weapon or card.is_device:
            if board_state and self._have_empty_warriors(board_state):
                return 10.0  # Have targets without weapons
            else:
                return -10.0  # No valid targets

        # =================================================================
        # TIER 3: PILOTS - Check for unpiloted ships to prioritize
        # =================================================================

        if card.is_pilot and card.is_character and board_state:
            unpiloted = self._find_unpiloted_ship_on_board(board_state)
            if unpiloted:
                unpiloted_id, unpiloted_name, unpiloted_type = unpiloted
                # NOTE: Threshold check is done by DeployPhasePlanner at phase start.
                # If we're here, the planner approved deploying, so prioritize pilots for unpiloted ships.
                logger.info(f"🎯 Prioritizing pilot {card.title} for {unpiloted_name}")
                return 200.0 + card.power_value * 5  # High priority

        # =================================================================
        # TIER 4: STARSHIPS/VEHICLES - Must have pilot available
        # NOTE: Threshold check is done by DeployPhasePlanner at phase start.
        # Here we only check for practical requirements (pilot, space location).
        # =================================================================

        if card.is_starship or card.is_vehicle:
            if not board_state:
                return 0.0

            # Ships with permanent pilot skip the pilot check
            if card.has_permanent_pilot:
                score = 10.0 + card.power_value * 3
                logger.debug(f"{card.title}: piloted ship - power={card.power_value}")
                return score

            # Unpiloted ship - need pilot to have any power
            # First: Do we have a space location for starships?
            if card.is_starship:
                has_pure_space = any(
                    loc.is_space and not getattr(loc, 'is_ground', False)
                    for loc in board_state.locations if loc
                )
                if not has_pure_space:
                    logger.debug(f"{card.title}: NO SPACE LOCATION available")
                    return -999.0

            # Check if we have a pilot we can afford
            available_force = board_state.force_pile - card.deploy_value
            if not self._have_pilot_in_hand(board_state, available_force):
                logger.debug(f"{card.title}: NO PILOT available (need cost <= {available_force})")
                return -200.0  # No pilot = 0 power = bad

            # Have pilot - good to deploy
            score = 10.0 + (card.power_value or 3) * 2
            logger.debug(f"{card.title}: unpiloted but have pilot available")
            return score

        # =================================================================
        # TIER 5: CHARACTERS - Score based on power value
        # NOTE: Threshold check is done ONCE by DeployPhasePlanner at phase start.
        # Individual cards are scored assuming planner already approved deploying.
        # =================================================================

        if card.is_character and board_state:
            # Identify "pure pilots" - low power pilots we should save for ships
            is_pure_pilot = False
            if card.is_pilot and not card.is_warrior and card.power_value <= 4:
                is_pure_pilot = True
            if card.is_warrior and card.is_pilot and card.power_value <= 3:
                is_pure_pilot = True

            # Check if we have enough force (reserve 1)
            force_after = board_state.force_pile - card.deploy_value
            if force_after < 1:
                logger.debug(f"{card.title}: would leave < 1 force")
                return -10.0

            # Score based on power - higher power = higher score
            score = 10.0 * card.power_value
            logger.debug(f"{card.title}: ground deploy - power={card.power_value}")

            # Penalize pure pilots - save them for ships when possible
            # Only penalize, don't block - if threshold is met, they can still deploy
            if is_pure_pilot:
                score -= 30.0
                logger.debug(f"{card.title}: pure pilot penalty -30 (saving for ships)")

            # Bonus for high stats
            if card.power_value >= 5:
                score += 10.0
            elif card.power_value >= 3:
                score += 5.0
            if card.ability_value >= 4:
                score += 8.0
            elif card.ability_value >= 2:
                score += 4.0

            # Strategic focus bonus
            if game_strategy and card.card_type:
                focus_bonus = game_strategy.get_focus_deploy_bonus(card.card_type)
                if focus_bonus > 0:
                    score += focus_bonus

            return score

        # =================================================================
        # FALLBACK: Unknown card type
        # =================================================================
        logger.debug(f"{card.title}: unknown card type, neutral score")
        return 0.0

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

        Priority order:
        1. BATTLE OPPORTUNITY: Enemy has weak presence (2-6 power), we can overpower
           and have Force to battle - go there and fight!
        2. CONTROLLABLE DRAIN: Empty location with opponent icons - we can control
           and drain. Prioritize by icon count.
        3. SUPPORT: We're losing at a location but can catch up with this deploy
        4. AVOID: Overkill locations, lost causes, no strategic value

        Key insight: Only deploy where we can CONTROL after deployment.
        """
        score = 0.0
        loc_name = location.site_name or location.system_name or str(location.location_index)

        # Get config thresholds
        overkill_threshold = config.DEPLOY_OVERKILL_THRESHOLD
        comfortable_threshold = config.DEPLOY_COMFORTABLE_THRESHOLD
        battle_force_reserve = config.BATTLE_FORCE_RESERVE

        # Calculate power differential
        my_power = board_state.my_power_at_location(location.location_index)
        their_power = board_state.their_power_at_location(location.location_index)
        power_diff = my_power - their_power

        # Check if opponent has presence
        opponent_has_presence = len(location.their_cards) > 0 or their_power > 0

        # Parse opponent force icons (for drain potential)
        their_icons = location.their_icons or ""
        icon_count = 0
        if their_icons and their_icons != "0":
            try:
                icon_count = int(their_icons.replace("*", "").strip() or "0")
            except ValueError:
                icon_count = 1 if their_icons else 0

        # Calculate deployable power to THIS location type
        deployable_power = self._calculate_deployable_power(board_state, location)

        # =====================================================================
        # SCENARIO 1: BATTLE OPPORTUNITY
        # Enemy has weak presence, we can overpower AND have Force to battle
        # This is HIGH PRIORITY - we can remove their presence!
        # =====================================================================
        if opponent_has_presence and their_power > 0:
            # Calculate what power we'd have after deploying everything we can
            potential_power = my_power + deployable_power
            potential_diff = potential_power - their_power

            # Check if we have Force to battle after deploying
            # (battle initiation costs 1 Force)
            force_available = board_state.force_pile
            deploy_cost = self._estimate_deploy_cost(board_state, location)
            force_after_deploy = force_available - deploy_cost
            can_battle = force_after_deploy >= battle_force_reserve

            # BATTLE OPPORTUNITY: Weak enemy (2-6 power), we can decisively overpower
            if their_power <= 6 and potential_diff >= 2 and can_battle:
                # Great opportunity! Deploy here, then battle to remove them
                battle_bonus = 60.0 + (potential_diff * 5) + (icon_count * 15)
                score += battle_bonus
                logger.info(f"⚔️ BATTLE OPPORTUNITY at {loc_name}: they have {their_power}, "
                           f"we can deploy to {potential_power} (+{potential_diff}), icons={icon_count}")

                # Extra bonus if this location has drain potential after we win
                if icon_count > 0:
                    score += 20.0 * icon_count
                    logger.debug(f"  +{20 * icon_count} for drain potential after battle")

                return score

            # We can overpower but maybe not battle (low Force)
            elif potential_diff >= 2 and not can_battle:
                score += 30.0 + potential_diff * 3
                logger.debug(f"Location {loc_name}: can overpower (+{potential_diff}) but no Force for battle")

            # Close contest - might be worth fighting for
            elif potential_diff >= -2:
                score += 20.0 + (2 + potential_diff) * 5
                logger.debug(f"Location {loc_name}: close contest (potential +{potential_diff})")

            # We CAN'T catch up - lost cause, deploy elsewhere
            else:
                lost_cause_penalty = -50.0 - abs(potential_diff) * 3
                score += lost_cause_penalty
                logger.debug(f"Location {loc_name}: {lost_cause_penalty:.1f} (LOST CAUSE - can only reach {potential_power} vs {their_power})")
                return score

        # =====================================================================
        # SCENARIO 2: CONTROLLABLE DRAIN (Empty location with opponent icons)
        # No enemy = we can control and drain safely
        # Prioritize by icon count (more icons = more drain damage)
        # =====================================================================
        if not opponent_has_presence:
            if my_power == 0:
                # Empty location - establish presence
                if icon_count > 0:
                    # DRAIN OPPORTUNITY - prioritize by icon count!
                    drain_bonus = 50.0 + (icon_count * 25.0)
                    score += drain_bonus
                    logger.info(f"🎯 DRAIN TARGET at {loc_name}: {icon_count} opponent icons, empty!")
                else:
                    # No drain potential but still worth presence
                    score += 10.0
                    logger.debug(f"Location {loc_name}: +10.0 (establish presence, no drain)")

            elif my_power < comfortable_threshold:
                # Light presence - reinforce if it has drain value
                if icon_count > 0:
                    score += 20.0 + (icon_count * 10.0)
                    logger.debug(f"Location {loc_name}: reinforce drain location ({icon_count} icons)")
                else:
                    score += 5.0

            else:
                # Already have solid presence with no enemy - OVERKILL
                overkill_penalty = -30.0 - (my_power - comfortable_threshold) * 5
                score += overkill_penalty
                logger.debug(f"Location {loc_name}: {overkill_penalty:.1f} (OVERKILL - {my_power} power, no enemy)")

            return score

        # =====================================================================
        # SCENARIO 3: ALREADY WINNING - Check for overkill
        # =====================================================================
        if power_diff >= overkill_threshold:
            overkill_penalty = -50.0 - (power_diff - overkill_threshold) * 3
            score += overkill_penalty
            logger.debug(f"Location {loc_name}: {overkill_penalty:.1f} (overkill +{power_diff})")

        elif power_diff >= comfortable_threshold:
            score -= 25.0
            logger.debug(f"Location {loc_name}: -25.0 (comfortable lead +{power_diff})")

        elif power_diff >= 0:
            # Winning or tie - might want to secure it
            score += 15.0 + power_diff * 2
            if icon_count > 0:
                score += icon_count * 5  # Bonus for drain potential
            logger.debug(f"Location {loc_name}: winning by {power_diff}, icons={icon_count}")

        # Use GameStrategy priority if available
        if game_strategy:
            priority = game_strategy.get_location_priority(location.location_index)
            if priority:
                score += priority.score * 0.2
                if priority.threat_level == ThreatLevel.DANGEROUS:
                    score -= 15.0
                elif priority.threat_level == ThreatLevel.RETREAT:
                    score -= 30.0

        return score

    def _calculate_deployable_power(self, board_state, location, reserve_for_battle: bool = True) -> int:
        """
        Calculate total power we could deploy to this location this turn.

        Considers:
        - Cards in hand that can deploy to this location type
        - Available Force to pay deploy costs
        - Reserve Force for battle initiation if needed
        - Whether cards are characters/vehicles (ground) or starships (space)

        Returns estimated total deployable power.
        """
        if not board_state or not board_state.cards_in_hand:
            return 0

        # Reserve Force for battle if requested
        battle_reserve = config.BATTLE_FORCE_RESERVE if reserve_for_battle else 0
        available_force = board_state.force_pile - battle_reserve
        if available_force <= 0:
            return 0

        total_power = 0

        # Collect deployable cards
        deployable_cards = []
        for card in board_state.cards_in_hand:
            metadata = get_card(card.blueprint_id)
            if not metadata:
                continue

            # Check if card can deploy to this location type
            can_deploy_here = False
            if location.is_space and not getattr(location, 'is_ground', False):
                # Pure space - only starships
                can_deploy_here = metadata.is_starship
            elif getattr(location, 'is_ground', True):
                # Ground or docking bay - characters can go anywhere, vehicles need exterior
                if metadata.is_character:
                    can_deploy_here = True
                elif metadata.is_vehicle:
                    # Check if location has exterior icon
                    loc_meta = get_card(location.blueprint_id) if location.blueprint_id else None
                    has_exterior = loc_meta.is_exterior if loc_meta else True
                    can_deploy_here = has_exterior
                # Don't count starships at docking bays - they have 0 power there
            else:
                # Default - assume characters can deploy
                can_deploy_here = metadata.is_character

            if can_deploy_here and metadata.deploy_value and metadata.deploy_value > 0:
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

        return total_power

    def _estimate_deploy_cost(self, board_state, location) -> int:
        """
        Estimate total Force cost to deploy everything we can to this location.

        Used to calculate how much Force we'd have left for battle.
        """
        if not board_state or not board_state.cards_in_hand:
            return 0

        available_force = board_state.force_pile
        total_cost = 0

        for card in board_state.cards_in_hand:
            metadata = get_card(card.blueprint_id)
            if not metadata:
                continue

            # Check if card can deploy to this location type
            can_deploy_here = False
            if location.is_space and not getattr(location, 'is_ground', False):
                can_deploy_here = metadata.is_starship
            elif getattr(location, 'is_ground', True):
                # Characters can go anywhere, vehicles need exterior
                if metadata.is_character:
                    can_deploy_here = True
                elif metadata.is_vehicle:
                    loc_meta = get_card(location.blueprint_id) if location.blueprint_id else None
                    has_exterior = loc_meta.is_exterior if loc_meta else True
                    can_deploy_here = has_exterior
            else:
                can_deploy_here = metadata.is_character

            if can_deploy_here and metadata.deploy_value and metadata.deploy_value > 0:
                if total_cost + metadata.deploy_value <= available_force:
                    total_cost += metadata.deploy_value

        return total_cost

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
        for card in location.my_cards:
            card_meta = get_card(card.blueprint_id)
            if card_meta and card_meta.provides_presence:
                return True
        return False
