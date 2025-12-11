import os
from dataclasses import dataclass

# Try to load credentials from credentials.py (not committed to git)
# Note: Do NOT name this file "secrets.py" - it shadows Python's stdlib secrets module
try:
    from credentials import GEMP_PASSWORD as _SECRETS_PASSWORD
except ImportError:
    _SECRETS_PASSWORD = None

@dataclass
class Config:
    """Configuration for the Rando Cal bot"""

    # Flask settings
    SECRET_KEY: str = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-prod')
    HOST: str = '127.0.0.1'  # localhost only, nginx will proxy
    PORT: int = 5001  # Custom port to avoid conflicts
    DEBUG: bool = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'

    # GEMP server settings
    GEMP_SERVER_URL: str = os.environ.get('GEMP_SERVER_URL', 'http://localhost:8082/gemp-swccg-server/')
    GEMP_USERNAME: str = os.environ.get('GEMP_USERNAME', 'rando_cal')
    # Password priority: env var > credentials.py > empty (will fail login)
    GEMP_PASSWORD: str = os.environ.get('GEMP_PASSWORD', _SECRETS_PASSWORD or '')

    # Bot settings
    BOT_MODE: str = 'astrogator'  # 'standard', 'astrogator', 'scrap_trader'
    GAME_POLL_INTERVAL: int = 1   # seconds between game update polls (fast)
    HALL_POLL_INTERVAL: int = 10  # seconds between hall/lobby polls (slow)
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
    DEPLOY_THRESHOLD: int = 6        # Don't deploy until we can deploy this much power

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
    # Optimized 2024-12-01: Reduced delays by ~20% to improve play speed
    # Analysis showed 60% of server responses are <0.5s, so delays were bottleneck
    # Request rate ~26/min stays well under 40/min safety limit
    NETWORK_DELAY_QUICK: float = 0.75      # Delay when noLongDelay=true (quick response expected)
    NETWORK_DELAY_NORMAL: float = 1.5      # Delay when noLongDelay=false (bot should "think")
    NETWORK_DELAY_BACKGROUND: float = 30.0 # Delay for background requests (hall, cardInfo)
    NETWORK_DELAY_MIN: float = 0.2         # Absolute minimum between any requests

    # Hall polling optimization
    HALL_CHECK_INTERVAL_DURING_GAME: int = 60  # Only check hall every N seconds during game

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
