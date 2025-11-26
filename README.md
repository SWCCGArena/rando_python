# Rando Cal - SWCCG Bot

A Python/Flask bot for playing Star Wars Customizable Card Game (SWCCG) on the [GEMP](https://gemp.starwarsccg.org/) online platform.

## Features

- **Automated Gameplay**: Joins games, makes decisions, and plays full games autonomously
- **Web Admin UI**: Monitor games, view stats, and control the bot via browser
- **Evaluator-Based AI**: Modular scoring system for deploy, move, draw, and battle decisions
- **Statistics Tracking**: Win/loss records, achievements, player history
- **In-Game Chat**: Responds to chat commands and posts achievements
- **Persistent Database**: SQLite storage for stats and game history

## Prerequisites

### Card Data (Required)

The bot requires SWCCG card data from the official card JSON repository:

```bash
# Clone the card data repository (sibling to this repo)
cd /path/to/parent/directory
git clone git@github.com:swccgpc/swccg-card-json.git
```

Expected directory structure:
```
parent_directory/
├── new_rando/           # This repository
└── swccg-card-json/     # Card data repository
    ├── Dark.json
    └── Light.json
```

The bot looks for card JSON at `../swccg-card-json/` relative to its install location. You can override this by setting `CARD_JSON_DIR` in your environment or `config.py`.

### Python Requirements

- Python 3.10+
- pip

## Quick Start

```bash
# 1. Clone this repository
git clone <this-repo-url> new_rando
cd new_rando

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure credentials
export GEMP_USERNAME="your_bot_username"
export GEMP_PASSWORD="your_bot_password"
export GEMP_SERVER_URL="https://gemp.starwarsccg.org/gemp-swccg-server/"

# 5. Run the bot
python app.py

# 6. Access admin UI at http://127.0.0.1:5001
```

## Configuration

Configuration can be set via environment variables or by editing `config.py`:

| Variable | Description | Default |
|----------|-------------|---------|
| `GEMP_USERNAME` | Bot's GEMP username | `rando_cal` |
| `GEMP_PASSWORD` | Bot's GEMP password | (required) |
| `GEMP_SERVER_URL` | GEMP server endpoint | `https://gemp.starwarsccg.org/gemp-swccg-server/` |
| `CARD_JSON_DIR` | Path to swccg-card-json | `../swccg-card-json` |
| `LOG_LEVEL` | Logging verbosity | `INFO` |

## Architecture

The bot separates concerns into distinct layers:

```
Flask App (app.py)
    ├── Engine (engine/)     # Game mechanics, networking, state
    ├── Brain (brain/)       # AI decision-making
    ├── Admin (admin/)       # Web UI
    └── Persistence (persistence/)  # Database, stats
```

### Key Components

- **Engine**: GEMP HTTP client, XML parsing, board state tracking, event processing
- **Brain**: Evaluator-based AI with pluggable strategy modules
- **Evaluators**: Scoring systems for different decision types (deploy, move, draw, battle)
- **Admin**: Flask web interface for monitoring and control

### Evaluator System

The AI uses a scoring-based system where each possible action is evaluated:

1. Decision XML is parsed into a `DecisionContext`
2. Multiple evaluators score possible actions based on game state
3. Highest-scored action is selected
4. Reasoning is logged for debugging

See `EVALUATOR_SYSTEM.md` for details on adding new evaluators.

## Directory Structure

```
new_rando/
├── app.py                  # Flask application entry point
├── config.py               # Configuration management
├── engine/
│   ├── client.py          # GEMP HTTP client
│   ├── parser.py          # XML parsing
│   ├── board_state.py     # Game state tracking
│   ├── event_processor.py # XML event processing
│   ├── decision_handler.py # Decision routing
│   ├── card_loader.py     # Card JSON loading
│   └── evaluators/        # AI decision evaluators
│       ├── deploy_evaluator.py
│       ├── move_evaluator.py
│       ├── draw_evaluator.py
│       └── ...
├── brain/
│   ├── interface.py       # Brain ABC
│   └── static_brain.py    # Main AI implementation
├── admin/
│   ├── templates/         # Jinja2 templates
│   └── static/            # CSS/JS
├── persistence/
│   ├── models.py          # SQLAlchemy models
│   ├── database.py        # DB connection
│   └── stats_repository.py # Stats queries
├── data/                   # SQLite database (gitignored)
└── logs/                   # Log files (gitignored)
```

## Development

```bash
# Run with debug mode
FLASK_DEBUG=True python app.py

# Run tests
pytest

# Code formatting
black .
flake8
```

## Chat Commands

Players can interact with the bot via in-game chat:

- `rando help` - Show available commands
- `rando stats` - Show bot's overall statistics
- `rando achievements` - List unlockable achievements

## Documentation

| File | Content |
|------|---------|
| `EVALUATOR_SYSTEM.md` | How to add new AI evaluators |
| `DECISION_ARCHITECTURE.md` | Decision system design |
| `IMPLEMENTATION_PLAN.md` | Development roadmap |
| `CLAUDE.md` | AI assistant context file |

## Contributing

Contributions welcome! The evaluator system makes it easy to improve AI behavior:

1. Create a new evaluator in `engine/evaluators/`
2. Export it in `engine/evaluators/__init__.py`
3. Register it in `decision_handler.py`

## License

Same as original rando_cal project.

## Credits

Original bot by the SWCCG community. Python rewrite with contributions from the community.

## Links

- [GEMP Platform](https://gemp.starwarsccg.org/)
- [SWCCG Players Committee](https://www.starwarsccg.org/)
- [Card JSON Repository](https://github.com/swccgpc/swccg-card-json)
