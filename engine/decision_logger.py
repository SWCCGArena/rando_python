"""
Decision XML Logger

Captures full decision XML for every decision the bot makes.
This enables post-game analysis, debugging, and training data extraction.

Log files are rotated per-game alongside the main log.
"""

import logging
import os
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Optional

# Get username for log filename
_log_username = os.environ.get('GEMP_USERNAME', 'rando')

# Log directory (same as main logs)
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Decision log file path
DECISION_LOG_PATH = LOG_DIR / f"{_log_username}_decisions.log"

# Create dedicated decision logger
decision_logger = logging.getLogger("decision_xml")
decision_logger.setLevel(logging.INFO)
decision_logger.propagate = False  # Don't propagate to root logger

# File handler for decision log
_file_handler: Optional[logging.FileHandler] = None


def _ensure_handler():
    """Lazily initialize the file handler."""
    global _file_handler
    if _file_handler is None:
        _file_handler = logging.FileHandler(str(DECISION_LOG_PATH))
        _file_handler.setFormatter(logging.Formatter('%(message)s'))  # Raw format
        decision_logger.addHandler(_file_handler)


def log_decision(
    decision_element: ET.Element,
    decision_id: str,
    decision_type: str,
    decision_text: str,
    chosen_value: str,
    chosen_text: str = "",
    reasoning: str = "",
    score: float = 0.0,
    turn: int = 0,
    phase: str = "",
    is_my_turn: bool = True,
):
    """
    Log a decision with full XML and metadata.

    Args:
        decision_element: The full decision XML element
        decision_id: Decision ID from GEMP
        decision_type: Type (CARD_ACTION_CHOICE, INTEGER, etc.)
        decision_text: The prompt text shown to the bot
        chosen_value: The value sent back to GEMP
        chosen_text: Human-readable description of the choice
        reasoning: Evaluator reasoning summary
        score: Final score that led to this choice
        turn: Current turn number
        phase: Current game phase
        is_my_turn: Whether it's the bot's turn
    """
    _ensure_handler()

    timestamp = datetime.now().isoformat()

    # Convert XML element to string
    try:
        xml_str = ET.tostring(decision_element, encoding='unicode')
        # Pretty-print with basic indentation
        import xml.dom.minidom
        dom = xml.dom.minidom.parseString(xml_str)
        xml_pretty = dom.toprettyxml(indent="  ")
        # Remove the XML declaration line
        xml_lines = xml_pretty.split('\n')[1:]
        xml_pretty = '\n'.join(line for line in xml_lines if line.strip())
    except Exception as e:
        xml_pretty = f"<error>Could not format XML: {e}</error>"

    # Build log entry
    entry_lines = [
        f"=== DECISION {decision_id} @ {timestamp} ===",
        f"Type: {decision_type}",
        f"Turn: {turn}, Phase: {phase}, MyTurn: {is_my_turn}",
        f"Text: {decision_text[:200]}{'...' if len(decision_text) > 200 else ''}",
        "",
        xml_pretty,
        "",
        f"Chosen: {chosen_value}" + (f" ({chosen_text})" if chosen_text else ""),
    ]

    if reasoning:
        entry_lines.append(f"Reasoning: {reasoning}")
    if score != 0.0:
        entry_lines.append(f"Score: {score:.1f}")

    entry_lines.append("=" * 50)
    entry_lines.append("")  # Blank line between entries

    entry = '\n'.join(entry_lines)
    decision_logger.info(entry)


def rotate_decision_log(opponent_name: str = None, won: bool = None):
    """
    Rotate the decision log file after a game ends.

    Args:
        opponent_name: Name of the opponent (for filename)
        won: Whether the bot won (for filename)
    """
    global _file_handler

    try:
        if _file_handler is None:
            return  # No log to rotate

        # Generate new filename matching main log format
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        result_str = "win" if won else "loss" if won is not None else "unknown"
        opponent_str = opponent_name.replace(' ', '_') if opponent_name else "unknown"

        new_filename = f"{_log_username}_{timestamp}_vs_{opponent_str}_{result_str}_decisions.log"
        new_path = LOG_DIR / new_filename

        # Flush and close current handler
        _file_handler.flush()
        _file_handler.close()
        decision_logger.removeHandler(_file_handler)

        # Rename if file exists and has content
        if DECISION_LOG_PATH.exists() and DECISION_LOG_PATH.stat().st_size > 0:
            shutil.move(str(DECISION_LOG_PATH), str(new_path))

        # Create new handler
        _file_handler = logging.FileHandler(str(DECISION_LOG_PATH))
        _file_handler.setFormatter(logging.Formatter('%(message)s'))
        decision_logger.addHandler(_file_handler)

    except Exception as e:
        # Use standard logging for errors (decision_logger might be broken)
        logging.getLogger(__name__).error(f"Error rotating decision log: {e}")


def flush():
    """Flush the decision log."""
    if _file_handler:
        _file_handler.flush()
