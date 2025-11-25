# Rando Cal - Python Rewrite

Modern Python/Flask rewrite of the famous rando_cal SWCCG bot.

## Quick Start

```bash
# Setup
cd /opt/gemp/rando_cal_working/new_rando
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
export GEMP_USERNAME="rando_blu"
export GEMP_PASSWORD="your_password"
export GEMP_SERVER_URL="http://localhost:8082/gemp-swccg-server/"

# Run
python app.py

# Access admin UI
# http://127.0.0.1:5001
```

## Project Status

**Current Phase**: Phase 1 (Foundation) - In Progress

See `IMPLEMENTATION_PLAN.md` for complete roadmap.

## Architecture

This rewrite separates concerns into distinct layers:

- **Engine**: Game mechanics, networking, state tracking
- **Brain**: Decision-making (swappable AI implementations)
- **Admin**: Web UI, monitoring, configuration
- **Persistence**: Database, stats, achievements

The key innovation is the **Brain Interface** - decision-making is completely decoupled from the game engine, allowing for:
- Easy testing
- Multiple AI strategies
- Future LLM integration
- A/B testing different approaches

## Directory Structure

```
new_rando/
├── app.py              # Flask application entry point
├── config.py           # Configuration management
├── engine/             # Game engine (networking, state, events)
├── brain/              # AI brains (static, astrogator, future LLM)
├── admin/              # Web admin interface
├── persistence/        # Database, achievements, stats
├── data/               # SQLite database (gitignored)
├── logs/               # Log files (gitignored)
└── venv/               # Virtual environment (gitignored)
```

## Development

See `IMPLEMENTATION_PLAN.md` for:
- Detailed implementation steps for each phase
- Architecture decisions and rationale
- Testing procedures
- Progress tracking

## Original Unity Version

The original C# Unity version is in `../GempArenaBot/`. This rewrite aims to:
- Improve code organization and maintainability
- Add modern web admin interface
- Enable 24/7 server deployment
- Improve AI decision-making
- Support future LLM integration

## License

Same as original rando_cal project.

## Credits

Original bot by the SWCCG community. Python rewrite by the same community with love. 💙
