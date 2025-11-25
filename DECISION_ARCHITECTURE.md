# Decision System Architecture

## Design Philosophy

**Goal**: Replace nested if/else chains with a modular, testable, extensible decision system.

## Core Architecture

```
Decision Request
       ↓
   Evaluator Pipeline
       ↓
   ┌─────────────────────────────────┐
   │  Strategy Evaluators            │
   │  - DeployEvaluator              │
   │  - BattleEvaluator              │
   │  - ForceActivationEvaluator     │
   │  - MoveEvaluator                │
   └─────────────────────────────────┘
       ↓
   Scoring System
   (Each action gets a score)
       ↓
   Best Action Selected
```

## Key Components

### 1. **Action Evaluator (Base Class)**

```python
class ActionEvaluator(ABC):
    """Base class for evaluating a specific action type"""

    @abstractmethod
    def can_evaluate(self, action: Action, context: DecisionContext) -> bool:
        """Check if this evaluator can handle this action"""
        pass

    @abstractmethod
    def evaluate(self, action: Action, context: DecisionContext) -> float:
        """Return a score for this action (0-100)"""
        pass

    @abstractmethod
    def get_reason(self) -> str:
        """Return human-readable reason for the score"""
        pass
```

### 2. **Decision Context**

```python
@dataclass
class DecisionContext:
    """All information needed to make a decision"""
    board_state: BoardState
    available_actions: List[Action]
    current_phase: str
    my_turn: bool

    # Calculated properties
    @property
    def power_advantage(self) -> int:
        return self.board_state.total_my_power() - self.board_state.total_their_power()

    @property
    def force_advantage(self) -> int:
        return self.board_state.force_pile - self.board_state.their_force_pile
```

### 3. **Action Model**

```python
@dataclass
class Action:
    """Represents a possible action"""
    action_id: str
    action_type: str  # "deploy", "battle", "move", "activate", etc.
    action_text: str

    # Type-specific data
    card_blueprint: Optional[str] = None
    target_location: Optional[int] = None
    cost: int = 0

    # Evaluation results
    score: float = 0.0
    reason: str = ""
```

### 4. **Specific Evaluators**

#### DeployEvaluator
```python
class DeployEvaluator(ActionEvaluator):
    """Evaluates deploy actions based on strategic value"""

    def evaluate(self, action: Action, context: DecisionContext) -> float:
        score = 50.0  # Base score

        # Rule: Don't deploy if low on Force
        if context.board_state.force_pile < 3:
            score -= 30
            self.reason = "Low on Force"

        # Rule: Deploy characters at weak locations
        if action.card_type == "character":
            weakest_loc = self._find_weakest_location(context)
            if action.target_location == weakest_loc:
                score += 20
                self.reason = "Reinforcing weak location"

        # Rule: Don't deploy if power advantage is high
        if context.power_advantage > 10:
            score -= 20
            self.reason = "Already winning, conserve Force"

        return score
```

#### ForceActivationEvaluator
```python
class ForceActivationEvaluator(ActionEvaluator):
    """Determines how much Force to activate"""

    def evaluate(self, action: Action, context: DecisionContext) -> float:
        # Strategy: Activate based on available Force and need
        available = context.board_state.force_pile

        # Activate more if behind on power
        if context.power_advantage < -5:
            return min(available, 4)  # Activate up to 4
        elif context.power_advantage < 0:
            return min(available, 3)  # Activate up to 3
        else:
            return min(available, 2)  # Conserve, activate 2
```

### 5. **Decision Engine**

```python
class DecisionEngine:
    """Main decision-making coordinator"""

    def __init__(self):
        self.evaluators = [
            DeployEvaluator(),
            BattleEvaluator(),
            ForceActivationEvaluator(),
            MoveEvaluator(),
        ]

    def make_decision(self, context: DecisionContext) -> Action:
        """Evaluate all actions and return the best one"""

        scored_actions = []

        for action in context.available_actions:
            # Find evaluator for this action type
            evaluator = self._find_evaluator(action)

            if evaluator:
                score = evaluator.evaluate(action, context)
                action.score = score
                action.reason = evaluator.get_reason()
                scored_actions.append(action)

        # Sort by score, return best
        scored_actions.sort(key=lambda a: a.score, reverse=True)

        best_action = scored_actions[0] if scored_actions else None

        logger.info(f"Best action: {best_action.action_text} (score={best_action.score:.1f}, reason={best_action.reason})")

        return best_action
```

## Rules System

Instead of nested if/else, use composable rules:

```python
class Rule(ABC):
    """Base class for a decision rule"""

    @abstractmethod
    def applies(self, action: Action, context: DecisionContext) -> bool:
        """Check if this rule applies to this action"""
        pass

    @abstractmethod
    def apply(self, score: float, action: Action, context: DecisionContext) -> float:
        """Modify the score based on this rule"""
        pass

class LowForceRule(Rule):
    """Penalize expensive actions when Force is low"""

    def applies(self, action: Action, context: DecisionContext) -> bool:
        return action.cost > 0 and context.board_state.force_pile < 5

    def apply(self, score: float, action: Action, context: DecisionContext) -> float:
        penalty = (action.cost / context.board_state.force_pile) * 20
        return score - penalty

class PowerAdvantageRule(Rule):
    """Boost aggressive actions when we have power advantage"""

    def applies(self, action: Action, context: DecisionContext) -> bool:
        return action.action_type in ["battle", "deploy"] and context.power_advantage > 5

    def apply(self, score: float, action: Action, context: DecisionContext) -> float:
        return score + 15
```

Then evaluators can apply multiple rules:

```python
def evaluate(self, action: Action, context: DecisionContext) -> float:
    score = 50.0

    for rule in self.rules:
        if rule.applies(action, context):
            score = rule.apply(score, action, context)

    return max(0, min(100, score))  # Clamp to 0-100
```

## Benefits of This Architecture

1. **Testable**: Each evaluator and rule can be unit tested independently
2. **Extensible**: Add new evaluators/rules without modifying existing code
3. **Debuggable**: Each decision has a clear score and reason
4. **Maintainable**: Rules are small, focused, easy to understand
5. **Tunable**: Adjust weights and thresholds easily

## Migration Path

**Phase 1**: Build framework (Action, DecisionContext, DecisionEngine)
**Phase 2**: Implement basic evaluators (Deploy, Battle, Force)
**Phase 3**: Port C# logic as rules (one rule at a time)
**Phase 4**: Add ML/heuristic improvements
**Phase 5**: A/B test against old logic

## Admin UI Integration

Show decision process:
```
Decision: Deploy Action
Available Actions:
  1. Deploy Character (score=75, reason="Reinforcing weak location")
  2. Deploy from Reserve Deck (score=30, reason="Low on Force")
  3. Pass (score=20, reason="Conserve resources")

Selected: Deploy Character
```
