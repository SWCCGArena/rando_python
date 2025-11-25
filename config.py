import os
from dataclasses import dataclass

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
    GEMP_PASSWORD: str = os.environ.get('GEMP_PASSWORD', 'battmann')

    # Bot settings
    BOT_MODE: str = 'astrogator'  # 'standard', 'astrogator', 'scrap_trader'
    POLL_INTERVAL: int = 3  # seconds between GEMP polls
    TABLE_NAME: str = 'Bot Table: Random'
    GAME_FORMAT: str = 'open'  # 'open', 'legacy', etc.

    # AI configuration - Basic
    MAX_HAND_SIZE: int = 16          # Hard cap - strongly avoid drawing above this
    HAND_SOFT_CAP: int = 12          # Soft cap - start penalizing draws above this
    DEPLOY_THRESHOLD: int = 3        # Minimum power to deploy
    CHAOS_PERCENT: int = 25          # Random action chance

    # AI configuration - Force Economy
    FORCE_GEN_TARGET: int = 6        # Target force generation (icons)
    MAX_RESERVE_CHECKS: int = 2      # Max reserve deck peeks per turn

    # AI configuration - Battle Strategy
    BATTLE_FAVORABLE_THRESHOLD: int = 4   # Power advantage for "good odds" battle
    BATTLE_DANGER_THRESHOLD: int = -6     # Power disadvantage to avoid/retreat

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
