"""
Draw Evaluator

Handles card draw decisions.
Ported from C# AICACHandler.cs RankDrawAction

Decision factors:
- Hand size (target ~7-8 cards, soft cap 12, hard cap 16)
- Reserve deck status (don't deck out)
- Force pile available
- Strategy (deploy vs hold)
- Force generation deficit (draw to find locations if low gen)
- Future turn planning (save force for expensive cards)
- Late-game life force preservation
"""

import logging
from typing import List, Optional
from .base import ActionEvaluator, DecisionContext, EvaluatedAction, ActionType
from ..game_strategy import GameStrategy, HAND_SOFT_CAP, HAND_HARD_CAP
from ..strategy_profile import get_current_profile, StrategyMode
from ..strategy_config import get_config

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIG-DRIVEN PARAMETERS
# =============================================================================

def _get_draw_config(key: str, default):
    """Get draw strategy config value."""
    return get_config().get('draw_strategy', key, default)

def _get_weight(key: str, default: float) -> float:
    """Get evaluator weight."""
    return get_config().get_weight('draw', key, default)

# Rank deltas
def get_very_good_delta() -> float:
    return _get_weight('very_good_delta', 150.0)

def get_good_delta() -> float:
    return _get_weight('good_delta', 10.0)

def get_bad_delta() -> float:
    return _get_weight('bad_delta', -10.0)

def get_very_bad_delta() -> float:
    return _get_weight('very_bad_delta', -150.0)

# Draw thresholds
def get_target_hand_size() -> int:
    return _get_draw_config('target_hand_size', 7)

def get_low_reserve_threshold() -> int:
    return _get_draw_config('low_reserve_threshold', 6)

def get_small_hand_threshold() -> int:
    return _get_draw_config('small_hand_threshold', 5)

def get_aggressive_force_threshold() -> int:
    return _get_draw_config('aggressive_force_threshold', 10)

def get_deck_size_for_full_hand() -> int:
    return _get_draw_config('deck_size_for_full_hand', 12)

def get_force_reserve_turn_threshold() -> int:
    return _get_draw_config('force_reserve_turn_threshold', 4)

def get_small_hand_for_reserve() -> int:
    return _get_draw_config('small_hand_for_reserve', 6)

def get_late_game_life_force() -> int:
    return _get_draw_config('late_game_life_force', 12)

def get_critical_life_force() -> int:
    return _get_draw_config('critical_life_force', 6)

def get_expensive_card_threshold() -> int:
    return _get_draw_config('expensive_card_threshold', 8)

# Inline thresholds
def get_hold_back_draw_force_threshold() -> int:
    return _get_draw_config('hold_back_draw_force_threshold', 6)

def get_hold_back_draw_life_threshold() -> int:
    return _get_draw_config('hold_back_draw_life_threshold', 10)

def get_hold_back_draw_force_floor() -> int:
    return _get_draw_config('hold_back_draw_force_floor', 6)

def get_force_starved_activation() -> int:
    return _get_draw_config('force_starved_activation', 8)

def get_force_starved_power_threshold() -> int:
    return _get_draw_config('force_starved_power_threshold', 6)

def get_force_starved_max_hand() -> int:
    return _get_draw_config('force_starved_max_hand', 8)

# Keep MAX_HAND_SIZE as it comes from GameStrategy
MAX_HAND_SIZE = HAND_HARD_CAP


class DrawEvaluator(ActionEvaluator):
    """
    Evaluates card draw decisions.

    Considers:
    - Current hand size (soft cap 12, hard cap 16)
    - Reserve deck status
    - Force availability
    - Current strategy
    - Force generation deficit (draw to find locations)
    """

    def __init__(self):
        super().__init__("Draw")

    def _get_game_strategy(self, context: DecisionContext) -> Optional[GameStrategy]:
        """Get GameStrategy from board_state's strategy_controller"""
        if context.board_state and context.board_state.strategy_controller:
            return context.board_state.strategy_controller.game_strategy
        return None

    def can_evaluate(self, context: DecisionContext) -> bool:
        """
        Handle CARD_ACTION_CHOICE with draw actions.

        STRICT REQUIREMENT: Only evaluate during OUR turn AND Draw phase.
        "Draw destiny" is NOT drawing cards - it's a random number mechanic.
        This evaluator is ONLY for the Draw phase decision to draw cards from deck.
        """
        if context.decision_type not in ['CARD_ACTION_CHOICE', 'ACTION_CHOICE']:
            return False

        # CRITICAL: Only evaluate during OUR turn
        if context.board_state and not context.board_state.is_my_turn():
            logger.debug(f"🎴 DrawEvaluator skipping - not our turn")
            return False

        # CRITICAL: Only evaluate during Draw phase
        # "Draw destiny" happens in other phases and is NOT drawing cards!
        phase = context.phase or ""
        if "draw" not in phase.lower():
            logger.debug(f"🎴 DrawEvaluator skipping - not draw phase (phase={phase})")
            return False

        # Must be our turn AND draw phase - now check for draw actions
        decision_lower = (context.decision_text or "").lower()
        if "draw" in decision_lower and "action" in decision_lower:
            logger.info(f"🎴 DrawEvaluator triggered (our turn, draw phase): '{context.decision_text}'")
            return True

        # Also check if any action is a draw action
        for action_text in context.action_texts:
            action_lower = action_text.lower()
            # Match "Draw" but not "Draw destiny" (destiny is random number, not card draw)
            if "draw" in action_lower and "destiny" not in action_lower:
                logger.info(f"🎴 DrawEvaluator triggered by action: '{action_text}'")
                return True

        return False

    def evaluate(self, context: DecisionContext) -> List[EvaluatedAction]:
        """Evaluate draw card options"""
        actions = []
        bs = context.board_state
        game_strategy = self._get_game_strategy(context)

        for i, action_id in enumerate(context.action_ids):
            action_text = context.action_texts[i] if i < len(context.action_texts) else ""

            # Flexible matching for draw actions (case-insensitive)
            # Match any action containing "draw" (e.g., "Draw", "Draw card", "Draw card into hand from Force Pile")
            # BUT exclude "draw destiny" - that's a random number mechanic, not card drawing!
            action_lower = action_text.lower()
            if "draw" not in action_lower:
                continue
            if "destiny" in action_lower:
                # "Draw destiny to X" is NOT drawing cards - skip it
                logger.debug(f"🎴 Skipping destiny draw action: '{action_text}'")
                continue

            logger.info(f"🎴 Evaluating draw action: '{action_text}' (id={action_id})")

            action = EvaluatedAction(
                action_id=action_id,
                action_type=ActionType.DRAW,
                score=0.0,
                display_text=action_text
            )

            if bs:
                self._rank_draw_action(action, bs, game_strategy)
            else:
                # No board state - be neutral
                action.add_reasoning("No board state - neutral draw", 0.0)

            actions.append(action)

        return actions

    def _rank_draw_action(self, action: EvaluatedAction, board_state,
                          game_strategy: Optional[GameStrategy] = None):
        """
        Rank the draw action based on game state.

        Ported from C# AICACHandler.RankDrawAction
        Enhanced with:
        - GameStrategy hand size caps and force gen awareness
        - Future turn planning (save force for expensive cards)
        - Late-game life force preservation
        - Dynamic strategy profiles for game-state-aware decisions

        CRITICAL: When life force is low, reduce max hand size proportionally.
        Having 12 cards in hand but only 2 force to spend is TERRIBLE.
        """
        from ..card_loader import get_card

        # === STRATEGY PROFILE ADJUSTMENT ===
        # Modify draw preference based on game position
        profile = get_current_profile(board_state)
        draw_multiplier = profile.draw_multiplier

        if profile.mode == StrategyMode.DESPERATION:
            action.add_reasoning("DESPERATION: Need action, not cards!", -10.0)
        elif profile.mode == StrategyMode.AGGRESSIVE:
            action.add_reasoning("AGGRESSIVE: Less drawing, more deploying", -5.0)
        elif profile.mode == StrategyMode.DEFENSIVE:
            action.add_reasoning("DEFENSIVE: Build hand for destiny draws", 8.0)
        elif profile.mode == StrategyMode.CRUSHING:
            action.add_reasoning("CRUSHING: Draw for better destiny", 12.0)

        # === STRATEGIC STATE DRAW MODE ===
        # When strategic state detects we're missing critical card types or
        # losing the drain war, MAJOR boost to drawing to find answers.
        # This is a categorical change, not a marginal adjustment.
        strategic_state = getattr(board_state, 'strategic_state', None)
        if strategic_state and strategic_state.enabled:
            # FORCE DRAW MODE: Missing critical card types
            if strategic_state.force_draw_mode:
                missing_type = strategic_state.inventory.missing_critical_type
                if missing_type:
                    # Major boost - overwhelms most penalties except life force critical
                    draw_boost = 100.0
                    action.add_reasoning(
                        f"STRATEGIC DRAW: Missing {missing_type} - must find options!",
                        draw_boost
                    )
                    logger.info(f"STRATEGIC DRAW MODE: +{draw_boost} (missing {missing_type})")

            # DRAIN CRISIS: Losing drain war and getting worse
            trajectory = strategic_state.trajectory
            if (trajectory.drain_gap_trend == "worsening" and
                trajectory.turns_at_negative >= 2):
                # We're losing and it's getting worse - draw for answers!
                urgency_boost = 50.0 + (trajectory.turns_at_negative * 15)
                action.add_reasoning(
                    f"DRAIN CRISIS: Gap {trajectory.current_drain_gap:+d}, "
                    f"worsening for {trajectory.turns_at_negative} turns - draw for answers!",
                    urgency_boost
                )
                logger.info(f"DRAIN CRISIS DRAW: +{urgency_boost} "
                           f"(gap {trajectory.current_drain_gap:+d})")
        hand_size = board_state.hand_size if hasattr(board_state, 'hand_size') else 7
        reserve_deck = board_state.reserve_deck if hasattr(board_state, 'reserve_deck') else 20
        used_pile = board_state.used_pile if hasattr(board_state, 'used_pile') else 0
        force_pile = board_state.force_pile if hasattr(board_state, 'force_pile') else 5
        turn_number = board_state.turn_number if hasattr(board_state, 'turn_number') else 1

        # Total reserve force (old method) - just reserve deck
        reserve = board_state.total_reserve_force() if hasattr(board_state, 'total_reserve_force') else reserve_deck

        # === CRITICAL: LIFE FORCE BASED HAND LIMIT ===
        # Total remaining life force = cards that can still circulate
        # (reserve deck + used pile + force pile)
        # Hand cards are STUCK until played, so don't count them
        remaining_life_force = reserve_deck + used_pile + force_pile

        # Get force generation for future turn planning
        force_generation = 1  # Default: we generate 1 ourselves
        if game_strategy:
            force_generation = game_strategy.my_force_generation
        elif hasattr(board_state, 'my_force_generation'):
            force_generation = board_state.my_force_generation

        # === LATE GAME LIFE FORCE PRESERVATION ===
        # When life force is critically low, be very conservative
        if remaining_life_force < get_critical_life_force():
            action.add_reasoning(
                f"CRITICAL life force ({remaining_life_force}) - minimize draws",
                get_very_bad_delta() * 0.8  # Strong penalty but not absolute
            )
            # Still allow draws if hand is truly empty
            if hand_size >= 2:
                return

        # Late game - be more strategic
        if remaining_life_force < get_late_game_life_force():
            # Penalty scales with how low life force is
            penalty_scale = (get_late_game_life_force() - remaining_life_force) / get_late_game_life_force()
            action.add_reasoning(
                f"Late game ({remaining_life_force} life force) - draw carefully",
                get_bad_delta() * 2 * penalty_scale
            )

        # When remaining life force drops below MAX_HAND_SIZE (16),
        # reduce max hand proportionally.
        effective_max_hand = MAX_HAND_SIZE
        if remaining_life_force < MAX_HAND_SIZE:
            effective_max_hand = max(2, remaining_life_force)  # Floor at 2
            logger.debug(f"Life force {remaining_life_force} < {MAX_HAND_SIZE}: effective max hand = {effective_max_hand}")

        # If hand already exceeds effective max, STRONGLY penalize drawing
        if hand_size >= effective_max_hand:
            penalty = get_very_bad_delta()  # -999 to strongly discourage
            action.add_reasoning(
                f"CRITICAL: Hand {hand_size} >= life force limit {effective_max_hand} (only {remaining_life_force} cards left!)",
                penalty
            )
            return

        # === NON-STRATEGIC HOLD-BACK: DRAW TO FIND OPTIONS ===
        # If we held back this turn because we COULDN'T deploy (not because we're
        # saving for a strategic play), draw aggressively to find new options.
        # This prevents the bot from sitting with a big hand of undeployable cards
        # while having plenty of force.
        #
        # Conditions for aggressive draw:
        # 1. Deploy planner held back due to inability (not strategic save)
        # 2. Force pile is decent (>6) - we have resources to spare
        # 3. Life force is decent (>10) - not in survival mode
        # 4. Hand is below hard cap
        hold_back_force_threshold = get_hold_back_draw_force_threshold()
        hold_back_life_threshold = get_hold_back_draw_life_threshold()
        hold_back_force_floor = get_hold_back_draw_force_floor()

        if hasattr(board_state, 'deploy_planner') and board_state.deploy_planner:
            planner = board_state.deploy_planner
            if planner.current_plan and planner.should_hold_back():
                hold_reason = planner.current_plan.reason or ""
                hold_reason_lower = hold_reason.lower()

                # Check if this is a NON-STRATEGIC hold (couldn't deploy vs choosing not to)
                # Strategic holds mention: "crush", "bleed", "early game", "saving"
                is_strategic_hold = any(keyword in hold_reason_lower for keyword in
                                       ['crush', 'bleed', 'early game', 'saving', 'next-turn'])

                if not is_strategic_hold:
                    # This is a "couldn't deploy" hold - check if we should draw
                    if (force_pile > hold_back_force_threshold and
                        remaining_life_force > hold_back_life_threshold and
                        hand_size < MAX_HAND_SIZE):

                        # Calculate how many draws we can afford while keeping force floor
                        draws_affordable = force_pile - hold_back_force_floor

                        if draws_affordable > 0:
                            # Boost drawing significantly - we need new options!
                            draw_boost = 50.0 + (draws_affordable * 5)  # Strong base + bonus per affordable draw
                            action.add_reasoning(
                                f"HOLD-BACK DRAW: Couldn't deploy ({hold_reason[:50]}...), "
                                f"force {force_pile} > {hold_back_force_threshold}, "
                                f"drawing to find options (up to {draws_affordable} draws)",
                                draw_boost
                            )
                            logger.info(f"🎴 HOLD-BACK DRAW boost: +{draw_boost} (reason: {hold_reason[:60]})")

        # === NEXT-TURN CRUSH PLAN AWARENESS ===
        # If we're holding back for a next-turn crush, limit drawing to preserve force
        # The crush plan tells us exactly how much force we need next turn
        if hasattr(board_state, 'next_turn_crush_plan') and board_state.next_turn_crush_plan:
            crush_plan = board_state.next_turn_crush_plan
            # Calculate max force we can spend on draws while still affording crush next turn
            max_draw_force = crush_plan.get_max_draw_force(force_pile)

            logger.info(f"🔮 Next-turn crush plan active: need {crush_plan.force_needed} force, "
                       f"expect {crush_plan.expected_force_next_turn}, can spend {max_draw_force} on draws")

            if max_draw_force <= 0:
                # Can't afford to draw at all - need to save every bit of force!
                action.add_reasoning(
                    f"SAVING FOR NEXT-TURN CRUSH: Need {crush_plan.force_needed} force for "
                    f"{', '.join(crush_plan.card_names)} → {crush_plan.target_location_name}",
                    get_very_bad_delta() * 0.9  # Strong penalty but allow if hand is truly empty
                )
                if hand_size >= 3:  # If we have at least 3 cards, definitely don't draw
                    return
            else:
                # Can afford some drawing, but penalize to encourage saving
                action.add_reasoning(
                    f"Next-turn crush: can spend up to {max_draw_force} on draws",
                    get_bad_delta()  # Moderate penalty - encourage saving but don't block
                )

        # === FUTURE TURN PLANNING: EXPENSIVE CARDS ===
        # Check if we have expensive cards worth saving force for
        max_deployable_cost = 0
        affordable_cards_count = 0
        expensive_card_in_hand = False

        if hasattr(board_state, 'cards_in_hand'):
            for card in board_state.cards_in_hand:
                if card.blueprint_id:
                    metadata = get_card(card.blueprint_id)
                    if metadata and metadata.deploy_value:
                        deploy_cost = metadata.deploy_value
                        max_deployable_cost = max(max_deployable_cost, deploy_cost)
                        if deploy_cost >= get_expensive_card_threshold():
                            expensive_card_in_hand = True
                        if force_pile >= deploy_cost:
                            affordable_cards_count += 1

        # If we have expensive cards (Executor costs 15+), save force across turns
        if expensive_card_in_hand and max_deployable_cost > force_pile:
            # We need to accumulate force - don't draw!
            force_deficit = max_deployable_cost - force_pile
            turns_to_save = (force_deficit + force_generation - 1) // max(1, force_generation)

            # Only save if it's achievable (within ~3 turns)
            if turns_to_save <= 3 and remaining_life_force >= max_deployable_cost:
                action.add_reasoning(
                    f"Saving for expensive card (cost {max_deployable_cost}, need {force_deficit} more, ~{turns_to_save} turns)",
                    get_bad_delta() * 2
                )

        # If we have stuff to deploy but couldn't afford it, save force
        if affordable_cards_count == 0 and hand_size > 3 and force_pile < 6:
            action.add_reasoning(
                f"No affordable cards (hand {hand_size}, force {force_pile}) - save force for next turn",
                get_bad_delta() * 1.5
            )

        # === FORCE-STARVED STRATEGY ===
        # When activation is low (< 8/turn), hoarding cards is counterproductive.
        # If we already have enough power in hand, stop drawing and SAVE force
        # for next turn's deployment.
        #
        # Key insight: Forward-looking planning
        # - Calculate: next_turn_force = current_force + activation
        # - If next_turn_force < deploy_cost_for_6_power + 2, stop drawing!
        force_starved_activation = get_force_starved_activation()
        force_starved_power_threshold = get_force_starved_power_threshold()
        force_starved_max_hand = get_force_starved_max_hand()

        if force_generation < force_starved_activation:
            # We're force-starved! Check if we have enough deployable power
            deployable_power = 0
            min_cost_for_threshold_power = 999
            power_cost_pairs = []  # Track (power, cost) for efficient combo finding

            if hasattr(board_state, 'cards_in_hand'):
                for card in board_state.cards_in_hand:
                    if card.blueprint_id:
                        metadata = get_card(card.blueprint_id)
                        if metadata:
                            card_power = metadata.power_value or 0
                            card_cost = metadata.deploy_value or 0
                            # Only count characters/starships with power
                            if card_power > 0 and card_cost > 0:
                                deployable_power += card_power
                                power_cost_pairs.append((card_power, card_cost))

                # Find minimum cost to reach 6 power threshold
                # Sort by efficiency (power/cost ratio descending)
                power_cost_pairs.sort(key=lambda x: x[0]/x[1] if x[1] > 0 else 0, reverse=True)

                cumulative_power = 0
                cumulative_cost = 0
                for power, cost in power_cost_pairs:
                    cumulative_power += power
                    cumulative_cost += cost
                    if cumulative_power >= force_starved_power_threshold:
                        min_cost_for_threshold_power = cumulative_cost
                        break

            # If we have 6+ deployable power, apply force-starved logic
            if deployable_power >= force_starved_power_threshold:
                # Forward-looking: can we afford to deploy next turn?
                next_turn_force = force_pile + force_generation
                force_needed = min_cost_for_threshold_power + 2  # +2 buffer for reactions

                logger.info(f"🎴 FORCE-STARVED check: activation={force_generation}, "
                           f"deployable_power={deployable_power}, min_cost={min_cost_for_threshold_power}, "
                           f"next_turn_force={next_turn_force}, need={force_needed}")

                if next_turn_force < force_needed:
                    # We WON'T have enough force next turn - stop drawing!
                    shortfall = force_needed - next_turn_force
                    action.add_reasoning(
                        f"FORCE-STARVED: Save force! ({deployable_power}p ready, need {force_needed} force, "
                        f"will have {next_turn_force} → short {shortfall})",
                        get_very_bad_delta() * 0.6  # Strong penalty but not absolute
                    )
                    logger.warning(f"🎴 FORCE-STARVED: Stopping draw to save force for deployment")

                    # If hand already has 6+ cards, make the penalty even stronger
                    if hand_size >= 6:
                        action.add_reasoning(
                            f"Already have {hand_size} cards - more won't help without force",
                            get_bad_delta() * 2
                        )
                        return  # Exit early - definitely don't draw

                # Even if we CAN afford next turn, don't over-draw when force-starved
                if hand_size >= force_starved_max_hand:
                    action.add_reasoning(
                        f"Force-starved ({force_generation}/turn): hand {hand_size} is enough",
                        get_bad_delta() * 3
                    )

        # Determine if we have deployable cards (for dynamic soft cap)
        has_deployable_cards = affordable_cards_count > 0 or max_deployable_cost > 0

        # === BASELINE: DRAW TOWARDS DYNAMIC SOFT CAP ===
        # Real players overdraw early game (16 cards turn 1-3) to find key cards,
        # then tighten up late game (8 cards turn 7+) to preserve life force.
        # Determine game phase for logging
        phase_note = "early" if turn_number <= 3 else ("mid" if turn_number <= 6 else "late")

        # Get dynamic soft cap from game strategy if available
        if game_strategy:
            effective_soft_cap = game_strategy.get_effective_soft_cap(has_deployable_cards)
        else:
            # Fallback: simple turn-based adjustment
            if turn_number <= 3:
                effective_soft_cap = HAND_SOFT_CAP + 4  # 16
            elif turn_number <= 6:
                effective_soft_cap = HAND_SOFT_CAP  # 12
            else:
                effective_soft_cap = HAND_SOFT_CAP - 4  # 8
            # If no deployable cards, allow extra drawing
            if not has_deployable_cards:
                effective_soft_cap += 2

        if hand_size < effective_soft_cap and remaining_life_force >= get_late_game_life_force():
            # Bonus scales with how far below cap we are
            cards_below_cap = effective_soft_cap - hand_size
            # Use exponential scaling: smaller hands get MUCH stronger bonus
            # This ensures drawing beats Pass even after force-saving penalties
            baseline_bonus = 8.0 * cards_below_cap
            # Minimum of 30 to beat Pass (5) + typical penalties (-25)
            baseline_bonus = max(30.0, baseline_bonus)
            action.add_reasoning(
                f"Hand {hand_size} below {phase_note}-game cap {effective_soft_cap} - draw!",
                baseline_bonus
            )
            logger.info(f"🎴 Draw baseline: hand {hand_size} < {phase_note} cap {effective_soft_cap}, +{baseline_bonus}")

        # === FORCE RESERVATION FOR OPPONENT'S TURN ===
        # After turn 4, keep some force for reactions/battles
        # Also check if we have cards on contested locations
        force_to_reserve = 1 if hand_size < get_small_hand_for_reserve() else 2

        # Reserve more force if we have presence at contested locations
        if hasattr(board_state, 'locations') and board_state.locations:
            contested_locations = sum(
                1 for loc in board_state.locations
                if loc and hasattr(loc, 'both_present') and loc.both_present
            )
            if contested_locations > 0:
                force_to_reserve = max(force_to_reserve, 2 + contested_locations)

        if turn_number >= get_force_reserve_turn_threshold():
            if force_pile <= force_to_reserve:
                action.add_reasoning(
                    f"Turn {turn_number}: reserve {force_to_reserve} force for reactions/battles",
                    get_bad_delta() * 1.5
                )

        # C# Logic 1: Don't draw if low reserve (avoid decking)
        if reserve <= get_low_reserve_threshold():
            penalty = get_bad_delta() * (get_low_reserve_threshold() - reserve)
            action.add_reasoning(f"Low reserve ({reserve}) - avoid drawing", penalty)

        # C# Logic 2: Draw if hand is smaller than target and enough reserve
        # But only if we're not in late game conservation mode
        if hand_size < get_target_hand_size() and reserve > 10 and force_pile > 1:
            if remaining_life_force >= get_late_game_life_force():
                action.add_reasoning(f"Hand size {hand_size} < {get_target_hand_size()} - draw to fill", get_good_delta())

        # C# Logic 3: Draw if hand is very small (even in late game, need options)
        if hand_size <= get_small_hand_threshold() and reserve > 4 and force_pile > 1:
            action.add_reasoning(f"Small hand ({hand_size}) - draw cards", get_good_delta())

        # C# Logic 4: Aggressive draw only if we have good life force
        if force_pile > get_aggressive_force_threshold() and remaining_life_force >= get_late_game_life_force():
            action.add_reasoning(f"High force pile ({force_pile}) - YOLO draw", get_good_delta())

        # C# Logic 5: On Hold strategy but hand is weak, still draw
        if force_pile > 5 and hand_size <= 4:
            action.add_reasoning("Weak hand - draw even on hold", get_good_delta())

        # Strategic hand size management (dynamic soft cap based on game phase)
        if game_strategy:
            # Apply GameStrategy hand size penalty (uses dynamic cap)
            hand_penalty = game_strategy.get_hand_size_penalty(hand_size, has_deployable_cards)
            if hand_penalty < 0:
                action.add_reasoning(f"Hand size {hand_size} above {phase_note}-game cap", hand_penalty)

            # Force generation deficit - draw to find locations
            # But only if we're not critically low on life force
            if remaining_life_force >= get_late_game_life_force():
                if game_strategy.should_prioritize_drawing_for_locations(hand_size):
                    action.add_reasoning(f"Low force gen ({game_strategy.my_force_generation}) - draw for locations", get_good_delta())
        else:
            # Fallback: Use local effective_soft_cap calculation
            if hand_size >= effective_max_hand:
                overflow = hand_size - effective_max_hand
                action.add_reasoning(f"Hand full ({hand_size}/{effective_max_hand}) - avoid drawing", get_bad_delta() * overflow)
            elif hand_size >= effective_soft_cap:
                overflow = hand_size - effective_soft_cap
                action.add_reasoning(f"Hand above {phase_note}-game cap ({hand_size}/{effective_soft_cap})", get_bad_delta() * overflow * 0.5)

        # C# Logic 7: Save last force
        if force_pile == 1:
            action.add_reasoning("Last force - save it", get_bad_delta())
