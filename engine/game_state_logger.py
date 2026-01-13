"""
Game State XML Logger

Captures ALL XML updates from GEMP server for complete game replay and training data.
Separate from decision_logger which only captures decision XML.

Log files are rotated per-game alongside the main log.

Events captured:
- P (Participant) - Player info
- TC (Turn Change) - Turn progression
- GPC (Game Phase Change) - Phase tracking
- PCIP (Put Card In Play) - Card deployments
- RCIP (Replace Card In Play) - Card state changes
- MCIP (Move Card In Play) - Movement
- GS (Game State) - Force/power totals
- D (Decision) - Skipped (handled by decision_logger)
"""

import logging
import os
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Optional
import time

# Get username for log filename
_log_username = os.environ.get('GEMP_USERNAME', 'rando')

# Log directory (same as main logs)
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Game state log file path
GAME_STATE_LOG_PATH = LOG_DIR / f"{_log_username}_gamestate.xml"

# Track game start time for relative timestamps
_game_start_time: Optional[float] = None

# File handle for writing
_log_file: Optional[object] = None
_initialized: bool = False


def _ensure_initialized():
    """Lazily initialize the log file."""
    global _log_file, _initialized, _game_start_time

    if _initialized:
        return

    try:
        _log_file = open(GAME_STATE_LOG_PATH, 'w', encoding='utf-8')
        _game_start_time = time.time()

        # Write XML header
        _log_file.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        _log_file.write(f'<game_log started="{datetime.now().isoformat()}" bot="{_log_username}">\n')
        _log_file.flush()
        _initialized = True
    except Exception as e:
        logging.getLogger(__name__).error(f"Failed to initialize game state logger: {e}")


def log_game_event(event_element: ET.Element, event_type: str):
    """
    Log a game state XML event.

    Args:
        event_element: The XML element from GEMP
        event_type: Event type tag (P, TC, GPC, PCIP, etc.)
    """
    global _game_start_time

    # Skip decision events (handled by decision_logger)
    if event_type == 'D':
        return

    _ensure_initialized()

    if _log_file is None:
        return

    try:
        # Calculate relative timestamp
        elapsed = time.time() - _game_start_time if _game_start_time else 0.0

        # Convert element to string
        xml_str = ET.tostring(event_element, encoding='unicode')

        # Write event with timestamp and type
        _log_file.write(f'  <event t="{elapsed:.3f}" type="{event_type}">\n')
        _log_file.write(f'    {xml_str}\n')
        _log_file.write('  </event>\n')
        _log_file.flush()

    except Exception as e:
        logging.getLogger(__name__).error(f"Error logging game event: {e}")


def log_raw_xml(raw_xml: str, context: str = "update"):
    """
    Log raw XML string (for cases where we have string, not element).

    Args:
        raw_xml: Raw XML string from GEMP
        context: Context description (e.g., "initial_state", "update")
    """
    global _game_start_time

    _ensure_initialized()

    if _log_file is None:
        return

    try:
        elapsed = time.time() - _game_start_time if _game_start_time else 0.0

        _log_file.write(f'  <raw_xml t="{elapsed:.3f}" context="{context}">\n')
        _log_file.write('    <![CDATA[\n')
        _log_file.write(raw_xml)
        _log_file.write('\n    ]]>\n')
        _log_file.write('  </raw_xml>\n')
        _log_file.flush()

    except Exception as e:
        logging.getLogger(__name__).error(f"Error logging raw XML: {e}")


def set_opponent(opponent_name: str):
    """
    Record opponent name in the log.

    Args:
        opponent_name: Name of the opponent player
    """
    _ensure_initialized()

    if _log_file is None:
        return

    try:
        elapsed = time.time() - _game_start_time if _game_start_time else 0.0
        _log_file.write(f'  <opponent t="{elapsed:.3f}" name="{opponent_name}"/>\n')
        _log_file.flush()
    except Exception as e:
        logging.getLogger(__name__).error(f"Error logging opponent: {e}")


def rotate_game_state_log(opponent_name: str = None, won: bool = None):
    """
    Rotate the game state log file after a game ends.

    Args:
        opponent_name: Name of the opponent (for filename)
        won: Whether the bot won (for filename)
    """
    global _log_file, _initialized, _game_start_time

    try:
        if not _initialized or _log_file is None:
            return

        # Write closing tag
        _log_file.write('</game_log>\n')
        _log_file.flush()
        _log_file.close()

        # Generate new filename matching main log format
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        result_str = "win" if won else "loss" if won is not None else "unknown"
        opponent_str = opponent_name.replace(' ', '_') if opponent_name else "unknown"

        new_filename = f"{_log_username}_{timestamp}_vs_{opponent_str}_{result_str}_gamestate.xml"
        new_path = LOG_DIR / new_filename

        # Rename if file exists and has content
        if GAME_STATE_LOG_PATH.exists() and GAME_STATE_LOG_PATH.stat().st_size > 0:
            shutil.move(str(GAME_STATE_LOG_PATH), str(new_path))
            logging.getLogger(__name__).info(f"Game state log rotated to: {new_filename}")

        # Reset state for next game
        _log_file = None
        _initialized = False
        _game_start_time = None

    except Exception as e:
        logging.getLogger(__name__).error(f"Error rotating game state log: {e}")
        # Reset state even on error
        _log_file = None
        _initialized = False
        _game_start_time = None


def flush():
    """Flush the game state log."""
    if _log_file:
        try:
            _log_file.flush()
        except Exception:
            pass
