import os
from dataclasses import dataclass

# Try to load credentials from credentials.py (not committed to git)
# Note: Do NOT name this file "secrets.py" - it shadows Python's stdlib secrets module
try:
    from credentials import GEMP_PASSWORD as _SECRETS_PASSWORD
except ImportError:
    _SECRETS_PASSWORD = None

try:
    from credentials import GEMP_USERNAME as _SECRETS_USERNAME
except ImportError:
    _SECRETS_USERNAME = None

@dataclass
class Config:
    """Configuration for the Rando Cal bot"""

    # Flask settings
    SECRET_KEY: str = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-prod')
    HOST: str = os.environ.get('BOT_HOST', '0.0.0.0')  # 0.0.0.0 = all interfaces, 127.0.0.1 = localhost only
    PORT: int = int(os.environ.get('BOT_PORT', '5001'))  # Configurable for running multiple instances
    DEBUG: bool = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'

    # GEMP server settings
    GEMP_SERVER_URL: str = os.environ.get('GEMP_SERVER_URL', 'http://localhost/gemp-swccg-server/')
    GEMP_USERNAME: str = os.environ.get('GEMP_USERNAME', 'rando_cal')
    # Password priority: env var > credentials.py > empty (will fail login)
    GEMP_PASSWORD: str = os.environ.get('GEMP_PASSWORD', _SECRETS_PASSWORD or '')

    # Local fast mode for bot-vs-bot testing (set LOCAL_FAST_MODE=true)
    _local_fast = os.environ.get('LOCAL_FAST_MODE', 'false').lower() == 'true'

    # Bot settings
    BOT_MODE: str = 'astrogator'  # 'standard', 'astrogator', 'scrap_trader'
    # Poll intervals - minimal for local fast mode, production uses 3s to stay under rate limits
    GAME_POLL_INTERVAL: float = 0.05 if _local_fast else 3.0   # seconds between game update polls
    HALL_POLL_INTERVAL: float = 1.0 if _local_fast else 10.0   # seconds between hall/lobby polls
    TABLE_NAME: str = 'Bot Table'
    GAME_FORMAT: str = 'open'  # 'open', 'legacy', etc.

    # AI configuration - Basic
    MAX_HAND_SIZE: int = 16          # Hard cap - strongly avoid drawing above this
    HAND_SOFT_CAP: int = 12          # Soft cap - start penalizing draws above this
    CHAOS_PERCENT: int = 25          # Random action chance

    # AI configuration - Deploy Strategy
    # Minimum TOTAL power we need to be able to deploy this turn before committing
    # characters to a location. Prevents deploying lone weak characters that get
    # overwhelmed. From C# deployThresholdSlider (typical value 6-8).
    # Controlled by admin UI slider.
    DEPLOY_THRESHOLD: int = 4        # Don't deploy until we can deploy this much power (reduced from 6 for early aggression)

    # AI configuration - Force Economy
    FORCE_GEN_TARGET: int = 6        # Target force generation (icons)
    MAX_RESERVE_CHECKS: int = 2      # Max reserve deck peeks per turn

    # AI configuration - Battle Strategy
    BATTLE_FAVORABLE_THRESHOLD: int = 4   # Power advantage for "good odds" battle
    BATTLE_DANGER_THRESHOLD: int = -6     # Power disadvantage to avoid/retreat

    # AI configuration - Deployment Strategy
    DEPLOY_OVERKILL_THRESHOLD: int = 8    # Power advantage where we stop reinforcing
    DEPLOY_COMFORTABLE_THRESHOLD: int = 4 # Power advantage where reinforcing is low priority
    BATTLE_FORCE_RESERVE: int = 1         # Force to reserve for initiating battle after deploy
    DEPLOY_EARLY_GAME_THRESHOLD: int = 110  # Minimum score to deploy in turns 1-3 (hold back weak plays)
    DEPLOY_EARLY_GAME_TURNS: int = 3        # How many turns count as "early game"

    # Network rate limiting (matching web client behavior)
    # These delays make the bot behave more like a human player
    # Request rate ~26/min stays well under 40/min safety limit on prod
    NETWORK_DELAY_QUICK: float = 0.05 if _local_fast else 0.75      # Delay when noLongDelay=true
    NETWORK_DELAY_NORMAL: float = 0.1 if _local_fast else 1.5       # Delay when noLongDelay=false
    NETWORK_DELAY_BACKGROUND: float = 5.0 if _local_fast else 30.0  # Delay for background requests
    NETWORK_DELAY_MIN: float = 0.02 if _local_fast else 0.2         # Absolute minimum between requests

    # Hall polling optimization
    HALL_CHECK_INTERVAL_DURING_GAME: int = 5 if _local_fast else 60  # Only check hall every N seconds during game

    # Paths
    BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR: str = os.path.join(BASE_DIR, 'data')
    LOG_DIR: str = os.path.join(BASE_DIR, 'logs')
    CARD_JSON_DIR: str = os.path.join(os.path.dirname(BASE_DIR), 'swccg-card-json')

    # Database
    @property
    def DATABASE_URL(self) -> str:
        return f'sqlite:///{os.path.join(self.DATA_DIR, "rando.db")}'

    def __post_init__(self):
        """Ensure directories exist"""
        os.makedirs(self.DATA_DIR, exist_ok=True)
        os.makedirs(self.LOG_DIR, exist_ok=True)

# Create global config instance
config = Config()
config.__post_init__()
