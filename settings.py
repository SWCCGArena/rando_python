"""
User Settings Persistence

Saves user preferences to a JSON file so they persist across restarts.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Settings file location (in logs directory, not committed to git)
SETTINGS_FILE = Path(__file__).parent / "logs" / "user_settings.json"

# In local fast mode, auto-start by default for bot-vs-bot testing
_local_fast = os.environ.get('LOCAL_FAST_MODE', 'false').lower() == 'true'

# Default settings
DEFAULTS = {
    "gemp_server_url": "http://localhost/gemp-swccg-server/",
    "auto_start": _local_fast,  # Auto-start in local fast mode
}

# In-memory cache to avoid reading file on every call
_settings_cache: Optional[Dict[str, Any]] = None


def load_settings(force_reload: bool = False) -> Dict[str, Any]:
    """Load settings from file, returning defaults if file doesn't exist.

    Uses in-memory cache to avoid reading file on every call.
    """
    global _settings_cache

    if _settings_cache is not None and not force_reload:
        return _settings_cache.copy()

    if not SETTINGS_FILE.exists():
        logger.info(f"No settings file found, using defaults")
        _settings_cache = DEFAULTS.copy()
        return _settings_cache.copy()

    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f)
            logger.debug(f"Loaded settings from {SETTINGS_FILE}")
            # Merge with defaults to ensure all keys exist
            merged = DEFAULTS.copy()
            merged.update(settings)
            _settings_cache = merged
            return _settings_cache.copy()
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")
        _settings_cache = DEFAULTS.copy()
        return _settings_cache.copy()


def save_settings(settings: Dict[str, Any]) -> bool:
    """Save settings to file and update cache."""
    global _settings_cache
    try:
        # Ensure logs directory exists
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=2)
        logger.debug(f"Saved settings to {SETTINGS_FILE}")
        # Update cache
        _settings_cache = settings.copy()
        return True
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        return False


def get_setting(key: str, default: Any = None) -> Any:
    """Get a single setting value."""
    settings = load_settings()
    return settings.get(key, default)


def set_setting(key: str, value: Any) -> bool:
    """Set a single setting value and save."""
    settings = load_settings()
    settings[key] = value
    return save_settings(settings)
