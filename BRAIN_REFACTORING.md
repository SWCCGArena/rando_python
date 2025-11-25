# Brain System Refactoring

## Overview

Refactored the decision system to use the **Brain Interface** pattern, cleanly separating game engine logic (communication, state tracking) from decision-making logic (AI/brain).

This separation provides:
- **Swappable AI implementations** - Change brains without touching engine code
- **Testability** - Test brains independently of network/XML parsing
- **Personality systems** - Multiple brain types with different behaviors
- **Achievement tracking** - Brains can track game events for achievements
- **Future LLM integration** - Clean interface for GPT/Claude-based decisions

---

## Architecture

```
┌─────────────────────────────────────────┐
│     Flask Web Server (app.py)          │
│  • Admin UI + WebSocket                │
│  • Instantiates Brain                  │
└────────────┬────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│      Game Engine (engine/)              │
│  • GEMPClient - HTTP/XML communication  │
│  • EventProcessor - Parse game events   │
│  • BoardState - Track game state        │
│  • DecisionHandler - Bridge to Brain    │
└────────────┬────────────────────────────┘
             │
             │ Brain Interface
             ▼
┌─────────────────────────────────────────┐
│       Brain (brain/)                    │
│  • Brain ABC - Interface contract       │
│  • StaticBrain - Evaluator-based AI     │
│  • Future: AstrogatorBrain, LLMBrain    │
└─────────────────────────────────────────┘
             │
             │ Uses
             ▼
┌─────────────────────────────────────────┐
│    Evaluators (engine/evaluators/)      │
│  • DeployEvaluator - Deployment logic   │
│  • PassEvaluator - Pass/skip decisions  │
│  • Future: BattleEvaluator, etc.        │
└─────────────────────────────────────────┘
```

---

## Key Components

### 1. Brain Interface (`brain/interface.py`)

Defines the contract between engine and AI:

#### Input: `BrainContext`
```python
@dataclass
class BrainContext:
    board_state: BoardState              # Current game state
    decision_request: DecisionRequest    # What needs to be decided
    game_history: GameHistory            # Previous decisions/patterns
    current_strategy: str                # "DEPLOY", "HOLD", "AGGRESSIVE"
    deploy_threshold: int                # Config from admin UI
    max_hand_size: int                   # Config from admin UI
```

#### Input: `DecisionRequest`
```python
@dataclass
class DecisionRequest:
    decision_id: str                     # Unique ID for this decision
    decision_type: DecisionType          # CARD_SELECTION, CARD_ACTION, etc.
    prompt: str                          # Human-readable question
    options: List[DecisionOption]        # Available choices
    timeout_seconds: int                 # Time limit
```

#### Output: `BrainDecision`
```python
@dataclass
class BrainDecision:
    choice: str                          # Option ID to execute
    reasoning: str                       # Why this decision (for logging)
    confidence: float                    # 0.0-1.0 confidence score
    alternative_considered: str          # 2nd best option (for debugging)
```

#### Brain ABC
```python
class Brain(ABC):
    @abstractmethod
    def make_decision(self, context: BrainContext) -> BrainDecision:
        """Given context, return decision"""
        pass

    @abstractmethod
    def on_game_start(self, opponent_name: str, my_deck: str, their_deck_type: str):
        """Called when game starts"""
        pass

    @abstractmethod
    def on_game_end(self, won: bool, final_state: BoardState):
        """Called when game ends"""
        pass

    @abstractmethod
    def get_personality_name(self) -> str:
        """Return brain name (e.g., 'Static', 'Astrogator', 'LLM')"""
        pass
```

---

### 2. StaticBrain (`brain/static_brain.py`)

Default brain implementation using the evaluator system (ported from Unity C# ranking logic).

**How it works:**
1. Receives `BrainContext` with decision request
2. Converts to `EvaluatorContext` (internal format)
3. Runs evaluators (DeployEvaluator, PassEvaluator, etc.)
4. Returns highest-scored action as `BrainDecision`

**Code snippet:**
```python
class StaticBrain(Brain):
    def __init__(self):
        self.evaluators = [
            DeployEvaluator(),
            PassEvaluator(),
        ]
        self.combined_evaluator = CombinedEvaluator(self.evaluators)

    def make_decision(self, context: BrainContext) -> BrainDecision:
        # Convert BrainContext to EvaluatorContext
        evaluator_context = self._convert_to_evaluator_context(context)

        # Use evaluator system to rank options
        best_action = self.combined_evaluator.evaluate_decision(evaluator_context)

        if best_action:
            return BrainDecision(
                choice=best_action.action_id,
                reasoning=best_action.display_text + " | " + " | ".join(best_action.reasoning),
                confidence=min(1.0, best_action.score / 100.0)
            )
```

**Advantages:**
- ✅ Uses existing evaluator system (no logic rewrite needed)
- ✅ Ported from battle-tested Unity C# code
- ✅ Easy to extend with more evaluators

---

### 3. DecisionHandler Refactoring (`engine/decision_handler.py`)

Updated to use Brain instead of directly calling evaluators.

**Before:**
```python
class DecisionHandler:
    def __init__(self, board_state=None):
        self.evaluators = [DeployEvaluator(), PassEvaluator()]
        self.combined_evaluator = CombinedEvaluator(self.evaluators)

    @staticmethod
    def handle_decision(decision_element, phase_count=0, board_state=None):
        # ... parse XML ...
        evaluator_result = DecisionHandler._use_evaluators(...)
        return evaluator_result
```

**After:**
```python
class DecisionHandler:
    def __init__(self, brain=None):
        self.brain = brain

    @staticmethod
    def handle_decision(decision_element, phase_count=0, board_state=None, brain=None):
        # Try brain first for strategic decisions
        if brain and board_state:
            brain_result = DecisionHandler._use_brain(...)
            if brain_result:
                return brain_result

        # Fall back to heuristics for non-strategic decisions
        ...
```

**Key change:**
- Engine builds `BrainContext` from XML and board state
- Passes context to `brain.make_decision()`
- Returns brain's decision to engine
- Falls back to heuristics if brain unavailable

---

### 4. App Integration (`app.py`)

Instantiates brain and passes to decision handler.

**Changes:**
```python
# Import
from brain import StaticBrain

# Initialize in BotState
class BotState:
    def __init__(self):
        # Brain for decision-making
        self.brain = StaticBrain()
        logger.info(f"🧠 Initialized brain: {self.brain.get_personality_name()}")

# Pass brain to decision handler
decision_response = DecisionHandler.handle_decision(
    event,
    board_state=board_state_for_decision,
    brain=bot_state.brain  # <-- NEW
)
```

---

## Benefits

### 1. Separation of Concerns

**Engine responsibilities:**
- HTTP communication with GEMP server
- XML parsing
- Board state tracking
- Event processing

**Brain responsibilities:**
- Strategic decision-making
- Card evaluation
- Ranking algorithms
- Personality/style

**Why this matters:**
- Engine code doesn't change when AI logic changes
- Brain can be tested independently
- Multiple brains can share the same engine

---

### 2. Swappable Brains

Easy to create new brain implementations:

```python
class AstrogatorBrain(StaticBrain):
    """Space-themed personality with custom greetings"""

    def get_personality_name(self) -> str:
        return "Astrogator"

    def get_welcome_message(self, opponent_name: str, deck_name: str) -> str:
        return f"Greetings {opponent_name}! Prepare for hyperspace!"

class LLMBrain(Brain):
    """Future: Use GPT/Claude for decisions"""

    def make_decision(self, context: BrainContext) -> BrainDecision:
        # Convert context to prompt
        prompt = self._build_prompt(context)

        # Call LLM API
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}]
        )

        # Parse response
        return self._parse_llm_response(response)
```

**Change brain at runtime:**
```python
# In admin UI
bot_state.brain = AstrogatorBrain()  # Switch personality
```

---

### 3. Testability

Test brains without network/XML:

```python
def test_deploy_decision():
    # Create mock board state
    board_state = BoardState(
        my_player_name="TestBot",
        force_pile=10,
        locations=[...]
    )

    # Create decision request
    request = DecisionRequest(
        decision_id="test_1",
        decision_type=DecisionType.CARD_ACTION,
        prompt="Deploy which card?",
        options=[
            DecisionOption(option_id="card_1", display_text="Deploy Luke"),
            DecisionOption(option_id="card_2", display_text="Deploy Vader"),
        ]
    )

    # Build context
    context = BrainContext(
        board_state=board_state,
        decision_request=request,
        game_history=GameHistory()
    )

    # Test brain
    brain = StaticBrain()
    decision = brain.make_decision(context)

    assert decision.choice in ["card_1", "card_2"]
    assert decision.confidence > 0.5
```

---

### 4. Achievement Tracking

Brains can track events for achievements:

```python
class AchievementBrain(StaticBrain):
    def __init__(self):
        super().__init__()
        self.achievements = {}
        self.battle_damage_dealt = 0

    def on_game_end(self, won: bool, final_state: BoardState):
        # Check achievements
        if won and final_state.total_my_power() >= 20:
            self.achievements["power_overwhelming"] = True

        if self.battle_damage_dealt >= 30:
            self.achievements["devastator"] = True

        logger.info(f"🏆 Achievements: {self.achievements}")
```

---

## Future Brains

### Planned Implementations

1. **AstrogatorBrain** - Space-themed personality (extends StaticBrain)
   - Custom chat messages
   - Achievement tracking
   - Same ranking logic as StaticBrain

2. **LLMBrain** - GPT/Claude-based decisions
   - Converts BrainContext to natural language prompt
   - Calls OpenAI/Anthropic API
   - Parses response back to BrainDecision
   - Requires API key and internet

3. **MCTSBrain** - Monte Carlo Tree Search
   - Simulates future game states
   - Explores decision tree
   - Returns highest-value path
   - Computationally expensive

4. **SimpleBrain** - Random/testing brain
   - Already implemented in `interface.py`
   - Picks first option always
   - Useful for debugging

---

## Files Modified

| File | Changes | Impact |
|------|---------|--------|
| `brain/__init__.py` | Export Brain, StaticBrain, interfaces | Package setup |
| `brain/interface.py` | Already existed | Brain contract |
| `brain/static_brain.py` | **NEW** - StaticBrain implementation | Default AI |
| `engine/decision_handler.py` | Use Brain instead of evaluators | Bridge engine→brain |
| `app.py` | Instantiate Brain, pass to handler | Integration |

**Total:** ~300 lines added, ~50 lines modified

---

## Usage Example

### Creating a Custom Brain

```python
# brain/custom_brain.py
from .interface import Brain, BrainContext, BrainDecision

class MyCustomBrain(Brain):
    def make_decision(self, context: BrainContext) -> BrainDecision:
        # Access game state
        my_power = context.board_state.total_my_power()
        their_power = context.board_state.total_their_power()

        # Make decision based on custom logic
        if my_power > their_power:
            # Aggressive strategy
            best_option = self._pick_aggressive_option(context)
        else:
            # Defensive strategy
            best_option = self._pick_defensive_option(context)

        return BrainDecision(
            choice=best_option.option_id,
            reasoning=f"Custom logic: power {my_power}/{their_power}",
            confidence=0.8
        )

    def on_game_start(self, opponent_name, my_deck, their_deck_type):
        logger.info(f"MyCustomBrain starting game vs {opponent_name}")

    def on_game_end(self, won, final_state):
        logger.info(f"MyCustomBrain: {'Won' if won else 'Lost'}")

    def get_personality_name(self) -> str:
        return "Custom"
```

### Using the Custom Brain

```python
# app.py
from brain import MyCustomBrain

# In BotState.__init__
self.brain = MyCustomBrain()  # <-- Use custom brain
```

---

## Testing

### Manual Testing

1. Start bot: `python app.py`
2. Check logs for: `🧠 Initialized brain: Static`
3. Start game
4. Watch for brain decisions: `🧠 Brain chose: ...`

### Unit Testing (Future)

```python
# tests/test_static_brain.py
import pytest
from brain import StaticBrain, BrainContext, DecisionRequest

def test_brain_makes_decision():
    brain = StaticBrain()
    context = create_mock_context()
    decision = brain.make_decision(context)

    assert decision.choice
    assert decision.reasoning
    assert 0.0 <= decision.confidence <= 1.0
```

---

## Success Criteria

✅ **Brain interface defined** - Clean ABC with BrainContext/BrainDecision
✅ **StaticBrain implemented** - Uses existing evaluator system
✅ **DecisionHandler refactored** - Uses Brain instead of evaluators directly
✅ **App integration complete** - Brain instantiated and passed to handler
✅ **No logic changes** - Same decisions as before, just cleaner architecture

**Ready for testing!** 🚀

---

## Next Steps

### Immediate
1. **Test in live game** - Verify brain system works end-to-end
2. **Monitor logs** - Check for "🧠 Brain chose:" messages
3. **Compare decisions** - Ensure same quality as before refactoring

### Short Term
1. **Add more evaluators** - BattleEvaluator, MoveEvaluator, ForceActivationEvaluator
2. **Implement AstrogatorBrain** - First personality variant
3. **Add achievement tracking** - Track game events in brain

### Long Term
1. **LLM integration** - Experiment with GPT/Claude for decisions
2. **MCTS brain** - Advanced decision-making via simulation
3. **Brain stats** - Track win rates per brain type
4. **Admin UI brain selector** - Choose brain from UI

---

## Key Learnings

### 1. Interfaces Enable Flexibility
By defining a clean Brain interface, we can swap AI implementations without touching engine code. This is critical for experimentation and testing.

### 2. Separation of Concerns Matters
Game engine should handle "what's happening" (communication, state tracking), brain should handle "what to do" (decisions, strategy). Don't mix these concerns.

### 3. Adapter Pattern for Legacy Code
StaticBrain wraps the existing evaluator system, allowing us to refactor architecture without rewriting logic. This reduces risk.

### 4. Test at Boundaries
The Brain interface is a natural testing boundary. We can test brains with mock contexts, and test the engine with mock brains.

---

## Conclusion

The brain refactoring provides a **clean separation** between game mechanics and decision logic, enabling:
- Multiple AI personalities
- Easy testing
- Future LLM integration
- Achievement systems

All while maintaining the same decision quality as before. The architecture is now **extensible** and **testable**.

🧠 **The brain is ready!**
