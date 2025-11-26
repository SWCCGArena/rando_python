# Evaluator System - Decision Engine

## Overview

The evaluator system is a **modular, testable, and extensible** framework for making strategic decisions in SWCCG. Instead of nested if/else statements, each decision is scored by one or more evaluators, and the highest-scored action is chosen.

## Architecture

```
Decision XML → DecisionContext → Evaluators → EvaluatedActions → Best Action
```

### Core Components

1. **ActionEvaluator** - Base class for all evaluators
2. **DecisionContext** - Game state + decision info
3. **EvaluatedAction** - A scored action with reasoning
4. **CombinedEvaluator** - Runs multiple evaluators and picks best

### Data Flow

```python
# 1. Parse decision from XML
decision_element = <ge decisionType="CARD_ACTION_CHOICE" ...>

# 2. Build context
context = DecisionContext(
    board_state=board_state,
    decision_type="CARD_ACTION_CHOICE",
    action_ids=["0", "1", "2"],
    action_texts=["Deploy Vader", "Deploy Luke", "Pass"]
)

# 3. Run evaluators
deploy_eval = DeployEvaluator()
actions = deploy_eval.evaluate(context)
# Returns: [
#   EvaluatedAction(id="0", score=75.0, text="Deploy Vader"),
#   EvaluatedAction(id="1", score=60.0, text="Deploy Luke"),
#   EvaluatedAction(id="2", score=10.0, text="Pass")
# ]

# 4. Pick best
best = max(actions, key=lambda a: a.score)
# Returns: "0" (Deploy Vader)
```

---

## Implemented Evaluators

### 1. DeployEvaluator (`engine/evaluators/deploy_evaluator.py`)

Handles deploy phase decisions.

**Applies to:**
- `CARD_ACTION_CHOICE` - "Choose Deploy action or Pass"
- `CARD_SELECTION` - "Choose where to deploy X"
- `ARBITRARY_CARDS` - Card selection for deployment

**Scoring Factors:**
- **Card efficiency**: (Power + Ability) / Deploy Cost
- **Unique bonus**: +5 for unique characters
- **High power bonus**: +10 for Power ≥ 5
- **High ability bonus**: +8 for Ability ≥ 4
- **Resource awareness**: Penalize expensive cards when low on Force
- **Strategic positioning**: Deploy where we need power

**Example Scores:**
```python
# Darth Vader (Power 6, Ability 6, Deploy 6)
# Efficiency: (6+6)/6 = 2.0 * 5 = 10.0
# + Unique: +5
# + High Power: +10
# + High Ability: +8
# + Character: +5
# = 38.0 base score

# If we're behind on power by 8:
# + Power adjustment: 6 * 2 = +12
# = 50.0 total
```

### 2. PassEvaluator (`engine/evaluators/base.py`)

Creates a PASS/cancel action.

**Applies to:**
- Any decision where `noPass=false`

**Scoring Factors:**
- Base score: 5.0 (low default)
- +5 if Force < 3 (conserve resources)
- +3 if Reserve Deck < 14 (running low)

**When it wins:**
- All other actions score < 10
- We're too low on resources to do anything useful

---

## Creating New Evaluators

### Step 1: Create Evaluator Class

```python
# engine/evaluators/battle_evaluator.py

from .base import ActionEvaluator, DecisionContext, EvaluatedAction, ActionType

class BattleEvaluator(ActionEvaluator):
    """Evaluates battle initiation decisions"""

    def __init__(self):
        super().__init__("Battle")

    def can_evaluate(self, context: DecisionContext) -> bool:
        """Check if this is a battle decision"""
        return (
            context.phase and "battle" in context.phase.lower()
        ) or (
            "initiate battle" in context.decision_text.lower()
        )

    def evaluate(self, context: DecisionContext) -> List[EvaluatedAction]:
        """Score battle actions"""
        actions = []
        bs = context.board_state

        for i, action_id in enumerate(context.action_ids):
            action_text = context.action_texts[i] if i < len(context.action_texts) else ""

            action = EvaluatedAction(
                action_id=action_id,
                action_type=ActionType.BATTLE,
                score=50.0,
                display_text=action_text
            )

            # Extract location info from action
            location_index = self._extract_location(action_text)

            if location_index >= 0:
                my_power = bs.my_power_at_location(location_index)
                their_power = bs.their_power_at_location(location_index)

                # Only initiate if we have power advantage
                if my_power > their_power:
                    power_diff = my_power - their_power
                    action.add_reasoning(f"Power advantage: {my_power} vs {their_power}", power_diff * 10.0)
                else:
                    action.add_reasoning(f"Power disadvantage: {my_power} vs {their_power}", -50.0)

            actions.append(action)

        return actions

    def _extract_location(self, text: str) -> int:
        """Extract location index from action text"""
        # Implementation depends on text format
        return -1
```

### Step 2: Register in `__init__.py`

```python
# engine/evaluators/__init__.py

from .battle_evaluator import BattleEvaluator

__all__ = [
    'ActionEvaluator',
    'DecisionContext',
    'EvaluatedAction',
    'DeployEvaluator',
    'BattleEvaluator',  # Add new evaluator
]
```

### Step 3: Add to DecisionHandler

```python
# engine/decision_handler.py

# In _use_evaluators():
evaluators = [
    DeployEvaluator(),
    BattleEvaluator(),  # Add here
    PassEvaluator(),
]
```

**That's it!** The evaluator will now run automatically for applicable decisions.

---

## Porting C# Logic

The Unity C# bot has extensive ranking logic in files like:
- `AICACHandler.cs` - Card Action Choice ranking (~600 lines)
- `AICSHandler.cs` - Card Selection ranking (~400 lines)
- `AIStrategyController.cs` - High-level strategy

### Porting Strategy

**1. Identify Decision Type**

C# code groups logic by decision type:
```csharp
// AICACHandler.cs line 390
if (decisionType == "CARD_ACTION_CHOICE") {
    // Deploy logic
    // Battle logic
    // Move logic
}
```

**Map to Evaluator:**
- Deploy logic → `DeployEvaluator`
- Battle logic → `BattleEvaluator`
- Move logic → `MoveEvaluator`

**2. Extract Scoring Logic**

C# uses a ranking system:
```csharp
// AICACHandler.cs line 450
int rank = 50; // Base rank

// Add bonuses
if (card.isPowerfulCharacter()) rank += 20;
if (needMorePower) rank += 10;

// Penalize
if (card.deployCost > availableForce) rank -= 100;
```

**Translate to Python:**
```python
def _score_card_deployment(self, card, board_state) -> float:
    score = 50.0  # Base score

    # Bonuses
    if card.power_value >= 5:
        score += 20.0

    if board_state.power_advantage() < 0:
        score += 10.0

    # Penalties
    if card.deploy_value > board_state.force_pile:
        score -= 100.0

    return score
```

**3. Port Incrementally**

Don't try to port everything at once:

1. **Start with common cases:**
   - Deploy high-power characters
   - Avoid expensive cards when low on Force
   - Basic battle initiation

2. **Add edge cases:**
   - Specific card interactions
   - Special phase logic
   - Defensive shields

3. **Test iteratively:**
   - Play a game, observe decisions
   - Check logs for reasoning
   - Adjust scoring as needed

---

## Testing Evaluators

### Unit Testing

```python
# tests/test_deploy_evaluator.py

import pytest
from engine.evaluators import DeployEvaluator, DecisionContext, EvaluatedAction
from engine.board_state import BoardState

def test_deploy_high_power_character():
    # Setup
    board_state = BoardState(my_player_name="test")
    board_state.force_pile = 10

    context = DecisionContext(
        board_state=board_state,
        decision_type="CARD_ACTION_CHOICE",
        decision_text="Choose Deploy action",
        decision_id="1",
        phase="Deploy (turn #1)",
        turn_number=1,
        is_my_turn=True,
        action_ids=["0", "1"],
        action_texts=[
            "Deploy <div value='108_6'>Vader</div>",  # Power 6, Deploy 6
            "Pass"
        ]
    )

    # Run evaluator
    evaluator = DeployEvaluator()
    actions = evaluator.evaluate(context)

    # Verify
    vader_action = next(a for a in actions if "Vader" in a.display_text)
    pass_action = next(a for a in actions if "Pass" in a.display_text)

    assert vader_action.score > pass_action.score, "Should prefer deploying Vader"
    assert vader_action.score > 40.0, "Vader should score highly"
```

### Integration Testing

Run a game and check logs:

```bash
tail -f logs/rando.log | grep "🎯"
```

Look for:
```
🎯 Using evaluator system for decision...
🔍 Running evaluator: Deploy
  [Deploy] Deploy Vader: 50.0 - Card: Vader | High Power: +10 | Character: +5
  [Deploy] Pass: 5.0 - Default pass option
✅ Best action: Deploy Vader (score: 50.0)
   Reasoning: Card: Vader | High Power: +10 | Character: +5
```

### Debugging

If evaluator chooses wrong action:

1. **Check logs** - What was the score? What reasoning was applied?
2. **Verify context** - Did board_state have correct data?
3. **Adjust weights** - Increase/decrease bonuses
4. **Add missing logic** - Maybe a factor wasn't considered

---

## Extending Evaluators

### Adding Rules

Add new scoring logic to existing evaluators:

```python
# In DeployEvaluator._score_card_deployment():

# New rule: Prefer characters with matching pilot
if card.characteristics and "matching pilot" in card.characteristics:
    score += 15.0
    action.add_reasoning("Has matching pilot available", +15.0)
```

### Composing Evaluators

Multiple evaluators can score the same action:

```python
# Both DeployEvaluator and LocationEvaluator might run
evaluators = [
    DeployEvaluator(),      # Scores card value
    LocationEvaluator(),    # Scores where to deploy
    PassEvaluator(),        # Always creates pass option
]

# Each produces scored actions
# Best action wins
```

### Conditional Logic

Only apply rules in specific contexts:

```python
def evaluate(self, context: DecisionContext) -> List[EvaluatedAction]:
    actions = []

    # Only apply special logic during setup
    if context.turn_number <= 2:
        actions = self._evaluate_setup(context)
    else:
        actions = self._evaluate_normal(context)

    return actions
```

---

## Best Practices

### 1. Keep Evaluators Focused

Each evaluator should handle ONE type of decision:
- ✅ `DeployEvaluator` - Deploy phase only
- ✅ `BattleEvaluator` - Battle initiation only
- ❌ `MegaEvaluator` - Everything (too complex!)

### 2. Use Reasoning Strings

Always explain scores:
```python
# Good
action.add_reasoning("High power character", +10.0)

# Bad
action.score += 10.0  # No explanation
```

### 3. Scale Scores Meaningfully

- **0-20**: Low priority (pass, cancel)
- **20-50**: Moderate priority (decent actions)
- **50-80**: High priority (good strategic moves)
- **80+**: Critical priority (game-winning moves)
- **-100**: Never do this (illegal, terrible moves)

### 4. Test Edge Cases

Consider:
- What if Force is 0?
- What if no locations exist?
- What if hand is empty?
- What if opponent has no cards?

### 5. Log Liberally

Use debug logging to understand evaluations:
```python
self.logger.debug(f"Evaluating {len(context.action_ids)} actions")
self.logger.debug(f"Board state: Force={bs.force_pile}, Power={bs.total_my_power()}")
```

---

## Next Steps

### Immediate (Phase 5B)

1. **Test DeployEvaluator** - Play games, verify it makes good choices
2. **Tune Scoring** - Adjust weights based on game results
3. **Add BattleEvaluator** - Basic battle initiation logic
4. **Add MoveEvaluator** - Basic movement decisions

### Short Term

1. **Port C# Ranking Rules** - Systematically convert existing logic
2. **Add ForceActivationEvaluator** - How much Force to activate
3. **Add CardSelectionEvaluator** - General card picking logic
4. **Handle Special Cards** - Defensive shields, interrupts, etc.

### Long Term

1. **Machine Learning** - Train evaluator weights on game outcomes
2. **Opponent Modeling** - Track opponent patterns, adjust strategy
3. **Lookahead Search** - Evaluate future game states (minimax)
4. **Card-Specific Logic** - Per-card strategies (Vader, Luke, etc.)

---

## Files Created

| File | Purpose | Lines |
|------|---------|-------|
| `engine/evaluators/__init__.py` | Package initialization | 15 |
| `engine/evaluators/base.py` | Base classes and framework | 250 |
| `engine/evaluators/deploy_evaluator.py` | Deploy phase evaluator | 250 |
| `engine/decision_handler.py` (modified) | Integration with evaluators | +75 |
| `app.py` (modified) | Pass board_state to handler | +2 |

**Total:** ~600 lines of evaluator framework

---

## Success Criteria

✅ **Evaluator system integrated** - DecisionHandler uses evaluators
✅ **DeployEvaluator working** - Scores deploy actions based on card value
✅ **Reasoning logged** - Can see why bot chose each action
✅ **Extensible design** - Easy to add new evaluators
✅ **Fallback working** - Old heuristics used if evaluators fail

**Next: Test in live game and tune scoring!** 🚀
