"""
Strategy Configuration

Loads and provides access to strategy weights from JSON configuration files.
This allows tuning AI behavior without code changes.

Usage:
    from engine.strategy_config import get_config

    # Get a value (with fallback default)
    value = get_config().get('deploy_strategy', 'deploy_threshold', default=4)

    # Get evaluator weight
    bonus = get_config().get_weight('deploy', 'planned_target_bonus', default=200)

Environment:
    STRATEGY_CONFIG - Path to JSON config file (default: configs/baseline.json)
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default config path (relative to this file)
# Use production.json as default - this includes GamePlan, adaptive strategy, etc.
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "configs" / "production.json"


class StrategyConfig:
    """
    Loads and provides access to strategy weights from JSON.

    Thread-safe singleton that caches the loaded config.
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the strategy config.

        Args:
            config_path: Path to JSON config file. If not provided, uses
                        STRATEGY_CONFIG env var or default baseline.json.
        """
        if config_path:
            self.path = Path(config_path)
        else:
            env_path = os.environ.get('STRATEGY_CONFIG')
            if env_path:
                self.path = Path(env_path)
            else:
                self.path = DEFAULT_CONFIG_PATH

        self._config: Dict[str, Any] = {}
        self._loaded = False
        self._load()

    def _load(self):
        """Load configuration from JSON file."""
        try:
            if self.path.exists():
                with open(self.path, 'r', encoding='utf-8') as f:
                    self._config = json.load(f)
                self._loaded = True
                logger.info(f"Loaded strategy config from: {self.path}")
                logger.info(f"  Config name: {self._config.get('name', 'unknown')}")
                logger.info(f"  Config version: {self._config.get('version', 'unknown')}")
                # Log key config values for verification
                self._log_key_values()
            else:
                logger.warning(f"Strategy config not found: {self.path}, using defaults")
                self._config = {}
                self._loaded = False
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in strategy config {self.path}: {e}")
            self._config = {}
            self._loaded = False
        except Exception as e:
            logger.error(f"Error loading strategy config: {e}")
            self._config = {}
            self._loaded = False

    def _log_key_values(self):
        """Log key config values for verification."""
        # Deploy strategy
        ds = self._config.get('deploy_strategy', {})
        logger.info(f"  [deploy_strategy] early_game_threshold={ds.get('early_game_threshold')}, "
                   f"deploy_overkill_threshold={ds.get('deploy_overkill_threshold')}, "
                   f"early_game_turns={ds.get('early_game_turns')}")

        # Battle strategy
        bs = self._config.get('battle_strategy', {})
        logger.info(f"  [battle_strategy] favorable_threshold={bs.get('favorable_threshold')}, "
                   f"power_diff_for_battle={bs.get('power_diff_for_battle')}, "
                   f"react_threat_threshold={bs.get('react_threat_threshold')}")

        # Move strategy
        ms = self._config.get('move_strategy', {})
        logger.info(f"  [move_strategy] attack_score_base={ms.get('attack_score_base')}, "
                   f"attack_power_advantage={ms.get('attack_power_advantage')}")

        # Draw strategy
        drs = self._config.get('draw_strategy', {})
        logger.info(f"  [draw_strategy] target_hand_size={drs.get('target_hand_size')}, "
                   f"force_starved_activation={drs.get('force_starved_activation')}")

        # Global
        gl = self._config.get('global', {})
        logger.info(f"  [global] chaos_percent={gl.get('chaos_percent')}")

    def reload(self):
        """Reload configuration from file."""
        self._load()

    @property
    def name(self) -> str:
        """Get config name."""
        return self._config.get('name', 'default')

    @property
    def version(self) -> str:
        """Get config version."""
        return self._config.get('version', '0.0.0')

    @property
    def is_loaded(self) -> bool:
        """Check if config was successfully loaded."""
        return self._loaded

    def get(self, section: str, key: str, default: Any = None) -> Any:
        """
        Get a configuration value.

        Args:
            section: Config section (e.g., 'deploy_strategy', 'battle_strategy')
            key: Key within section (e.g., 'deploy_threshold')
            default: Default value if not found

        Returns:
            The config value or default
        """
        section_data = self._config.get(section, {})
        return section_data.get(key, default)

    def get_weight(self, evaluator: str, key: str, default: float = 0.0) -> float:
        """
        Get an evaluator weight.

        Shorthand for get('evaluator_weights', evaluator, {}).get(key, default)

        Args:
            evaluator: Evaluator name (e.g., 'deploy', 'battle', 'move')
            key: Weight key (e.g., 'planned_target_bonus')
            default: Default value if not found

        Returns:
            The weight value as a float
        """
        weights = self._config.get('evaluator_weights', {})
        evaluator_weights = weights.get(evaluator, {})
        return float(evaluator_weights.get(key, default))

    def get_global(self, key: str, default: Any = None) -> Any:
        """
        Get a global configuration value.

        Args:
            key: Global config key (e.g., 'chaos_percent')
            default: Default value if not found

        Returns:
            The config value or default
        """
        return self.get('global', key, default)

    def get_section(self, section: str) -> Dict[str, Any]:
        """
        Get an entire config section as a dict.

        Args:
            section: Config section name (e.g., 'monte_carlo')

        Returns:
            The section dict, or empty dict if not found
        """
        return self._config.get(section, {})

    def as_dict(self) -> Dict[str, Any]:
        """Get the full config as a dictionary."""
        return self._config.copy()


# Global singleton instance
_config: Optional[StrategyConfig] = None


def get_config() -> StrategyConfig:
    """
    Get the global strategy config singleton.

    Returns:
        The StrategyConfig instance
    """
    global _config
    if _config is None:
        _config = StrategyConfig()
    return _config


def set_config_path(path: str):
    """
    Set the config path and reload.

    Used for testing or switching between configs at runtime.

    Args:
        path: Path to JSON config file
    """
    global _config
    _config = StrategyConfig(path)


def reload_config():
    """Reload the current configuration from file."""
    global _config
    if _config:
        _config.reload()
