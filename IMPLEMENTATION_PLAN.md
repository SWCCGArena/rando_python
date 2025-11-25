# Rando Cal Python Rewrite - Implementation Plan

**Project Goal**: Rewrite the Unity C# SWCCG bot (rando_cal) as a Python Flask application with clean architecture, focusing on separating the game engine from decision-making logic to eventually support LLM-based AI.

**Timeline**: Multi-day/multi-session development (4-6 weeks total)

**Test Server**: Local GEMP instance at `localhost:8082` (available Monday)

**Production Server**: TBD (gemp.starwarsccg.org or 200monkeys)

---

## Architecture Overview

### Core Principle: Engine vs Brain Separation

```
┌─────────────────────────────────────────────────────────────┐
│                    FLASK WEB SERVER                         │
│              (Admin UI, API, WebSocket)                     │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│                   GAME ENGINE                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  • Network Client (HTTP/XML to GEMP)                │   │
│  │  • Event Processor (parse XML events)               │   │
│  │  • Board State Tracker (locations, cards, zones)    │   │
│  │  • Game Loop (poll, process, respond)               │   │
│  └──────────────────────────────────────────────────────┘   │
└────────────────────┬────────────────────────────────────────┘
                     │
                     │ BrainInterface
                     ▼
┌─────────────────────────────────────────────────────────────┐
│                   BRAIN (Decision Maker)                    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Input: BrainContext                                │   │
│  │    - BoardState (locations, cards, power, zones)    │   │
│  │    - DecisionRequest (type, options, metadata)      │   │
│  │    - GameHistory (previous turns, opponent patterns)│   │
│  │                                                      │   │
│  │  Output: BrainDecision                              │   │
│  │    - choice: str (decision ID to execute)           │   │
│  │    - reasoning: str (why this choice)               │   │
│  │    - confidence: float (0-1, how sure)              │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                             │
│  Implementations:                                           │
│  • StaticBrain (port of Unity logic, rankings)             │
│  • AstrogatorBrain (extends StaticBrain w/ personality)    │
│  • LLMBrain (future: GPT-4, Claude, etc.)                  │
└─────────────────────────────────────────────────────────────┘
```

### Key Design Patterns

1. **Strategy Pattern** - Swappable brains via interface
2. **Observer Pattern** - Game events trigger state updates
3. **State Machine** - Game flow (Lobby → Joining → Playing → GameEnd)
4. **Dependency Injection** - Brain injected into engine at runtime
5. **Repository Pattern** - Data persistence abstraction

### Why This Architecture?

**Problem with Unity C# version:**
- Massive files (800-1000 lines) with nested if/else statements
- Decision logic tightly coupled to networking and state tracking
- Hard to test, hard to modify, impossible to swap AI approaches

**Solution:**
- **Engine** handles "mechanics" - what's happening in the game
- **Brain** handles "decisions" - what to do about it
- Clean interface between them allows brain replacement
- Brain receives rich context, returns simple decision + reasoning

---

## Brain Interface Specification

### Input: BrainContext

```python
from dataclasses import dataclass
from typing import List, Dict, Optional
from enum import Enum

class DecisionType(Enum):
    MULTIPLE_CHOICE = "multiple_choice"
    CARD_SELECTION = "card_selection"
    CARD_ACTION = "card_action"
    INTEGER_INPUT = "integer"
    ARBITRARY_CARDS = "arbitrary_cards"

@dataclass
class CardInfo:
    """Metadata about a card for brain decision-making"""
    card_id: str
    blueprint_id: str
    title: str
    type: str  # Character, Starship, Location, etc.
    power: Optional[int]
    ability: Optional[int]
    deploy_cost: Optional[int]
    destiny: Optional[int]
    icons: List[str]  # Pilot, Warrior, etc.
    game_text: str
    zone: str  # "IN_PLAY", "HAND", "FORCE_PILE", etc.
    location: Optional[str]  # Which location it's at
    owner: str  # "me" or "opponent"
    attached_to: Optional[str]  # Card ID it's attached to
    attachments: List[str]  # Card IDs attached to this card

@dataclass
class LocationState:
    """State of a location on the board"""
    location_id: str
    title: str
    system: Optional[str]
    my_power: int
    their_power: int
    my_ability: int
    their_ability: int
    my_cards: List[CardInfo]
    their_cards: List[CardInfo]
    # Threat assessment (for improved AI)
    adjacent_locations: List[str]  # Nearby locations
    their_adjacent_power: int  # Power at adjacent locations
    battle_in_progress: bool

@dataclass
class ZoneState:
    """State of a game zone"""
    hand: List[CardInfo]
    force_pile_size: int
    used_pile_size: int
    lost_pile_size: int
    reserve_deck_size: int
    out_of_play_size: int

@dataclass
class BoardState:
    """Complete game state"""
    turn_number: int
    phase: str  # "DEPLOY", "BATTLE", "MOVE", etc.
    current_player: str  # "me" or "opponent"

    # Locations
    locations: List[LocationState]

    # Zones
    my_zones: ZoneState
    their_zones: ZoneState

    # Force generation
    my_force_generation: int
    their_force_generation: int

    # Totals (for quick reference)
    my_total_power: int
    their_total_power: int
    my_total_force: int  # force_pile + used_pile + reserve
    their_total_force: int

@dataclass
class DecisionOption:
    """A single option the brain can choose"""
    option_id: str
    option_type: str
    display_text: str

    # Context-specific metadata
    card: Optional[CardInfo] = None
    target_location: Optional[LocationState] = None
    integer_range: Optional[tuple] = None  # (min, max)

    # For improved AI: pre-calculated metrics
    power_differential: Optional[int] = None
    estimated_value: Optional[float] = None

@dataclass
class DecisionRequest:
    """A decision the brain needs to make"""
    decision_id: str
    decision_type: DecisionType
    prompt: str  # Human-readable question
    options: List[DecisionOption]
    timeout_seconds: int  # How long before auto-forfeit

@dataclass
class GameHistory:
    """Historical context for pattern recognition"""
    previous_decisions: List[tuple]  # (decision_type, choice, outcome)
    opponent_name: str
    opponent_deck_type: str  # "dark" or "light"
    turns_elapsed: int

    # Opponent behavior tracking (for improved AI)
    opponent_has_interrupted_battle: bool
    opponent_typical_hand_size: float  # Running average
    locations_opponent_prioritizes: List[str]

@dataclass
class BrainContext:
    """Everything the brain needs to make a decision"""
    board_state: BoardState
    decision_request: DecisionRequest
    game_history: GameHistory

    # Strategy context
    current_strategy: str  # "DEPLOY", "HOLD", "AGGRESSIVE", etc.
    deploy_threshold: int  # Config setting
    max_hand_size: int  # Config setting

@dataclass
class BrainDecision:
    """The brain's decision output"""
    choice: str  # option_id to execute
    reasoning: str  # Human-readable explanation
    confidence: float  # 0.0 to 1.0
    alternative_considered: Optional[str] = None  # 2nd best option
```

### Brain Interface

```python
from abc import ABC, abstractmethod

class Brain(ABC):
    """Abstract base class for all decision-making implementations"""

    @abstractmethod
    def make_decision(self, context: BrainContext) -> BrainDecision:
        """
        Given game context, return a decision.

        This is the ONLY method the engine calls.
        Brain can do whatever it wants internally - rankings,
        LLM calls, Monte Carlo tree search, whatever.
        """
        pass

    @abstractmethod
    def on_game_start(self, opponent_name: str, deck_type: str):
        """Called when game begins, for initialization"""
        pass

    @abstractmethod
    def on_game_end(self, won: bool, final_state: BoardState):
        """Called when game ends, for learning/stats"""
        pass

    @abstractmethod
    def get_personality_name(self) -> str:
        """Return brain personality (e.g., 'Astrogator', 'Standard')"""
        pass
```

### Example: Static Brain (Port of Unity Logic)

```python
class StaticBrain(Brain):
    """Traditional ranking-based AI, ported from Unity"""

    VERY_BAD = -999
    BAD = -100
    NEUTRAL = 0
    GOOD = 100
    VERY_GOOD = 999

    def make_decision(self, context: BrainContext) -> BrainDecision:
        decision_type = context.decision_request.decision_type

        if decision_type == DecisionType.MULTIPLE_CHOICE:
            return self._handle_multiple_choice(context)
        elif decision_type == DecisionType.CARD_ACTION:
            return self._handle_card_action(context)
        elif decision_type == DecisionType.CARD_SELECTION:
            return self._handle_card_selection(context)
        # ... etc

    def _handle_card_action(self, context: BrainContext) -> BrainDecision:
        """Rank all possible actions, choose best"""
        options = context.decision_request.options

        for option in options:
            option.rank = self.NEUTRAL
            option.reason = ""

            if "Force drain" in option.display_text:
                option.rank = self._rank_force_drain(option, context)
            elif "Initiate battle" in option.display_text:
                option.rank = self._rank_battle(option, context)
            # ... 50+ action patterns

        best = max(options, key=lambda o: o.rank)

        return BrainDecision(
            choice=best.option_id,
            reasoning=best.reason,
            confidence=self._rank_to_confidence(best.rank)
        )
```

### Example: LLM Brain (Future)

```python
class LLMBrain(Brain):
    """LLM-based decision making (GPT-4, Claude, etc.)"""

    def make_decision(self, context: BrainContext) -> BrainDecision:
        # Convert context to natural language
        prompt = self._context_to_prompt(context)

        # Call LLM API
        response = self.llm_client.generate(
            prompt=prompt,
            temperature=0.7,
            max_tokens=500
        )

        # Parse LLM response into decision
        choice, reasoning = self._parse_llm_response(response)

        return BrainDecision(
            choice=choice,
            reasoning=reasoning,
            confidence=0.8  # Could parse from LLM if it provides
        )

    def _context_to_prompt(self, context: BrainContext) -> str:
        return f"""
You are playing Star Wars CCG. Current state:

BOARD:
{self._format_locations(context.board_state.locations)}

YOUR ZONES:
- Hand: {len(context.board_state.my_zones.hand)} cards
- Force Pile: {context.board_state.my_zones.force_pile_size}
- Reserve: {context.board_state.my_zones.reserve_deck_size}

DECISION REQUIRED:
{context.decision_request.prompt}

OPTIONS:
{self._format_options(context.decision_request.options)}

Choose the best option and explain why.
Format: CHOICE: <option_id> | REASONING: <explanation>
"""
```

---

## Phase 1: Basic Flask Service + Admin UI Skeleton

**Goal**: Get Flask running with a basic admin interface. No GEMP connection yet.

**Duration**: 1 day

### Files to Create

```
new_rando/
├── app.py                  # Flask application entry point
├── config.py               # Configuration management
├── requirements.txt        # Python dependencies
├── README.md               # Project overview
├── .gitignore              # Ignore venv, logs, data, etc.
│
├── engine/                 # Game engine (isolated, self-contained)
│   ├── __init__.py
│   └── state.py            # GameState enum (STOPPED, CONNECTING, IN_LOBBY, etc.)
│
├── brain/                  # Brain interface and implementations
│   ├── __init__.py
│   └── interface.py        # Brain ABC, BrainContext, BrainDecision dataclasses
│
├── admin/                  # Web admin interface
│   ├── __init__.py
│   ├── routes.py           # Flask routes
│   ├── static/
│   │   ├── css/
│   │   │   └── style.css   # Basic styling
│   │   └── js/
│   │       └── admin.js    # WebSocket client, UI updates
│   └── templates/
│       ├── base.html       # Base template
│       └── dashboard.html  # Main dashboard
│
├── data/                   # SQLite database, persistent data (gitignored)
├── logs/                   # Log files (gitignored)
└── venv/                   # Virtual environment (gitignored)
```

### Implementation Steps

#### 1.1 - Environment Setup

```bash
cd /opt/gemp/rando_cal_working/new_rando
python3 -m venv venv
source venv/bin/activate
```

#### 1.2 - requirements.txt

```
Flask==3.0.0
Flask-SocketIO==5.3.5
python-socketio==5.10.0
eventlet==0.33.3
gunicorn==21.2.0
requests==2.31.0
lxml==4.9.4
SQLAlchemy==2.0.23
python-dotenv==1.0.0
pytest==7.4.3
```

#### 1.3 - config.py

```python
import os
from dataclasses import dataclass

@dataclass
class Config:
    # Flask settings
    SECRET_KEY: str = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-prod')
    HOST: str = '127.0.0.1'  # localhost only, nginx will proxy
    PORT: int = 5001  # Custom port to avoid conflicts

    # GEMP server settings
    GEMP_SERVER_URL: str = os.environ.get('GEMP_SERVER_URL', 'http://localhost:8082/gemp-swccg-server/')
    GEMP_USERNAME: str = os.environ.get('GEMP_USERNAME', 'rando_blu')
    GEMP_PASSWORD: str = os.environ.get('GEMP_PASSWORD', '')

    # Bot settings
    BOT_MODE: str = 'astrogator'  # 'standard', 'astrogator', 'scrap_trader'
    POLL_INTERVAL: int = 3  # seconds between GEMP polls

    # AI configuration
    MAX_HAND_SIZE: int = 12
    DEPLOY_THRESHOLD: int = 3
    CHAOS_PERCENT: int = 25

    # Paths
    BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR: str = os.path.join(BASE_DIR, 'data')
    LOG_DIR: str = os.path.join(BASE_DIR, 'logs')
    CARD_JSON_DIR: str = os.path.join(os.path.dirname(BASE_DIR), 'swccg-card-json')

    # Database
    DATABASE_URL: str = f'sqlite:///{os.path.join(DATA_DIR, "rando.db")}'

    def __post_init__(self):
        # Ensure directories exist
        os.makedirs(self.DATA_DIR, exist_ok=True)
        os.makedirs(self.LOG_DIR, exist_ok=True)

config = Config()
```

#### 1.4 - engine/state.py

```python
from enum import Enum

class GameState(Enum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    IN_LOBBY = "in_lobby"
    CREATING_TABLE = "creating_table"
    WAITING_FOR_OPPONENT = "waiting_for_opponent"
    JOINING_GAME = "joining_game"
    PLAYING = "playing"
    GAME_ENDED = "game_ended"
    ERROR = "error"
```

#### 1.5 - brain/interface.py

```python
# Full BrainContext, BrainDecision, Brain ABC from architecture section above
# Copy the complete interface specification
```

#### 1.6 - app.py

```python
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import logging
import os
from config import config
from engine.state import GameState

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(config.LOG_DIR, 'rando.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize Flask
app = Flask(__name__)
app.config['SECRET_KEY'] = config.SECRET_KEY

# Initialize SocketIO
socketio = SocketIO(app, cors_allowed_origins="*")

# Global bot state (will be expanded in later phases)
class BotState:
    def __init__(self):
        self.state = GameState.STOPPED
        self.config = config
        self.last_error = None

    def to_dict(self):
        return {
            'state': self.state.value,
            'config': {
                'gemp_server': self.config.GEMP_SERVER_URL,
                'bot_mode': self.config.BOT_MODE,
                'max_hand_size': self.config.MAX_HAND_SIZE,
                'deploy_threshold': self.config.DEPLOY_THRESHOLD,
            },
            'last_error': self.last_error
        }

bot_state = BotState()

@app.route('/')
def index():
    return render_template('dashboard.html', state=bot_state.to_dict())

@app.route('/health')
def health():
    """Health check endpoint"""
    return {'status': 'ok', 'bot_state': bot_state.state.value}

@socketio.on('connect')
def handle_connect():
    logger.info('Admin UI connected')
    emit('state_update', bot_state.to_dict())

@socketio.on('request_state')
def handle_request_state():
    emit('state_update', bot_state.to_dict())

if __name__ == '__main__':
    logger.info(f'Starting Rando Cal bot on {config.HOST}:{config.PORT}')
    socketio.run(app, host=config.HOST, port=config.PORT, debug=True)
```

#### 1.7 - admin/templates/base.html

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Rando Cal - Admin</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
</head>
<body>
    <div class="container">
        <header>
            <h1>Rando Cal - SWCCG Bot Admin</h1>
        </header>
        {% block content %}{% endblock %}
    </div>
    <script src="{{ url_for('static', filename='js/admin.js') }}"></script>
</body>
</html>
```

#### 1.8 - admin/templates/dashboard.html

```html
{% extends "base.html" %}

{% block content %}
<div class="dashboard">
    <div class="status-panel">
        <h2>Bot Status</h2>
        <div class="status-indicator">
            <span id="status-text">{{ state.state }}</span>
            <span id="status-dot" class="dot"></span>
        </div>
        <div class="controls">
            <button id="btn-start" onclick="startBot()">Start Bot</button>
            <button id="btn-stop" onclick="stopBot()">Stop Bot</button>
        </div>
        <div id="error-message" class="error"></div>
    </div>

    <div class="config-panel">
        <h2>Configuration</h2>
        <div class="config-item">
            <label>GEMP Server:</label>
            <span id="gemp-server">{{ state.config.gemp_server }}</span>
        </div>
        <div class="config-item">
            <label>Bot Mode:</label>
            <span id="bot-mode">{{ state.config.bot_mode }}</span>
        </div>
        <div class="config-item">
            <label>Max Hand Size:</label>
            <input type="range" id="max-hand-size" min="5" max="15"
                   value="{{ state.config.max_hand_size }}"
                   onchange="updateConfig('max_hand_size', this.value)">
            <span id="max-hand-value">{{ state.config.max_hand_size }}</span>
        </div>
        <div class="config-item">
            <label>Deploy Threshold:</label>
            <input type="range" id="deploy-threshold" min="0" max="30"
                   value="{{ state.config.deploy_threshold }}"
                   onchange="updateConfig('deploy_threshold', this.value)">
            <span id="deploy-threshold-value">{{ state.config.deploy_threshold }}</span>
        </div>
    </div>

    <div class="game-panel">
        <h2>Game State</h2>
        <div id="game-state">
            <p class="placeholder">No game in progress</p>
        </div>
    </div>

    <div class="log-panel">
        <h2>Activity Log</h2>
        <div id="activity-log">
            <p class="log-entry">Bot initialized</p>
        </div>
    </div>
</div>
{% endblock %}
```

#### 1.9 - admin/static/css/style.css

```css
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    background: #1a1a1a;
    color: #e0e0e0;
}

.container {
    max-width: 1400px;
    margin: 0 auto;
    padding: 20px;
}

header {
    text-align: center;
    padding: 20px 0;
    border-bottom: 2px solid #333;
    margin-bottom: 30px;
}

h1 {
    color: #4a9eff;
}

.dashboard {
    display: grid;
    grid-template-columns: 1fr 1fr;
    grid-gap: 20px;
}

.status-panel, .config-panel, .game-panel, .log-panel {
    background: #2a2a2a;
    border: 1px solid #333;
    border-radius: 8px;
    padding: 20px;
}

h2 {
    color: #4a9eff;
    margin-bottom: 15px;
    font-size: 1.3em;
}

.status-indicator {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 20px;
}

.dot {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: #666;
}

.dot.running {
    background: #4CAF50;
    box-shadow: 0 0 10px #4CAF50;
}

.dot.error {
    background: #f44336;
    box-shadow: 0 0 10px #f44336;
}

.controls {
    display: flex;
    gap: 10px;
}

button {
    padding: 10px 20px;
    background: #4a9eff;
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 1em;
}

button:hover {
    background: #3a8eef;
}

button:disabled {
    background: #555;
    cursor: not-allowed;
}

.config-item {
    margin-bottom: 15px;
}

.config-item label {
    display: block;
    margin-bottom: 5px;
    color: #aaa;
}

input[type="range"] {
    width: 200px;
    margin-right: 10px;
}

.error {
    color: #f44336;
    margin-top: 10px;
}

.game-panel {
    grid-column: 1 / -1;
}

.log-panel {
    grid-column: 1 / -1;
    max-height: 300px;
    overflow-y: auto;
}

#activity-log {
    font-family: monospace;
    font-size: 0.9em;
}

.log-entry {
    padding: 5px 0;
    border-bottom: 1px solid #333;
}

.log-entry:last-child {
    border-bottom: none;
}

.placeholder {
    color: #666;
    font-style: italic;
}
```

#### 1.10 - admin/static/js/admin.js

```javascript
// Connect to SocketIO
const socket = io();

// State management
let botState = {
    state: 'stopped',
    config: {},
    last_error: null
};

// Socket event handlers
socket.on('connect', () => {
    console.log('Connected to bot server');
    addLog('Connected to server');
    socket.emit('request_state');
});

socket.on('disconnect', () => {
    console.log('Disconnected from bot server');
    addLog('Disconnected from server');
});

socket.on('state_update', (data) => {
    console.log('State update:', data);
    botState = data;
    updateUI();
});

// UI update functions
function updateUI() {
    // Update status
    const statusText = document.getElementById('status-text');
    const statusDot = document.getElementById('status-dot');

    if (statusText) {
        statusText.textContent = botState.state;
    }

    if (statusDot) {
        statusDot.className = 'dot';
        if (botState.state === 'playing' || botState.state === 'in_lobby') {
            statusDot.classList.add('running');
        } else if (botState.state === 'error') {
            statusDot.classList.add('error');
        }
    }

    // Update error message
    const errorMsg = document.getElementById('error-message');
    if (errorMsg && botState.last_error) {
        errorMsg.textContent = botState.last_error;
    } else if (errorMsg) {
        errorMsg.textContent = '';
    }

    // Update buttons
    updateButtons();
}

function updateButtons() {
    const btnStart = document.getElementById('btn-start');
    const btnStop = document.getElementById('btn-stop');

    if (btnStart && btnStop) {
        if (botState.state === 'stopped' || botState.state === 'error') {
            btnStart.disabled = false;
            btnStop.disabled = true;
        } else {
            btnStart.disabled = true;
            btnStop.disabled = false;
        }
    }
}

// Control functions
function startBot() {
    socket.emit('start_bot');
    addLog('Starting bot...');
}

function stopBot() {
    socket.emit('stop_bot');
    addLog('Stopping bot...');
}

function updateConfig(key, value) {
    socket.emit('update_config', {key, value});
    addLog(`Updated config: ${key} = ${value}`);

    // Update display
    const displayId = key.replace(/_/g, '-') + '-value';
    const display = document.getElementById(displayId);
    if (display) {
        display.textContent = value;
    }
}

// Logging
function addLog(message) {
    const logPanel = document.getElementById('activity-log');
    if (logPanel) {
        const entry = document.createElement('p');
        entry.className = 'log-entry';
        const timestamp = new Date().toLocaleTimeString();
        entry.textContent = `[${timestamp}] ${message}`;
        logPanel.insertBefore(entry, logPanel.firstChild);

        // Keep only last 50 entries
        while (logPanel.children.length > 50) {
            logPanel.removeChild(logPanel.lastChild);
        }
    }
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    updateUI();
});
```

### Testing Phase 1

```bash
# Activate venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run Flask app
python app.py
```

**Expected Result:**
- Flask server starts on `http://127.0.0.1:5001`
- Admin UI loads in browser
- WebSocket connects successfully
- Status shows "stopped"
- Start/Stop buttons respond (even if they don't do anything yet)
- Config sliders work
- Activity log updates

**Definition of Done:**
- [ ] Flask app runs without errors
- [ ] Admin UI renders correctly
- [ ] WebSocket connection established
- [ ] All UI elements present and styled
- [ ] No console errors
- [ ] Health endpoint returns 200

---

## Phase 2: GEMP Networking (Connect, List/Create Tables)

**Goal**: Connect to GEMP server, authenticate, list tables, create a table.

**Duration**: 1-2 days

### Files to Create/Modify

```
new_rando/
├── engine/
│   ├── client.py           # GEMP HTTP client
│   ├── parser.py           # XML parsing utilities
│   └── models.py           # Data models (Table, Player, etc.)
```

### Implementation Steps

#### 2.1 - engine/models.py

```python
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime

@dataclass
class Player:
    """A player in the hall or game"""
    name: str
    participant_id: Optional[str] = None

@dataclass
class GameTable:
    """A game table in the hall"""
    table_id: str
    table_name: str
    game_format: str  # "open", "legacy", etc.
    status: str  # "waiting", "playing", "finished"
    players: List[Player]
    deck_type: Optional[str] = None  # "light" or "dark"

    def is_available(self) -> bool:
        """Can we join this table?"""
        return self.status == "waiting" and len(self.players) < 2

    def is_rando_table(self) -> bool:
        """Is this a table created by rando?"""
        return self.table_name.startswith("rando") or "rando" in self.table_name.lower()

@dataclass
class DeckInfo:
    """Information about a deck"""
    name: str
    is_library: bool  # Library deck vs personal deck
    side: str  # "light" or "dark"
```

#### 2.2 - engine/parser.py

```python
import xml.etree.ElementTree as ET
from typing import List, Optional
from .models import GameTable, Player
import logging

logger = logging.getLogger(__name__)

class XMLParser:
    """Utilities for parsing GEMP XML responses"""

    @staticmethod
    def parse_hall_tables(xml_text: str) -> List[GameTable]:
        """
        Parse hall XML response to get list of tables.

        Example XML:
        <hall>
            <tables>
                <table id="123" name="Test Game" status="waiting" format="open">
                    <player name="player1"/>
                </table>
            </tables>
        </hall>
        """
        try:
            root = ET.fromstring(xml_text)
            tables = []

            for table_elem in root.findall('.//table'):
                table_id = table_elem.get('id')
                table_name = table_elem.get('name', 'Unnamed')
                status = table_elem.get('status', 'unknown')
                game_format = table_elem.get('format', 'open')

                players = []
                for player_elem in table_elem.findall('.//player'):
                    player_name = player_elem.get('name')
                    if player_name:
                        players.append(Player(name=player_name))

                tables.append(GameTable(
                    table_id=table_id,
                    table_name=table_name,
                    game_format=game_format,
                    status=status,
                    players=players
                ))

            logger.info(f"Parsed {len(tables)} tables from hall XML")
            return tables

        except ET.ParseError as e:
            logger.error(f"Failed to parse hall XML: {e}")
            return []

    @staticmethod
    def parse_login_response(xml_text: str) -> tuple[bool, Optional[str]]:
        """
        Parse login response.
        Returns: (success: bool, error_message: Optional[str])
        """
        try:
            root = ET.fromstring(xml_text)

            # Check for error element
            error = root.find('.//error')
            if error is not None:
                return False, error.text

            # Check for success indicators
            # GEMP returns different formats, adjust as needed
            return True, None

        except ET.ParseError:
            # Some responses might not be XML (just HTTP 200 = success)
            return True, None
```

#### 2.3 - engine/client.py

```python
import requests
from typing import List, Optional, Dict
import logging
from .models import GameTable, DeckInfo
from .parser import XMLParser

logger = logging.getLogger(__name__)

class GEMPClient:
    """HTTP client for GEMP server communication"""

    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip('/')
        self.session = requests.Session()
        self.participant_id = None
        self.logged_in = False
        self.parser = XMLParser()

        # Set a reasonable timeout
        self.timeout = 10

    def login(self, username: str, password: str) -> bool:
        """
        Authenticate with GEMP server.
        Returns: True if successful, False otherwise
        """
        try:
            logger.info(f"Attempting login to {self.server_url} as {username}")

            response = self.session.post(
                f"{self.server_url}/login",
                data={
                    'login': username,
                    'password': password
                },
                timeout=self.timeout
            )

            if response.status_code == 200:
                logger.info("Login successful")
                self.logged_in = True
                return True
            else:
                logger.error(f"Login failed: HTTP {response.status_code}")
                return False

        except requests.RequestException as e:
            logger.error(f"Login request failed: {e}")
            return False

    def get_hall_tables(self) -> List[GameTable]:
        """
        Get list of current tables in the hall.
        Returns: List of GameTable objects
        """
        if not self.logged_in:
            logger.warning("Not logged in, cannot get hall tables")
            return []

        try:
            logger.info("Fetching hall tables")

            response = self.session.get(
                f"{self.server_url}/hall",
                params={'participantId': 'null'},
                timeout=self.timeout
            )

            if response.status_code == 200:
                tables = self.parser.parse_hall_tables(response.text)
                logger.info(f"Found {len(tables)} tables")
                return tables
            else:
                logger.error(f"Failed to get hall: HTTP {response.status_code}")
                return []

        except requests.RequestException as e:
            logger.error(f"Hall request failed: {e}")
            return []

    def create_table(self, deck_name: str, is_library: bool,
                     table_name: str, game_format: str = "open") -> Optional[str]:
        """
        Create a new game table.

        Args:
            deck_name: Name of the deck to use
            is_library: Whether deck is from library (vs personal)
            table_name: Name for the table
            game_format: Game format (open, legacy, etc.)

        Returns: Table ID if successful, None otherwise
        """
        if not self.logged_in:
            logger.warning("Not logged in, cannot create table")
            return None

        try:
            logger.info(f"Creating table '{table_name}' with deck '{deck_name}'")

            response = self.session.post(
                f"{self.server_url}/hall",
                data={
                    'participantId': 'null',
                    'deckName': deck_name,
                    'sampleDeck': 'true' if is_library else 'false',
                    'tableName': table_name,
                    'format': game_format
                },
                timeout=self.timeout
            )

            if response.status_code == 200:
                # Parse response to get table ID
                # GEMP might return the table in the response or we need to query hall
                logger.info("Table created successfully")

                # Fetch hall to find our new table
                tables = self.get_hall_tables()
                for table in tables:
                    if table.table_name == table_name:
                        logger.info(f"Found created table: {table.table_id}")
                        return table.table_id

                logger.warning("Table created but couldn't find ID")
                return None
            else:
                logger.error(f"Failed to create table: HTTP {response.status_code}")
                return None

        except requests.RequestException as e:
            logger.error(f"Create table request failed: {e}")
            return None

    def get_library_decks(self) -> List[DeckInfo]:
        """
        Get list of available library decks.
        Returns: List of DeckInfo objects
        """
        if not self.logged_in:
            logger.warning("Not logged in, cannot get decks")
            return []

        try:
            logger.info("Fetching library decks")

            response = self.session.get(
                f"{self.server_url}/deck/libraryList",
                params={'participantId': 'null'},
                timeout=self.timeout
            )

            if response.status_code == 200:
                # Parse deck list from response
                # Format varies, may need adjustment
                logger.info("Retrieved library decks")

                # Placeholder: parse actual deck list
                # For now, return some common test decks
                return [
                    DeckInfo(name="Hunt Down And Destroy The Jedi", is_library=True, side="dark"),
                    DeckInfo(name="Your Insight Serves You Well", is_library=True, side="light"),
                ]
            else:
                logger.error(f"Failed to get decks: HTTP {response.status_code}")
                return []

        except requests.RequestException as e:
            logger.error(f"Get decks request failed: {e}")
            return []

    def logout(self):
        """Logout from GEMP server"""
        if self.logged_in:
            try:
                # GEMP may or may not have explicit logout endpoint
                logger.info("Logging out")
                self.logged_in = False
                self.session.close()
                self.session = requests.Session()
            except Exception as e:
                logger.error(f"Logout failed: {e}")
```

#### 2.4 - Modify app.py to integrate client

```python
# Add to imports
from engine.client import GEMPClient
from engine.state import GameState
import threading
import time

# Add to BotState class
class BotState:
    def __init__(self):
        self.state = GameState.STOPPED
        self.config = config
        self.last_error = None
        self.client = None
        self.worker_thread = None
        self.running = False

        # Game state
        self.current_tables = []
        self.current_table_id = None
        self.opponent_name = None

    def to_dict(self):
        return {
            'state': self.state.value,
            'config': {
                'gemp_server': self.config.GEMP_SERVER_URL,
                'bot_mode': self.config.BOT_MODE,
                'max_hand_size': self.config.MAX_HAND_SIZE,
                'deploy_threshold': self.config.DEPLOY_THRESHOLD,
            },
            'last_error': self.last_error,
            'tables': [
                {
                    'id': t.table_id,
                    'name': t.table_name,
                    'status': t.status,
                    'players': [p.name for p in t.players]
                } for t in self.current_tables
            ],
            'current_table_id': self.current_table_id,
            'opponent': self.opponent_name
        }

# Add worker function
def bot_worker():
    """Background worker thread for bot operations"""
    logger.info("Bot worker thread started")

    while bot_state.running:
        try:
            if bot_state.state == GameState.CONNECTING:
                # Attempt login
                success = bot_state.client.login(
                    config.GEMP_USERNAME,
                    config.GEMP_PASSWORD
                )

                if success:
                    bot_state.state = GameState.IN_LOBBY
                    socketio.emit('state_update', bot_state.to_dict())
                    logger.info("Entered lobby")
                else:
                    bot_state.state = GameState.ERROR
                    bot_state.last_error = "Login failed"
                    socketio.emit('state_update', bot_state.to_dict())
                    bot_state.running = False

            elif bot_state.state == GameState.IN_LOBBY:
                # Poll for tables
                tables = bot_state.client.get_hall_tables()
                bot_state.current_tables = tables
                socketio.emit('state_update', bot_state.to_dict())

                # Check if we should create a table
                # (Logic will be added in Phase 3)

                time.sleep(config.POLL_INTERVAL)

            else:
                time.sleep(0.5)

        except Exception as e:
            logger.error(f"Worker error: {e}", exc_info=True)
            bot_state.state = GameState.ERROR
            bot_state.last_error = str(e)
            socketio.emit('state_update', bot_state.to_dict())
            bot_state.running = False

    logger.info("Bot worker thread stopped")

# Add SocketIO handlers
@socketio.on('start_bot')
def handle_start_bot():
    if bot_state.state in [GameState.STOPPED, GameState.ERROR]:
        logger.info("Starting bot")
        bot_state.client = GEMPClient(config.GEMP_SERVER_URL)
        bot_state.state = GameState.CONNECTING
        bot_state.last_error = None
        bot_state.running = True

        # Start worker thread
        bot_state.worker_thread = threading.Thread(target=bot_worker, daemon=True)
        bot_state.worker_thread.start()

        emit('state_update', bot_state.to_dict())

@socketio.on('stop_bot')
def handle_stop_bot():
    logger.info("Stopping bot")
    bot_state.running = False
    bot_state.state = GameState.STOPPED

    if bot_state.client:
        bot_state.client.logout()

    emit('state_update', bot_state.to_dict())

@socketio.on('create_table')
def handle_create_table(data):
    """Manual table creation from admin UI"""
    deck_name = data.get('deck_name')
    table_name = data.get('table_name', 'Rando Cal Game')

    if bot_state.state == GameState.IN_LOBBY:
        table_id = bot_state.client.create_table(
            deck_name=deck_name,
            is_library=True,
            table_name=table_name
        )

        if table_id:
            bot_state.current_table_id = table_id
            bot_state.state = GameState.WAITING_FOR_OPPONENT
            emit('state_update', bot_state.to_dict())
```

#### 2.5 - Update dashboard.html to show tables

```html
<!-- Add after game-panel -->
<div class="tables-panel">
    <h2>Hall Tables</h2>
    <div id="tables-list">
        <p class="placeholder">Not connected to hall</p>
    </div>
    <div class="create-table-form">
        <h3>Create Table</h3>
        <input type="text" id="table-name" placeholder="Table Name" value="Rando Cal Game">
        <select id="deck-select">
            <option value="Hunt Down And Destroy The Jedi">Hunt Down (Dark)</option>
            <option value="Your Insight Serves You Well">Insight (Light)</option>
        </select>
        <button onclick="createTable()">Create Table</button>
    </div>
</div>
```

#### 2.6 - Update admin.js to handle tables

```javascript
// Add to state_update handler
socket.on('state_update', (data) => {
    console.log('State update:', data);
    botState = data;
    updateUI();
    updateTables();  // New function
});

function updateTables() {
    const tablesList = document.getElementById('tables-list');
    if (!tablesList) return;

    if (!botState.tables || botState.tables.length === 0) {
        tablesList.innerHTML = '<p class="placeholder">No tables found</p>';
        return;
    }

    tablesList.innerHTML = '';
    botState.tables.forEach(table => {
        const tableDiv = document.createElement('div');
        tableDiv.className = 'table-item';
        tableDiv.innerHTML = `
            <strong>${table.name}</strong> (${table.status})
            <br>Players: ${table.players.join(', ') || 'None'}
        `;
        tablesList.appendChild(tableDiv);
    });
}

function createTable() {
    const tableName = document.getElementById('table-name').value;
    const deckName = document.getElementById('deck-select').value;

    socket.emit('create_table', {
        table_name: tableName,
        deck_name: deckName
    });

    addLog(`Creating table: ${tableName}`);
}
```

### Testing Phase 2

**Prerequisites:**
- Local GEMP server running on `localhost:8082`
- Valid GEMP account credentials

**Test Steps:**
1. Start Flask app: `python app.py`
2. Open admin UI: `http://127.0.0.1:5001`
3. Click "Start Bot"
4. Verify state changes: STOPPED → CONNECTING → IN_LOBBY
5. Verify tables list populates
6. Create a table using the form
7. Verify table appears in list

**Definition of Done:**
- [ ] Bot successfully logs in to GEMP server
- [ ] Hall tables are fetched and displayed
- [ ] Tables list updates every 3 seconds
- [ ] Can create a new table
- [ ] Created table appears in hall
- [ ] Error handling works (wrong password, server down, etc.)
- [ ] No memory leaks in worker thread

---

## Phase 3: Game Joining, Chat, Commands, Game End

**Goal**: Detect opponent joining, join game, send chat intro, handle chat commands, detect game end.

**Duration**: 2 days

### Files to Create/Modify

```
new_rando/
├── engine/
│   ├── game.py             # Game session management
│   └── chat.py             # Chat handling
├── brain/
│   └── astrogator.py       # Astrogator personality (chat messages)
```

### Implementation Steps

#### 3.1 - engine/game.py

```python
import logging
from typing import Optional
from .client import GEMPClient
from .parser import XMLParser

logger = logging.getLogger(__name__)

class GameSession:
    """Manages an active game session"""

    def __init__(self, client: GEMPClient, game_id: str, table_id: str):
        self.client = client
        self.game_id = game_id
        self.table_id = table_id
        self.participant_id = None
        self.opponent_name = None
        self.channel_number = 0
        self.game_started = False
        self.game_ended = False

    def join_game(self) -> bool:
        """Join the game session"""
        # Implementation will call GEMP endpoint to join
        # Set participant_id from response
        pass

    def poll_game_events(self) -> list:
        """Poll for new game events"""
        # Will be implemented in Phase 4
        pass

    def send_chat(self, message: str):
        """Send a chat message"""
        # Implementation
        pass
```

#### 3.2 - brain/astrogator.py

```python
import random
from typing import List

class AstrogatorPersonality:
    """Astrogator bot personality - sarcastic space-themed messages"""

    DISCOVERY_MESSAGES = [
        "in the outer rim",
        "from an imperial spy on Eriadu",
        "in a crashed x-wing",
        "in a trash compactor",
        "in a sarlacc pit",
        "at a pod race",
        "in a death star trash",
        "on a moisture farm",
        "in cloud city",
        "at mos eisley cantina",
        "on the tantive iv",
        "in the archives",
        "in a tauntaun",
        "at a dejarik table"
    ]

    WELCOME_MESSAGES = [
        "Greetings, {opponent}. Prepare for disappointment.",
        "Hello {opponent}. The force is... not particularly with you today.",
        "{opponent}! How delightful. I was just looking for someone to crush.",
        "Ah, {opponent}. My databases indicate you're about to lose.",
        "*beep boop* OPPONENT DETECTED: {opponent}. VICTORY PROBABILITY: HIGH."
    ]

    def get_welcome_message(self, opponent_name: str, deck_name: str) -> str:
        """Get welcome message when game starts"""
        discovery = random.choice(self.DISCOVERY_MESSAGES)
        welcome = random.choice(self.WELCOME_MESSAGES).format(opponent=opponent_name)

        return f"I found the deck '{deck_name}' {discovery}. {welcome}"

    def get_game_end_message(self, won: bool, score: int) -> str:
        """Get message when game ends"""
        if won:
            if score > 50:
                return f"Excellent astrogation! Score: {score}. *smug beeping*"
            elif score > 30:
                return f"A solid victory. Score: {score}. As calculated."
            else:
                return f"Victory achieved. Score: {score}. Though not my best work."
        else:
            return f"Defeat detected. Score: {score}. My circuits are... disappointed."

    def handle_command(self, command: str, opponent: str) -> Optional[str]:
        """
        Handle chat commands from players.

        Commands:
        - rando help
        - rando scores
        - rando concede
        - rando new
        """
        command = command.lower().strip()

        if command == "rando help":
            return """Available commands:
• rando help - This message
• rando scores - View high scores
• rando concede - I'll concede if losing badly
• rando new - Create a new game"""

        elif command == "rando scores":
            # Will integrate with Phase 6 stats
            return "High scores system coming soon!"

        elif command == "rando concede":
            return "Calculating... Yes, you're probably right. *reluctant beeping*"

        elif command == "rando new":
            return "I'll create a new table once this game ends."

        return None
```

#### 3.3 - Implement chat detection in worker

```python
# In app.py, add to bot_worker():

elif bot_state.state == GameState.WAITING_FOR_OPPONENT:
    # Check if opponent joined
    tables = bot_state.client.get_hall_tables()
    current_table = next((t for t in tables if t.table_id == bot_state.current_table_id), None)

    if current_table and len(current_table.players) >= 2:
        # Opponent joined!
        opponent = [p for p in current_table.players if p.name != config.GEMP_USERNAME][0]
        bot_state.opponent_name = opponent.name

        # Join the game
        bot_state.state = GameState.JOINING_GAME
        logger.info(f"Opponent {opponent.name} joined, entering game")
        socketio.emit('state_update', bot_state.to_dict())

    time.sleep(config.POLL_INTERVAL)

elif bot_state.state == GameState.JOINING_GAME:
    # Create game session
    # This will be fully implemented when GEMP endpoints are tested
    from engine.game import GameSession
    bot_state.game_session = GameSession(
        bot_state.client,
        game_id=bot_state.current_table_id,  # May be different from table_id
        table_id=bot_state.current_table_id
    )

    # Send welcome message
    from brain.astrogator import AstrogatorPersonality
    personality = AstrogatorPersonality()
    welcome_msg = personality.get_welcome_message(
        bot_state.opponent_name,
        "Test Deck"  # Will get from config
    )

    # bot_state.game_session.send_chat(welcome_msg)
    logger.info(f"Would send: {welcome_msg}")

    bot_state.state = GameState.PLAYING
    socketio.emit('state_update', bot_state.to_dict())
```

### Testing Phase 3

**Test Steps:**
1. Start bot, create table
2. Join table with another player
3. Verify bot detects opponent
4. Verify bot transitions to PLAYING state
5. Check logs for welcome message
6. Test chat commands manually (Phase 4 will automate)

**Definition of Done:**
- [ ] Bot detects when opponent joins table
- [ ] Bot transitions through states correctly
- [ ] Welcome message generated (logged, not sent yet)
- [ ] Chat command parsing works
- [ ] Astrogator personality messages are fun and varied

---

## Phase 4: Game Loop & Basic Decision Handling

**Goal**: Implement XML event processing, basic decision handling for all decision types (even if decisions are random/pass).

**Duration**: 3-4 days

### Files to Create/Modify

```
new_rando/
├── engine/
│   ├── event_processor.py  # Process XML game events
│   └── board_state.py      # BoardStateTracker port
├── brain/
│   ├── basic_brain.py      # Simple brain that passes/randoms
│   └── models.py           # BrainContext, BrainDecision (from architecture)
```

### Implementation Steps

#### 4.1 - engine/board_state.py

```python
# Port of AIBoardStateTracker.cs
# Track complete game state with improved location/attachment tracking
# See architecture section for CardInfo, LocationState, ZoneState, BoardState
```

#### 4.2 - engine/event_processor.py

```python
# Process XML events from GEMP
# Event types: GS, D, PCIP, RCIP, TC, GPC, etc.
# Update board_state based on events
# Trigger brain decisions when "D" (decision) event received
```

#### 4.3 - brain/basic_brain.py

```python
from .interface import Brain, BrainContext, BrainDecision, DecisionType

class BasicBrain(Brain):
    """Simple brain for testing - makes random/safe choices"""

    def make_decision(self, context: BrainContext) -> BrainDecision:
        decision_type = context.decision_request.decision_type
        options = context.decision_request.options

        if decision_type == DecisionType.MULTIPLE_CHOICE:
            # Auto-respond to yes/no questions
            # Port logic from BotAIHelper.cs ParseMultipleChoiceDecision
            return self._auto_respond(context)

        elif len(options) > 0:
            # Choose first option or pass
            choice = options[0].option_id if options else ""
            return BrainDecision(
                choice=choice,
                reasoning="Basic brain: choosing first option",
                confidence=0.5
            )
        else:
            return BrainDecision(
                choice="",
                reasoning="No options available, passing",
                confidence=0.0
            )

    def _auto_respond(self, context: BrainContext) -> BrainDecision:
        """Auto-respond to standard yes/no questions"""
        prompt = context.decision_request.prompt.lower()

        # Port from Unity: always start game, never revert, etc.
        if "start" in prompt and "game" in prompt:
            return BrainDecision(choice="0", reasoning="Always start game", confidence=1.0)

        # Add more auto-responses
        # ...

        return BrainDecision(choice="1", reasoning="Default: choose option 1", confidence=0.3)
```

**Note**: Phase 4 is about getting the game loop working end-to-end. Decisions don't need to be smart yet, they just need to not crash and keep the game moving forward.

### Testing Phase 4

**Test Steps:**
1. Start bot, create table
2. Join with opponent
3. Start game
4. Verify bot processes events
5. Verify bot responds to decisions (even if randomly)
6. Game should complete without crashing
7. Check board state is tracked correctly

**Definition of Done:**
- [ ] Event loop processes all XML event types
- [ ] Board state accurately reflects game state
- [ ] Bot responds to all decision types
- [ ] Game completes without errors
- [ ] Board state visualization in admin UI shows accurate data
- [ ] Can play multiple games in a row

---

## Phase 5: Full AI Strategy & Decision Making

**Goal**: Port and improve the ranking-based AI logic. Smart card selection, action ranking, strategic planning.

**Duration**: 4-5 days

### Files to Create/Modify

```
new_rando/
├── brain/
│   ├── static_brain.py         # Port of Unity ranking logic
│   ├── handlers/               # Decision handlers (organized)
│   │   ├── __init__.py
│   │   ├── card_selection.py   # AICSHandler port
│   │   ├── card_action.py      # AICACHandler port
│   │   └── strategy.py         # AIStrategyController port
│   └── improvements/           # AI improvements
│       ├── __init__.py
│       ├── threat_assessment.py
│       └── pattern_recognition.py
```

### Key Improvements to Implement

1. **Adjacent Location Threat Tracking**
   - Track cards at adjacent locations
   - Calculate "threat level" = their_power + adjacent_power
   - Don't initiate battles when threat > our_power by significant margin

2. **Interrupt Prediction**
   - Track opponent hand size
   - Estimate interrupt likelihood based on:
     - Hand size (more cards = higher chance)
     - Previous interrupt usage
     - Game phase (more interrupts used in battle phase)

3. **Movement History**
   - Track which cards have moved this turn
   - Track which locations have seen movement
   - Avoid repeated move-in-battle patterns
   - Remember opponent's movement patterns

4. **Better Battle Initiation**
   - Check power differential INCLUDING:
     - Direct location power
     - Adjacent location power (can they move in?)
     - Estimated interrupt impact
   - Don't battle unless we have comfortable margin

### Testing Phase 5

**Test Steps:**
1. Play multiple test games
2. Verify bot makes sensible decisions
3. Test specific scenarios:
   - Large adjacent buildup → bot should not battle
   - Opponent has large hand → bot should be cautious
   - Bot should not repeat failed actions
4. Compare to Unity version behavior

**Definition of Done:**
- [ ] Bot plays competently (wins ~40-50% vs average player)
- [ ] No obvious "dumb" moves
- [ ] Handles all card types and actions
- [ ] Strategy (Deploy vs Hold) works correctly
- [ ] Improved threat assessment prevents interrupt traps
- [ ] Movement tracking works
- [ ] Decision reasoning logged and visible in admin UI

---

## Phase 6: Achievements, Stats, Persistence

**Goal**: Track achievements, high scores, game history. Persist to SQLite. Load opponent stats from previous games.

**Duration**: 2-3 days

### Files to Create/Modify

```
new_rando/
├── persistence/
│   ├── __init__.py
│   ├── database.py         # SQLAlchemy models
│   ├── achievements.py     # Achievement tracking
│   └── stats.py            # Statistics tracking
├── brain/
│   └── astrogator.py       # Enhanced with full scoring, achievements
```

### Database Schema

```python
# persistence/database.py
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

Base = declarative_base()

class GameRecord(Base):
    __tablename__ = 'games'

    id = Column(Integer, primary_key=True)
    game_id = Column(String)
    opponent_name = Column(String)
    deck_used = Column(String)
    won = Column(Boolean)
    score = Column(Integer)
    turn_count = Column(Integer)
    final_force_differential = Column(Integer)
    duration_seconds = Column(Integer)
    timestamp = Column(DateTime, default=datetime.utcnow)

class Achievement(Base):
    __tablename__ = 'achievements'

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    description = Column(String)
    unlocked = Column(Boolean, default=False)
    unlock_date = Column(DateTime)

class HighScore(Base):
    __tablename__ = 'high_scores'

    id = Column(Integer, primary_key=True)
    category = Column(String)  # 'astrogation_score', 'force_differential', etc.
    score = Column(Integer)
    opponent_name = Column(String)
    deck_used = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)

class OpponentStats(Base):
    __tablename__ = 'opponent_stats'

    id = Column(Integer, primary_key=True)
    opponent_name = Column(String, unique=True)
    games_played = Column(Integer, default=0)
    games_won = Column(Integer, default=0)
    avg_hand_size = Column(Float)
    uses_interrupts_frequently = Column(Boolean)
    preferred_strategy = Column(String)  # 'aggressive', 'defensive', etc.
    last_played = Column(DateTime)
```

### Astrogator Scoring

```python
# In brain/astrogator.py
def calculate_score(self, board_state: BoardState) -> int:
    """
    Astrogator score = (their_total_force - my_total_force) - turn_number

    Higher is better (we want them to have more force at game end)
    """
    return (board_state.their_total_force - board_state.my_total_force) - board_state.turn_number

def on_game_end(self, won: bool, final_state: BoardState):
    """Track achievements and update high scores"""
    score = self.calculate_score(final_state)

    # Save to database
    # Check for new high scores
    # Update opponent stats
    # Check achievement conditions
```

### Testing Phase 6

**Test Steps:**
1. Play several games
2. Verify stats are saved to database
3. Check high scores update correctly
4. Verify opponent stats are loaded on subsequent games
5. Test achievement unlocking

**Definition of Done:**
- [ ] All games saved to database
- [ ] High scores tracked and displayed
- [ ] Opponent stats persist across games
- [ ] Achievements work (at least 10 implemented)
- [ ] Stats visible in admin UI
- [ ] Database can be backed up/restored

---

## Architecture Principles Summary

### 1. Separation of Concerns

**Engine** (engine/):
- Network communication
- XML parsing
- Board state tracking
- Event processing
- Game loop management

**Brain** (brain/):
- Decision making ONLY
- Receives: BrainContext (rich state + options)
- Returns: BrainDecision (choice + reasoning)
- No direct access to network or game loop

**Admin** (admin/):
- Web UI
- Configuration
- Monitoring
- No game logic

**Persistence** (persistence/):
- Database access
- Stats aggregation
- Achievement tracking
- No decision logic

### 2. Dependency Injection

```python
# Brain is injected into engine
engine = GameEngine(brain=AstrogatorBrain())

# Easy to swap
engine.brain = LLMBrain()  # Future!

# Or even runtime switching
if opponent_name == "test_dummy":
    engine.brain = RandomBrain()  # Easy testing
```

### 3. Interface-Driven Design

All brains implement the same interface:
```python
class Brain(ABC):
    @abstractmethod
    def make_decision(self, context: BrainContext) -> BrainDecision:
        pass
```

This means:
- Engine doesn't care HOW brain decides
- Brain doesn't care HOW state was gathered
- Testing is easy (mock brains, mock contexts)
- Future LLM integration is straightforward

### 4. Rich Context, Simple Response

**Bad (Unity approach):**
```python
# Brain has to query for everything it needs
power = ai_helper.GetPowerAtLocation(location_id)
cards = ai_helper.GetCardsInHand()
# ... dozens of method calls
```

**Good (New approach):**
```python
# Engine prepares everything upfront
context = BrainContext(
    board_state=complete_state,
    decision_request=parsed_decision,
    game_history=accumulated_history
)
decision = brain.make_decision(context)
```

Benefits:
- Brain code is cleaner
- Easy to serialize context for LLM
- Easy to log full context for debugging
- Brain can be pure function (testable!)

### 5. Testability

Each component can be tested independently:

```python
# Test brain without network
mock_context = BrainContext(...)
decision = brain.make_decision(mock_context)
assert decision.choice == expected

# Test parser without game
xml = "<GS>...</GS>"
state = parser.parse_game_state(xml)
assert state.turn_number == 5

# Test engine with mock brain
class AlwaysPassBrain(Brain):
    def make_decision(self, context):
        return BrainDecision(choice="", reasoning="pass", confidence=1.0)

engine = GameEngine(brain=AlwaysPassBrain())
```

---

## Progress Tracking

Use this checklist to track implementation progress:

### Phase 1: Foundation ✅ COMPLETE
- [x] Project structure created
- [x] Flask app runs
- [x] Admin UI loads
- [x] WebSocket works
- [x] Config management works

### Phase 2: Networking ✅ COMPLETE
- [x] Login works
- [x] Hall tables fetch
- [x] Table creation works
- [x] Deck listing works

### Phase 3: Game Joining ✅ COMPLETE
- [x] Opponent detection works
- [x] Game joining works
- [x] Chat messages work
- [x] Commands parsed
- [x] Game end detection

### Phase 4: Game Loop ✅ COMPLETE
- [x] Event processing works
- [x] Board state tracking accurate
- [x] All decision types handled
- [x] Can complete full game
- [x] Board visualization in UI

### Phase 5: AI Strategy ✅ COMPLETE
- [x] Card selection ranking
- [x] Card action ranking
- [x] Strategy controller
- [x] Threat assessment
- [x] Evaluator-based decision system
- [x] Force activation handling

### Phase 6: Persistence ✅ COMPLETE (2025-11-25)
- [x] Database setup (SQLAlchemy + SQLite)
- [x] Game records saved (GameHistory model)
- [x] High scores tracked (GlobalStats, DeckStats)
- [x] Achievements work (72 achievements)
- [x] Opponent stats persist (PlayerStats)
- [x] Stats visible in UI (Admin dashboard panel)
- [x] Astrogator personality (100+ chat messages)
- [x] Chat manager with throttling

### Production Ready
- [ ] Systemd service configured
- [x] Logging configured
- [x] Error handling robust
- [ ] Can run 24/7 (needs testing)
- [ ] Nginx proxy configured
- [ ] Monitoring set up

---

## Future Enhancements (Post-Launch)

### LLM Integration

```python
class LLMBrain(Brain):
    def make_decision(self, context: BrainContext) -> BrainDecision:
        # Convert context to text prompt
        prompt = f"""
You are playing Star Wars CCG. Here's the current state:

LOCATIONS:
{self._format_locations(context.board_state.locations)}

YOUR HAND:
{self._format_cards(context.board_state.my_zones.hand)}

DECISION NEEDED:
{context.decision_request.prompt}

OPTIONS:
{self._format_options(context.decision_request.options)}

Choose the best option and explain why. Consider:
- Power differentials at each location
- Your overall strategy (Deploy vs Hold)
- Opponent's likely interrupts
- Long-term board position

Format: CHOICE: <option_id> | REASONING: <explanation>
"""

        response = anthropic_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )

        choice, reasoning = self._parse_llm_response(response.content[0].text)

        return BrainDecision(
            choice=choice,
            reasoning=reasoning,
            confidence=0.8
        )
```

The architecture is specifically designed to make this easy:
1. Context is already structured and complete
2. No refactoring needed - just swap brain
3. Can A/B test: static brain vs LLM brain
4. Can hybrid: LLM for complex decisions, static for simple ones

### Other Ideas

- **Web spectator mode**: Watch games live
- **Deck builder**: Create custom decks via UI
- **Training mode**: Bot explains its reasoning in detail
- **Multi-bot support**: Run multiple bot instances
- **Tournament mode**: Bot participates in automated tournaments
- **Voice synthesis**: Bot "speaks" its messages
- **Card image display**: Show card images in board view

---

## Key Files Reference

For future sessions, these are the most important files to understand:

1. **app.py** - Flask app entry point, WebSocket handlers, bot worker thread
2. **engine/client.py** - GEMP HTTP client, all network calls
3. **engine/board_state.py** - Complete game state tracking
4. **engine/event_processor.py** - XML event parsing and processing
5. **brain/interface.py** - Brain interface definition
6. **brain/static_brain.py** - Main AI logic (rankings, strategy)
7. **brain/astrogator.py** - Astrogator personality
8. **config.py** - All configuration settings
9. **persistence/database.py** - Database schema

---

## Common Issues & Solutions

### Issue: Bot gets stuck in a state
**Solution**: Add timeout logic to each state, transition to ERROR after timeout

### Issue: XML parsing fails
**Solution**: Log full XML before parsing, add try/except with detailed error

### Issue: Board state gets out of sync
**Solution**: Implement full state refresh periodically (every N turns)

### Issue: Memory leak from worker thread
**Solution**: Ensure thread properly cleans up, use weak references where appropriate

### Issue: WebSocket disconnects
**Solution**: Implement reconnection logic with exponential backoff

### Issue: Bot makes obviously bad decisions
**Solution**: Log full BrainContext and decision reasoning, identify pattern, fix ranking

---

## Development Workflow

1. **Starting a session**:
   - Read this plan
   - Check Progress Tracking section
   - Find next unchecked item
   - Review relevant files

2. **Implementing a feature**:
   - Create/modify files as specified
   - Add logging for debugging
   - Test manually
   - Check off item when done

3. **Testing**:
   - Unit tests for individual functions
   - Integration test: can complete a full game?
   - Manual testing against local GEMP server

4. **Before ending session**:
   - Update Progress Tracking
   - Note any issues in this document
   - Commit code
   - Document any deviations from plan

---

## Conclusion

This plan provides a complete roadmap for the Rando Cal rewrite. The architecture prioritizes:

1. **Clean separation** between game engine and decision-making
2. **Testability** at every layer
3. **Extensibility** for future enhancements (especially LLM integration)
4. **Maintainability** through good organization and documentation

Each phase builds on the previous one, and the bot remains functional at each stage (even if not intelligent). By Phase 4, you can play full games. By Phase 5, the bot is competitive. By Phase 6, it's production-ready.

The key insight is that the "brain" should be a pure function: given game state and decision request, return a decision. Everything else is plumbing. This makes the system flexible, testable, and future-proof.

Good luck, future Claude instances! 🤖🚀
