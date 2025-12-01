"""
Rando Cal - Flask Application

Main entry point for the bot. Runs Flask web server with WebSocket
support for admin UI, and manages the bot worker greenlet that handles
game operations using eventlet for async I/O.
"""

from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import logging
import os
import time
import requests
import xml.etree.ElementTree as ET
from config import config
from engine.state import GameState
from engine.client import GEMPClient
from engine.decision_handler import DecisionHandler, DecisionResult
from engine.network_coordinator import NetworkCoordinator
from engine.board_state import BoardState
from engine.event_processor import EventProcessor
from engine.strategy_controller import StrategyController
from engine.table_manager import TableManager, TableManagerConfig, ConnectionMonitor
from brain import StaticBrain
from brain.astrogator_brain import AstrogatorBrain
from brain.achievements import AchievementTracker
from brain.chat_manager import ChatManager
from brain.command_handler import CommandHandler
from persistence import init_db, StatsRepository
import settings

# Setup logging
os.makedirs(config.LOG_DIR, exist_ok=True)
LOG_FILE_PATH = os.path.join(config.LOG_DIR, 'rando.log')
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.FileHandler(LOG_FILE_PATH),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def rotate_game_log(opponent_name: str = None, won: bool = None):
    """
    Rotate the log file after a game ends.

    Renames the current rando.log to a timestamped file preserving game info,
    then starts a fresh rando.log for the next game.

    Args:
        opponent_name: Name of the opponent (for filename)
        won: Whether the bot won (for filename)
    """
    from datetime import datetime
    import shutil

    try:
        # Generate new filename with timestamp and game info
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        result_str = "win" if won else "loss" if won is not None else "unknown"
        opponent_str = opponent_name.replace(' ', '_') if opponent_name else "unknown"

        # New filename: rando_20231127_143052_vs_PlayerName_win.log
        new_filename = f"rando_{timestamp}_vs_{opponent_str}_{result_str}.log"
        new_path = os.path.join(config.LOG_DIR, new_filename)

        # Get the root logger and find the file handler
        root_logger = logging.getLogger()
        file_handler = None
        handler_index = -1

        for i, handler in enumerate(root_logger.handlers):
            if isinstance(handler, logging.FileHandler) and 'rando.log' in handler.baseFilename:
                file_handler = handler
                handler_index = i
                break

        if file_handler:
            # Flush and close the current file handler
            file_handler.flush()
            file_handler.close()

            # Remove from logger temporarily
            root_logger.removeHandler(file_handler)

            # Rename the log file (if it exists and has content)
            if os.path.exists(LOG_FILE_PATH) and os.path.getsize(LOG_FILE_PATH) > 0:
                shutil.move(LOG_FILE_PATH, new_path)
                logger.info(f"📁 Rotated log to: {new_filename}")

            # Create a new file handler for the fresh log
            new_handler = logging.FileHandler(LOG_FILE_PATH)
            new_handler.setFormatter(logging.Formatter(LOG_FORMAT))
            new_handler.setLevel(logging.INFO)

            # Add the new handler
            root_logger.addHandler(new_handler)

            logger.info(f"📝 Started new log file for next game")
        else:
            logger.warning("Could not find file handler for log rotation")

    except Exception as e:
        logger.error(f"Error rotating log: {e}")

# Initialize database
db_path = os.path.join(config.LOG_DIR, 'rando_stats.db')
db_url = f'sqlite:///{db_path}'
init_db(db_url)
logger.info(f"📊 Database initialized: {db_path}")

# Repair any data inconsistencies from previous bugs
from persistence.stats_repository import StatsRepository
_repair_repo = StatsRepository()
_repaired = _repair_repo.repair_achievement_counts()
if _repaired > 0:
    logger.info(f"🔧 Repaired {_repaired} player achievement counts")

# Load user settings and apply to config
user_settings = settings.load_settings()
if user_settings.get('gemp_server_url'):
    config.GEMP_SERVER_URL = user_settings['gemp_server_url']
    logger.info(f"📡 Loaded GEMP server from settings: {config.GEMP_SERVER_URL}")

# Initialize Flask with custom template and static folders
app = Flask(__name__,
            template_folder='admin/templates',
            static_folder='admin/static')
app.config['SECRET_KEY'] = config.SECRET_KEY
app.config['TEMPLATES_AUTO_RELOAD'] = True  # Force template reload on every request

# Initialize SocketIO
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')


class BotState:
    """
    Global bot state.

    This will be expanded in later phases with game client,
    board state, brain, etc.
    """

    def __init__(self):
        self.state = GameState.STOPPED
        self.config = config
        self.last_error = None

        # Will be populated in later phases
        self.client = None
        self.running = False
        self.game_session = None

        # Persistence layer
        self.stats_repo = StatsRepository()
        logger.info("📊 Initialized stats repository")

        # Brain for decision-making (Astrogator personality)
        self.brain = AstrogatorBrain()
        logger.info(f"🧠 Initialized brain: {self.brain.get_personality_name()}")

        # Achievement tracking
        self.achievement_tracker = AchievementTracker(self.stats_repo)
        logger.info("🏆 Initialized achievement tracker")

        # Chat manager (client set later when available)
        self.chat_manager = None  # Initialized after client is created

        # Command handler for chat commands (client set later when available)
        self.command_handler = None  # Initialized after client is created

        # Strategy controller for Battle Order rules and location checking
        # Pass config so GameStrategy can use live config values
        self.strategy_controller = StrategyController(config=config)
        logger.info(f"📊 Initialized strategy controller")

        # Table manager for autonomous table lifecycle
        self.table_manager = None  # Initialized after client is created
        self.connection_monitor = None
        logger.info(f"🎯 Table manager will be initialized after login")

        # Phase 4: Board state tracking
        self.board_state = None
        self.event_processor = None

        # Game state (Phase 2+)
        self.current_tables = []
        self.current_table_id = None
        self.opponent_name = None

        # Deck lists
        self.library_decks = []
        self.user_decks = []

        # Game session state
        self.game_id = None
        self.channel_number = 0

        # NetworkCoordinator for rate-limited requests
        self.coordinator = None  # Initialized after client is created

        # Hall polling state (for incremental updates)
        self.hall_channel_number = 0
        self._last_hall_check_during_game = 0.0
        # Use config value for hall check interval during game
        self.HALL_CHECK_INTERVAL_DURING_GAME = getattr(
            self.config, 'HALL_CHECK_INTERVAL_DURING_GAME', 60
        )

    def initialize_table_manager(self):
        """Initialize table manager after client is ready"""
        if self.client and not self.table_manager:
            table_config = TableManagerConfig(
                table_name_prefix=config.TABLE_NAME,
                create_delay_seconds=2.0,
                retry_delay_seconds=5.0,
            )
            self.table_manager = TableManager(self.client, table_config)
            self.connection_monitor = ConnectionMonitor(self.client)
            logger.info(f"🎯 TableManager initialized")

    def initialize_chat_manager(self):
        """Initialize chat manager after client is ready"""
        if self.client and not self.chat_manager:
            self.chat_manager = ChatManager(
                brain=self.brain,
                stats_repo=self.stats_repo,
                client=self.client,
                achievement_tracker=self.achievement_tracker
            )
            logger.info("💬 ChatManager initialized")

    def initialize_command_handler(self):
        """Initialize command handler after client is ready"""
        if self.client and not self.command_handler:
            self.command_handler = CommandHandler(
                client=self.client,
                stats_repo=self.stats_repo,
                bot_username=config.GEMP_USERNAME
            )
            logger.info("🎮 CommandHandler initialized")

    def initialize_coordinator(self):
        """Initialize NetworkCoordinator after client is ready"""
        if self.client and not self.coordinator:
            self.coordinator = NetworkCoordinator(self.client, config=self.config)
            logger.info("📡 NetworkCoordinator initialized")

    def to_dict(self):
        """Convert state to dictionary for JSON serialization"""
        result = {
            'state': self.state.value,
            'config': {
                'gemp_server': self.config.GEMP_SERVER_URL,
                'bot_mode': self.config.BOT_MODE,
                'table_name': self.config.TABLE_NAME,
                'auto_start': settings.get_setting('auto_start', False),
                # Hand management
                'max_hand_size': self.config.MAX_HAND_SIZE,
                'hand_soft_cap': self.config.HAND_SOFT_CAP,
                # Force economy
                'force_gen_target': self.config.FORCE_GEN_TARGET,
                'max_reserve_checks': self.config.MAX_RESERVE_CHECKS,
                # Battle strategy
                'deploy_threshold': self.config.DEPLOY_THRESHOLD,
                'battle_favorable_threshold': self.config.BATTLE_FAVORABLE_THRESHOLD,
                'battle_danger_threshold': self.config.BATTLE_DANGER_THRESHOLD,
            },
            'last_error': self.last_error,
            'tables': [
                {
                    'id': getattr(t, 'table_id', ''),
                    'name': getattr(t, 'table_name', ''),
                    'status': getattr(t, 'status', ''),
                    'players': [getattr(p, 'name', '') for p in getattr(t, 'players', [])]
                } for t in self.current_tables
            ] if hasattr(self, 'current_tables') and self.current_tables else [],
            'current_table_id': self.current_table_id,
            'opponent': self.opponent_name,
            'decks': {
                'library': [{'name': d.name, 'side': d.side} for d in self.library_decks],
                'user': [{'name': d.name, 'side': d.side} for d in self.user_decks]
            }
        }

        # Add board state if in game
        if self.board_state:
            bs = self.board_state
            result['board_state'] = {
                'phase': bs.current_phase,
                'my_turn': bs.is_my_turn(),
                'my_side': bs.my_side,
                'my_player': bs.my_player_name,
                'opponent': bs.opponent_name,

                # Resources
                'force': bs.force_pile,
                'used': bs.used_pile,
                'reserve': bs.reserve_deck,
                'lost': bs.lost_pile,
                'hand_size': len(bs.cards_in_hand),

                # Their resources
                'their_force': bs.their_force_pile,
                'their_used': bs.their_used_pile,
                'their_reserve': bs.their_reserve_deck,
                'their_lost': bs.their_lost_pile,
                'their_hand_size': bs.their_hand_size,

                # Power
                'my_power': bs.total_my_power(),
                'their_power': bs.total_their_power(),
                'power_advantage': bs.power_advantage(),
                'force_advantage': bs.force_advantage(),

                # Current deployment plan (from holistic planner)
                'deploy_plan': bs.deploy_plan_summary if hasattr(bs, 'deploy_plan_summary') and bs.deploy_plan_summary else None,

                # Locations with cards (includes cardId for tracking verification)
                'locations': [{
                    'index': i,
                    'card_id': loc.card_id if hasattr(loc, 'card_id') else None,
                    'system_name': loc.system_name or '',
                    'site_name': loc.site_name or loc.system_name or loc.blueprint_id,
                    'is_site': loc.is_site,
                    'is_space': loc.is_space,
                    'is_ground': loc.is_ground,
                    'my_power': max(0, bs.my_power_at_location(i)),  # Show 0 instead of -1
                    'their_power': max(0, bs.their_power_at_location(i)),  # Show 0 instead of -1
                    'my_cards': [{
                        'card_id': c.card_id,  # CRITICAL: cardId for tracking
                        'name': c.card_title or c.blueprint_id,
                        'blueprint': c.blueprint_id,
                        'power': c.power,
                        'ability': c.ability,
                    } for c in loc.my_cards],
                    'their_cards': [{
                        'card_id': c.card_id,  # CRITICAL: cardId for tracking
                        'name': c.card_title or c.blueprint_id,
                        'blueprint': c.blueprint_id,
                        'power': c.power,
                        'ability': c.ability,
                    } for c in loc.their_cards],
                } for i, loc in enumerate(bs.locations) if loc],

                # Hand (includes cardId for tracking verification)
                'hand': [{
                    'card_id': c.card_id,  # CRITICAL: cardId for tracking
                    'name': c.card_title or c.blueprint_id,
                    'blueprint': c.blueprint_id,
                    'type': c.card_type,
                    'deploy': c.deploy,
                } for c in bs.cards_in_hand],

                # Total cards tracked (for debugging)
                'tracked_cards_count': len(bs.cards_in_play),
            }

        # Add overall bot stats
        if self.stats_repo:
            try:
                overall = self.stats_repo.get_overall_stats()
                result['bot_stats'] = {
                    'total_games': overall.get('total_games', 0),
                    'total_wins': overall.get('total_wins', 0),
                    'total_losses': overall.get('total_losses', 0),
                    'win_rate': round(overall.get('win_rate', 0), 1),
                    'unique_players': overall.get('unique_players', 0),
                    'total_achievements': overall.get('total_achievements_awarded', 0),
                }
            except Exception as e:
                logger.error(f"Error getting bot stats: {e}")
                result['bot_stats'] = None

        return result


# Global bot instance
bot_state = BotState()


def process_events_iteratively(initial_events, game_id, initial_channel_number, client, event_processor=None, max_iterations=100):
    """
    Process game events iteratively, handling decisions that lead to more events.
    Uses a loop instead of recursion to avoid stack overflow and detect infinite loops.

    Args:
        initial_events: List of initial event elements to process
        game_id: Current game ID
        initial_channel_number: Starting channel number
        client: GEMPClient instance
        event_processor: Optional EventProcessor to update board state
        max_iterations: Maximum number of event processing loops to prevent infinite loops.
            Set to 100 because legitimate scenarios (e.g., opponent drawing many cards during
            draw phase) can easily exceed 25 iterations without being an actual loop.
            Real loops are detected much faster by DecisionHandler.should_concede_due_to_loop()
            which tracks repeating decision patterns.

    Returns:
        Updated channel number
    """
    current_cn = initial_channel_number
    events_to_process = list(initial_events)
    iteration = 0
    last_decision_key = None  # Track (decision_id, decision_type, decision_text) tuple
    repeat_count = 0

    while events_to_process and iteration < max_iterations:
        iteration += 1
        current_events = events_to_process
        events_to_process = []

        for i, event in enumerate(current_events):
            event_type = event.get('type', 'unknown')

            # Log important events at INFO, others at DEBUG
            if event_type in ['D', 'TC', 'GPC']:
                logger.info(f"  [Iter {iteration}] Event {i+1}/{len(current_events)}: type={event_type}")
            elif i < 3:
                logger.debug(f"  [Iter {iteration}] Event {i+1}/{len(current_events)}: type={event_type}")

            if event_type == 'D':
                # Decision event - handle it
                decision_type = event.get('decisionType', 'unknown')
                decision_text = event.get('text', '')
                decision_id = event.get('id', '')

                # === CONCEDE CHECK ===
                # Before processing decision, check if we should concede
                board_state_for_decision = event_processor.board_state if event_processor else None
                if board_state_for_decision and hasattr(board_state_for_decision, 'should_concede'):
                    should_concede, concede_reason = board_state_for_decision.should_concede()
                    if should_concede:
                        logger.info(f"🏳️ Concede triggered: {concede_reason}")

                        # Send farewell message (through coordinator for logging)
                        if bot_state.chat_manager:
                            farewell = "Good game! I can no longer meaningfully act, so I'll concede. Until next time!"
                            try:
                                if bot_state.coordinator:
                                    bot_state.coordinator.post_chat_message(game_id, farewell)
                                else:
                                    client.post_chat_message(game_id, farewell)
                            except Exception as e:
                                logger.warning(f"Failed to send concede message: {e}")

                        # NOTE: Don't call on_game_end() here - the normal game_ended
                        # handler will be triggered when server confirms concede,
                        # avoiding double-recording of stats

                        # Execute concede (through coordinator for logging)
                        concede_success = (bot_state.coordinator.concede_game(game_id)
                                          if bot_state.coordinator else client.concede_game(game_id))
                        if concede_success:
                            logger.info("✅ Game conceded successfully")
                            return current_cn
                        else:
                            logger.warning("Failed to concede, continuing game")

                # === CRITICAL LOOP CHECK ===
                # DecisionHandler tracks multi-decision loops (e.g., A→B→A→B cycles)
                # If we're in a critical loop (20+ repeats), concede to avoid hanging
                if DecisionHandler.should_concede_due_to_loop():
                    loop_severity, loop_count = DecisionHandler.get_loop_status()
                    logger.error(f"🚨 CRITICAL DECISION LOOP ({loop_count} repeats) - conceding to avoid hang!")

                    # Send farewell message (through coordinator for logging)
                    if bot_state.chat_manager:
                        farewell = "I appear to be stuck in a decision loop. Conceding to avoid hanging the game. GG!"
                        try:
                            if bot_state.coordinator:
                                bot_state.coordinator.post_chat_message(game_id, farewell)
                            else:
                                client.post_chat_message(game_id, farewell)
                        except Exception as e:
                            logger.warning(f"Failed to send loop-concede message: {e}")

                    # Execute concede (through coordinator for logging)
                    concede_success = (bot_state.coordinator.concede_game(game_id)
                                      if bot_state.coordinator else client.concede_game(game_id))
                    if concede_success:
                        logger.info("✅ Game conceded due to loop")
                        return current_cn
                    else:
                        logger.error("Failed to concede! Breaking event loop as last resort.")
                        return current_cn

                # Log loop status for debugging (DecisionHandler handles loop breaking)
                loop_severity, loop_count = DecisionHandler.get_loop_status()
                if loop_severity != 'none':
                    logger.warning(f"🔄 Loop status: {loop_severity} ({loop_count} repeats)")

                logger.info(f"  [Iter {iteration}] Event {i+1}: DECISION {decision_type} - '{decision_text[:80]}...'")

                # Get decision response - GUARANTEED to return a DecisionResult (never None)
                decision_result = DecisionHandler.handle_decision(
                    event,
                    board_state=board_state_for_decision,
                    brain=bot_state.brain
                )

                # Post the decision using coordinator (applies delay based on noLongDelay)
                # If coordinator not available, fall back to direct client call
                if bot_state.coordinator:
                    response_xml = bot_state.coordinator.post_decision(
                        game_id, current_cn,
                        decision_result.decision_id, decision_result.value,
                        no_long_delay=decision_result.no_long_delay
                    )
                else:
                    response_xml = client.post_decision(
                        game_id, current_cn,
                        decision_result.decision_id, decision_result.value
                    )
                if response_xml:
                    logger.debug(f"  [Iter {iteration}] 📦 Decision response: {len(response_xml)} bytes")
                    # Log the XML for debugging when there might be issues
                    if repeat_count > 0 or iteration > 3:
                        logger.warning(f"  [Iter {iteration}] XML Response (first 500 chars): {response_xml[:500]}")
                    try:
                        resp_root = ET.fromstring(response_xml)
                        if resp_root.tag == 'update':
                            # Update channel number
                            new_cn = int(resp_root.get('cn', current_cn))
                            if new_cn != current_cn:
                                logger.info(f"  [Iter {iteration}] 📈 Channel number: {current_cn} -> {new_cn}")
                                current_cn = new_cn
                            else:
                                logger.debug(f"  [Iter {iteration}] Channel number unchanged: {current_cn}")

                            # Get new events from response - add to queue for next iteration
                            resp_events = resp_root.findall('.//ge')
                            if len(resp_events) > 0:
                                logger.debug(f"  [Iter {iteration}] 🔄 Adding {len(resp_events)} events to queue...")
                                events_to_process.extend(resp_events)
                    except Exception as e:
                        logger.error(f"  [Iter {iteration}] Error parsing decision response: {e}")
            else:
                # Non-decision event - process through EventProcessor
                if event_processor:
                    event_processor.process_event(event)

                # Log important events
                if i < 3 or event_type in ['M', 'GS', 'PCIP', 'RCIP', 'MCIP', 'GPC', 'TC']:
                    logger.debug(f"  [Iter {iteration}] Event {i+1}: {event_type}")

    if iteration >= max_iterations:
        logger.error(f"⚠️  Hit max iterations ({max_iterations}) in event processing!")

        # === CONCEDE ON MAX ITERATIONS ===
        # If we hit max iterations, we're stuck in an unbreakable loop.
        # Concede the game to avoid hanging forever.
        logger.error(f"🚨 CRITICAL: Max iterations reached - conceding game to avoid hang!")

        # Send farewell message (through coordinator for logging)
        if bot_state.chat_manager:
            farewell = "I appear to be stuck in an unbreakable loop. Conceding to avoid hanging the game. GG!"
            try:
                if bot_state.coordinator:
                    bot_state.coordinator.post_chat_message(game_id, farewell)
                else:
                    client.post_chat_message(game_id, farewell)
            except Exception as e:
                logger.warning(f"Failed to send max-iterations concede message: {e}")

        # Execute concede (through coordinator for logging)
        concede_success = (bot_state.coordinator.concede_game(game_id)
                          if bot_state.coordinator else client.concede_game(game_id))
        if concede_success:
            logger.info("✅ Game conceded due to max iterations")
        else:
            logger.error("Failed to concede! Game may be stuck.")

    return current_cn


def do_location_checks(game_id: str, client, board_state, strategy_controller, coordinator=None):
    """
    Perform location checks during Control phase.

    Queries cardInfo for locations to get:
    - Force drain amounts
    - Force icons
    - Battle Order rules

    Ported from C# AIStrategyController.StartNewPhase() + ContinuePendingChecks()

    Args:
        game_id: Current game ID
        client: GEMPClient (fallback if no coordinator)
        board_state: Current BoardState
        strategy_controller: StrategyController
        coordinator: Optional NetworkCoordinator for rate-limited calls
    """
    if not strategy_controller or not board_state:
        return

    # Only check if we're in Control phase
    if not board_state.current_phase or 'Control' not in board_state.current_phase:
        return

    # Get locations to check (smart selection, max 5 per turn)
    # Note: This now also checks _first_control_phase_seen flag
    locations_to_check = strategy_controller.get_locations_to_check(board_state)

    if not locations_to_check:
        logger.debug("📊 No locations to check this turn")
        return

    # Log what we're about to check
    loc_names = [loc.site_name or loc.card_id for loc in locations_to_check]
    logger.info(f"📊 Checking {len(locations_to_check)} location(s) for Battle Order rules: {loc_names}")

    for loc in locations_to_check:
        loc_name = loc.site_name or loc.card_id
        try:
            # Use coordinator for rate-limited cardInfo calls (30s delay between)
            if coordinator:
                html = coordinator.get_card_info(game_id, loc.card_id)
            else:
                html = client.get_card_info(game_id, loc.card_id)

            if html:
                result = strategy_controller.process_location_check(loc.card_id, html)
                strategy_controller.update_location_with_check(loc, result)
                logger.info(f"   📍 Checked '{loc_name}': drain={result.my_drain_amount or 'N/A'}, "
                           f"battle_order={result.has_battle_order} "
                           f"(total checks this game: {strategy_controller._total_checks_this_game})")
        except Exception as e:
            logger.error(f"Error checking location '{loc_name}' ({loc.card_id}): {e}")

    if strategy_controller.under_battle_order_rules:
        logger.info("⚠️  Under Battle Order rules - force drains cost extra!")


# Bot worker greenlet
def bot_worker():
    """
    Background worker greenlet for bot operations.

    This runs in an eventlet greenlet (not a thread) and handles:
    - Connecting to GEMP server
    - Polling for hall tables
    - Creating/joining games (Phase 3+)
    - Playing games (Phase 4+)
    """
    logger.info("🤖 Bot worker greenlet started")

    while bot_state.running:
        try:
            # Check rate limit failsafe
            if bot_state.coordinator and bot_state.coordinator.rate_limit_exceeded:
                logger.error("🚨 Rate limit failsafe triggered - stopping bot")
                bot_state.state = GameState.ERROR
                bot_state.last_error = "Rate limit exceeded - bot stopped for safety"
                bot_state.running = False
                socketio.emit('state_update', bot_state.to_dict(), namespace='/')
                socketio.emit('log_message', {
                    'message': '🚨 RATE LIMIT EXCEEDED - Bot stopped for safety. Check logs.',
                    'level': 'error'
                }, namespace='/')
                break

            logger.debug(f"🔄 Worker loop iteration, state={bot_state.state.value}")
            if bot_state.state == GameState.CONNECTING:
                # Attempt login
                logger.info(f"Connecting to GEMP at {config.GEMP_SERVER_URL}")
                success = bot_state.client.login(
                    config.GEMP_USERNAME,
                    config.GEMP_PASSWORD
                )

                if success:
                    bot_state.state = GameState.IN_LOBBY
                    bot_state.last_error = None

                    # Emit immediate state update (namespace required for background threads)
                    socketio.emit('state_update', bot_state.to_dict(), namespace='/')
                    socketio.emit('log_message', {'message': '✅ Connected to GEMP server', 'level': 'success'}, namespace='/')

                    # Initialize network coordinator FIRST so all requests are logged
                    bot_state.initialize_coordinator()

                    # Load deck lists through coordinator (this can take a moment)
                    logger.info("Loading deck lists...")
                    if bot_state.coordinator:
                        bot_state.library_decks = bot_state.coordinator.get_library_decks()
                        bot_state.user_decks = bot_state.coordinator.get_user_decks()
                    else:
                        bot_state.library_decks = bot_state.client.get_library_decks()
                        bot_state.user_decks = bot_state.client.get_user_decks()
                    logger.info(f"Loaded {len(bot_state.library_decks)} library decks, {len(bot_state.user_decks)} user decks")

                    # Initialize table manager with decks
                    bot_state.initialize_table_manager()
                    if bot_state.table_manager:
                        bot_state.table_manager.set_decks(bot_state.library_decks, bot_state.user_decks)

                    # Initialize chat manager
                    bot_state.initialize_chat_manager()

                    # Initialize command handler
                    bot_state.initialize_command_handler()

                    # Emit updated state with decks
                    socketio.emit('state_update', bot_state.to_dict(), namespace='/')
                    socketio.emit('log_message', {'message': f'📚 Loaded {len(bot_state.library_decks)} library decks, {len(bot_state.user_decks)} user decks', 'level': 'info'}, namespace='/')
                    logger.info("✅ Entered lobby")
                else:
                    bot_state.state = GameState.ERROR
                    bot_state.last_error = "Login failed - check credentials"
                    socketio.emit('state_update', bot_state.to_dict(), namespace='/')
                    socketio.emit('log_message', {'message': '❌ Login failed', 'level': 'error'}, namespace='/')
                    bot_state.running = False

            elif bot_state.state == GameState.IN_LOBBY:
                # Poll for tables and get channel number for incremental updates
                # Use coordinator for rate-limited hall fetches
                if bot_state.coordinator:
                    tables, channel_number = bot_state.coordinator.get_hall_initial(return_channel_number=True)
                else:
                    tables, channel_number = bot_state.client.get_hall_tables(return_channel_number=True)
                bot_state.current_tables = tables
                if channel_number > 0:
                    if bot_state.hall_channel_number != channel_number:
                        logger.info(f"📡 Hall channel number: {bot_state.hall_channel_number} -> {channel_number}")
                    bot_state.hall_channel_number = channel_number
                socketio.emit('state_update', bot_state.to_dict(), namespace='/')

                # Log table count periodically (every 5 polls = 15 seconds)
                if not hasattr(bot_state, '_poll_count'):
                    bot_state._poll_count = 0
                bot_state._poll_count += 1

                if bot_state._poll_count % 5 == 0:
                    logger.debug(f"Hall: {len(tables)} tables")

                # Check if we have an existing table to rejoin
                my_table = None
                for table in tables:
                    # Check if we're in this table
                    if any(p.name == config.GEMP_USERNAME for p in table.players):
                        # Skip finished tables - don't rejoin them!
                        if table.status == 'finished':
                            logger.debug(f"Skipping finished table {table.table_id}")
                            continue  # Skip to next table in the for loop
                        my_table = table
                        break

                if my_table:

                    # Found a table we're in!
                    # Only log/update if this is a new table or state change
                    is_new_table = bot_state.current_table_id != my_table.table_id

                    bot_state.current_table_id = my_table.table_id

                    # Check if we have an opponent
                    opponent = my_table.get_opponent(config.GEMP_USERNAME)
                    if opponent:
                        is_new_opponent = bot_state.opponent_name != opponent.name
                        bot_state.opponent_name = opponent.name

                        if is_new_table or is_new_opponent:
                            logger.info(f"🎮 Rejoined table {my_table.table_id} with opponent: {opponent.name}")
                            socketio.emit('log_message', {'message': f'🎮 Rejoined table with {opponent.name}', 'level': 'success'}, namespace='/')

                        # If game is started, we'd join it (Phase 3+)
                        if my_table.game_id:
                            if is_new_table:
                                logger.info(f"Game in progress: {my_table.game_id} (Phase 3: will join)")
                                socketio.emit('log_message', {'message': 'Game in progress - Phase 3 will handle this', 'level': 'warning'}, namespace='/')

                        bot_state.state = GameState.WAITING_FOR_OPPONENT
                        socketio.emit('state_update', bot_state.to_dict(), namespace='/')
                    else:
                        # Still waiting for opponent
                        if is_new_table:
                            logger.info(f"⏳ Waiting for opponent at table {my_table.table_id}")
                            socketio.emit('log_message', {'message': '⏳ Waiting for opponent to join', 'level': 'info'}, namespace='/')

                        bot_state.state = GameState.WAITING_FOR_OPPONENT
                        socketio.emit('state_update', bot_state.to_dict(), namespace='/')
                else:
                    # No table found - auto-create one!
                    if bot_state.current_table_id is not None:
                        logger.info("Table no longer exists in hall")
                        socketio.emit('log_message', {'message': 'Table closed', 'level': 'info'}, namespace='/')
                        bot_state.current_table_id = None
                        bot_state.opponent_name = None

                    # Use TableManager to auto-create a table
                    if bot_state.table_manager:
                        action = bot_state.table_manager.get_required_action(tables, config.GEMP_USERNAME)
                        if action == 'create_table':
                            logger.info("🔄 Auto-creating table...")
                            socketio.emit('log_message', {'message': '🔄 Auto-creating table...', 'level': 'info'}, namespace='/')

                            # Small delay before creating
                            socketio.sleep(2)

                            table_id = bot_state.table_manager.create_table()
                            if table_id:
                                bot_state.current_table_id = table_id
                                bot_state.state = GameState.WAITING_FOR_OPPONENT
                                logger.info(f"✅ Table created: {table_id}")
                                socketio.emit('log_message', {
                                    'message': f'✅ Table created with deck: {bot_state.table_manager.state.current_deck_name}',
                                    'level': 'success'
                                }, namespace='/')
                            else:
                                logger.warning(f"Table creation failed (attempt {bot_state.table_manager.state.consecutive_failures})")
                                socketio.emit('log_message', {
                                    'message': f'⚠️ Table creation failed, will retry...',
                                    'level': 'warning'
                                }, namespace='/')

                    socketio.emit('state_update', bot_state.to_dict(), namespace='/')

                socketio.sleep(config.HALL_POLL_INTERVAL)

            elif bot_state.state == GameState.WAITING_FOR_OPPONENT:
                # Poll hall to check if opponent joined
                # Use coordinator for rate-limited hall fetches
                if bot_state.coordinator:
                    tables = bot_state.coordinator.get_hall_initial()
                else:
                    tables = bot_state.client.get_hall_tables()
                bot_state.current_tables = tables

                # Find our table
                my_table = None
                for table in tables:
                    if table.table_id == bot_state.current_table_id:
                        my_table = table
                        break

                if my_table:
                    # Check if opponent joined
                    if len(my_table.players) >= 2:
                        opponent = my_table.get_opponent(config.GEMP_USERNAME)
                        if opponent and bot_state.opponent_name != opponent.name:
                            # New opponent joined!
                            bot_state.opponent_name = opponent.name
                            logger.info(f"🎮 Opponent joined: {opponent.name}")
                            socketio.emit('log_message', {'message': f'🎮 Opponent {opponent.name} joined!', 'level': 'success'}, namespace='/')
                            socketio.emit('state_update', bot_state.to_dict(), namespace='/')

                    # Check if game started (has gameId)
                    if my_table.game_id and bot_state.game_id != my_table.game_id:
                        logger.info(f"🎲 Game started! Game ID: {my_table.game_id}")
                        socketio.emit('log_message', {'message': f'🎲 Game started vs {bot_state.opponent_name}!', 'level': 'success'}, namespace='/')

                        # Join the game (through coordinator for logging)
                        if bot_state.coordinator:
                            game_state_xml = bot_state.coordinator.join_game(my_table.game_id)
                        else:
                            game_state_xml = bot_state.client.join_game(my_table.game_id)
                        if game_state_xml:
                            bot_state.game_id = my_table.game_id

                            # Initialize board state tracking
                            # NOTE: my_side starts as None and will be detected from HAND cards
                            # (see event_processor.py SIDE DETECTION section)
                            bot_state.board_state = BoardState(my_player_name=config.GEMP_USERNAME)
                            bot_state.board_state.strategy_controller = bot_state.strategy_controller
                            bot_state.event_processor = EventProcessor(bot_state.board_state)
                            bot_state.strategy_controller.setup()  # Reset for new game
                            logger.info(f"🎮 Board state tracking initialized (side will be detected from cards)")

                            # Reset brain evaluators for new game (clears cached plans)
                            if bot_state.brain and hasattr(bot_state.brain, 'reset_for_new_game'):
                                bot_state.brain.reset_for_new_game()
                            elif bot_state.brain and hasattr(bot_state.brain, 'combined_evaluator'):
                                # StaticBrain: reset evaluators directly
                                for evaluator in bot_state.brain.evaluators:
                                    if hasattr(evaluator, 'reset_for_new_game'):
                                        evaluator.reset_for_new_game()

                            # Reset decision tracker (clears loop detection history)
                            DecisionHandler.reset_tracker()
                            logger.info(f"🧠 Brain evaluators and decision tracker reset for new game")

                            # Set opponent_name EARLY so achievements work for initial cards
                            # The full reset_for_game will be called later with correct side info
                            # We only set the fields needed for achievement checking here
                            if bot_state.chat_manager:
                                bot_state.chat_manager.game_id = bot_state.game_id
                                bot_state.chat_manager.opponent_name = bot_state.opponent_name or "Unknown"
                                logger.info(f"💬 Chat manager early init for achievements (opponent: {bot_state.opponent_name})")

                            # Register callback for card placements (achievements)
                            if bot_state.chat_manager:
                                def on_card_placed(card_title, blueprint_id, zone, owner):
                                    # Only check achievements for cards deployed to board (not hand/deck)
                                    board_zones = ['AT_LOCATION', 'ATTACHED', 'LOCATIONS', 'STACKED_ON']
                                    if zone in board_zones:
                                        bot_state.chat_manager.on_card_deployed(
                                            card_title, blueprint_id, zone, owner,
                                            bot_state.board_state
                                        )
                                bot_state.event_processor.register_card_placed_callback(on_card_placed)

                                # Register callback for battle damage (high score tracking)
                                def on_battle_damage(damage: int):
                                    bot_state.chat_manager.on_battle_damage(damage, bot_state.board_state)
                                bot_state.event_processor.register_battle_damage_callback(on_battle_damage)

                            # Parse initial game state and events
                            try:
                                import xml.etree.ElementTree as ET
                                root = ET.fromstring(game_state_xml)
                                logger.info(f"Join response root tag: {root.tag}, attribs: {root.attrib}")
                                # Root IS the gameState element
                                if root.tag == 'gameState':
                                    cn_raw = root.get('cn', '0')
                                    bot_state.channel_number = int(cn_raw)
                                    logger.info(f"Joined game, raw cn='{cn_raw}', channel number: {bot_state.channel_number}")

                                    # Parse initial game events (just like C# does)
                                    game_events = root.findall('.//ge')
                                    if len(game_events) > 0:
                                        logger.info(f"📬 Initial game state has {len(game_events)} events")
                                        logger.info(f"First event type: {game_events[0].get('type', 'unknown')}")

                                        # Process all events iteratively (handles decisions and their responses)
                                        # Set catching_up flag to skip chat-related callbacks for historical events
                                        logger.info(f"🔄 Processing initial events... (starting cn={bot_state.channel_number})")
                                        bot_state.event_processor.catching_up = True
                                        new_cn = process_events_iteratively(
                                            game_events,
                                            bot_state.game_id,
                                            bot_state.channel_number,
                                            bot_state.client,
                                            bot_state.event_processor
                                        )
                                        bot_state.event_processor.catching_up = False
                                        logger.info(f"✅ Initial events processed, cn: {bot_state.channel_number} -> {new_cn}")
                                        bot_state.channel_number = new_cn
                                    else:
                                        logger.warning("No initial game events found")
                                else:
                                    logger.warning(f"Unexpected root element: {root.tag}")
                                    bot_state.channel_number = 0
                            except Exception as e:
                                logger.warning(f"Could not parse game state: {e}")
                                logger.info(f"XML content: {game_state_xml[:500]}")
                                bot_state.channel_number = 0

                            # CRITICAL: Check if game is already finished from initial events
                            # (e.g., opponent timed out before we joined)
                            if bot_state.board_state and bot_state.board_state.game_winner:
                                logger.info(f"🏁 Game already finished when joined! Winner: {bot_state.board_state.game_winner}")
                                logger.info(f"   Reason: {bot_state.board_state.game_win_reason}")

                                # Determine if we won
                                bot_won = (bot_state.board_state.game_winner == config.GEMP_USERNAME or
                                          bot_state.board_state.game_winner == bot_state.board_state.my_player_name)

                                # Notify table manager
                                if bot_state.table_manager:
                                    bot_state.table_manager.on_game_ended()

                                # Rotate log file BEFORE clearing state
                                rotate_game_log(
                                    opponent_name=bot_state.opponent_name,
                                    won=bot_won
                                )

                                # Clear game state and return to lobby
                                bot_state.current_table_id = None
                                bot_state.opponent_name = None
                                bot_state.game_id = None
                                bot_state.channel_number = 0
                                bot_state.state = GameState.IN_LOBBY
                                socketio.emit('state_update', bot_state.to_dict(), namespace='/')
                                socketio.emit('log_message', {'message': f'🏁 Game already finished - returning to lobby', 'level': 'info'}, namespace='/')
                            else:
                                # Game is active - enter PLAYING state
                                bot_state.state = GameState.PLAYING
                                socketio.emit('state_update', bot_state.to_dict(), namespace='/')
                                logger.info("✅ In game session")

                                # Register with chat system for this game (through coordinator for logging)
                                if bot_state.coordinator:
                                    chat_registered, initial_chat_msg_id = bot_state.coordinator.register_chat(bot_state.game_id)
                                else:
                                    chat_registered, initial_chat_msg_id = bot_state.client.register_chat(bot_state.game_id)
                                if chat_registered:
                                    logger.info(f"💬 Registered with chat system (last_msg_id={initial_chat_msg_id})")
                                else:
                                    logger.warning("⚠️ Failed to register with chat system - commands won't work")
                                    initial_chat_msg_id = 0

                                # Initialize chat manager for this game
                                if bot_state.chat_manager:
                                    deck_name = "Unknown"
                                    if bot_state.table_manager and bot_state.table_manager.state:
                                        deck_name = bot_state.table_manager.state.current_deck_name or "Unknown"

                                        # Backup: save table state at game start (in case table was created before persistence fix)
                                        if deck_name != "Unknown" and bot_state.current_table_id:
                                            from engine.table_manager import _save_table_state
                                            _save_table_state(bot_state.current_table_id, deck_name)
                                            logger.debug(f"📝 Saved table state at game start: {deck_name}")

                                    my_side = bot_state.board_state.my_side or "unknown"
                                    opponent_side = "Light" if my_side == "Dark" else "Dark"

                                    bot_state.chat_manager.reset_for_game(
                                        game_id=bot_state.game_id,
                                        opponent_name=bot_state.opponent_name or "Unknown",
                                        deck_name=deck_name,
                                        my_side=my_side,
                                        opponent_side=opponent_side
                                    )
                                    bot_state.chat_manager.on_game_start()
                                    logger.info(f"💬 Chat manager initialized for game vs {bot_state.opponent_name} (deck: {deck_name})")

                                # Initialize command handler for this game
                                if bot_state.command_handler:
                                    bot_state.command_handler.reset_for_game(
                                        game_id=bot_state.game_id,
                                        opponent_name=bot_state.opponent_name or "Unknown",
                                        initial_msg_id=initial_chat_msg_id
                                    )
                                    logger.info(f"🎮 Command handler initialized for game")
                else:
                    # Table disappeared - back to lobby
                    logger.info("Table no longer exists - returning to lobby")
                    socketio.emit('log_message', {'message': 'Table closed - back in lobby', 'level': 'info'}, namespace='/')
                    bot_state.current_table_id = None
                    bot_state.opponent_name = None
                    bot_state.state = GameState.IN_LOBBY
                    socketio.emit('state_update', bot_state.to_dict(), namespace='/')

                socketio.sleep(config.HALL_POLL_INTERVAL)

            elif bot_state.state == GameState.PLAYING:
                # Flag to detect game end from XML
                game_finished = False

                # Poll for game updates
                if bot_state.game_id:
                    logger.debug(f"⏱️  Polling game update (cn={bot_state.channel_number})")

                    # Skip delay during fast action phases (draw/activate)
                    phase = bot_state.board_state.current_phase if bot_state.board_state else ""
                    fast_phase = any(p in phase.lower() for p in ['draw', 'activate'])

                    # Use coordinator for rate-limited game updates
                    if bot_state.coordinator:
                        update_xml = bot_state.coordinator.get_game_update(
                            bot_state.game_id,
                            bot_state.channel_number,
                            fast_phase=fast_phase
                        )
                    else:
                        update_xml = bot_state.client.get_game_update(
                            bot_state.game_id,
                            bot_state.channel_number
                        )

                    if update_xml == "SESSION_EXPIRED":
                        # Session expired - track failure and attempt recovery
                        if bot_state.connection_monitor:
                            bot_state.connection_monitor.record_failure("Session expired")

                        logger.warning("🔄 Session expired, re-logging in...")
                        socketio.emit('log_message', {'message': '🔄 Session expired, re-logging in...', 'level': 'warning'}, namespace='/')

                        # Re-login through coordinator for proper delay
                        login_success = (bot_state.coordinator.login(config.GEMP_USERNAME, config.GEMP_PASSWORD)
                                        if bot_state.coordinator else bot_state.client.login(config.GEMP_USERNAME, config.GEMP_PASSWORD))
                        if login_success:
                            logger.info("✅ Re-login successful, re-joining game...")
                            if bot_state.connection_monitor:
                                bot_state.connection_monitor.record_success()

                            # Re-join the game (through coordinator for logging)
                            if bot_state.coordinator:
                                game_state_xml = bot_state.coordinator.join_game(bot_state.game_id)
                            else:
                                game_state_xml = bot_state.client.join_game(bot_state.game_id)
                            if game_state_xml:
                                # Re-parse channel number
                                try:
                                    import xml.etree.ElementTree as ET
                                    root = ET.fromstring(game_state_xml)
                                    if root.tag == 'gameState':
                                        bot_state.channel_number = int(root.get('cn', 0))
                                        logger.info(f"✅ Re-joined game, channel number: {bot_state.channel_number}")
                                except Exception as e:
                                    logger.error(f"Error re-parsing game state: {e}")
                            else:
                                logger.error("❌ Failed to re-join game")
                        else:
                            logger.error("❌ Re-login failed")
                            # Try recovery via ConnectionMonitor
                            if bot_state.connection_monitor:
                                socketio.sleep(5)  # Wait before retry
                                if bot_state.connection_monitor.attempt_recovery(config.GEMP_USERNAME, config.GEMP_PASSWORD):
                                    logger.info("✅ Connection recovered via monitor")
                                else:
                                    bot_state.state = GameState.STOPPED
                                    bot_state.last_error = "Session expired and recovery failed"
                            else:
                                bot_state.state = GameState.STOPPED
                                bot_state.last_error = "Session expired and re-login failed"

                    elif update_xml is None:
                        # Request failed completely
                        error_reason = bot_state.client.last_error or "Request returned None"

                        # HTTP 404 means game doesn't exist anymore - treat as game ended, NOT connection failure
                        if "404" in error_reason:
                            logger.info(f"🏁 Game no longer exists (HTTP 404) - returning to lobby")
                            socketio.emit('log_message', {'message': '🏁 Game ended (no longer on server)', 'level': 'info'}, namespace='/')

                            # Notify table manager
                            if bot_state.table_manager:
                                bot_state.table_manager.on_game_ended()

                            # Leave chat system
                            if bot_state.game_id:
                                if bot_state.coordinator:
                                    bot_state.coordinator.leave_chat(bot_state.game_id)
                                elif bot_state.client:
                                    bot_state.client.leave_chat(bot_state.game_id)

                            # Clear game state and return to lobby
                            game_finished = True  # Skip normal game end processing below
                            bot_state.current_table_id = None
                            bot_state.opponent_name = None
                            bot_state.game_id = None
                            bot_state.channel_number = 0
                            bot_state.state = GameState.IN_LOBBY
                            socketio.emit('state_update', bot_state.to_dict(), namespace='/')
                        else:
                            # Other failures - track and attempt recovery
                            if bot_state.connection_monitor:
                                should_recover = bot_state.connection_monitor.record_failure(error_reason)

                                if should_recover:
                                    logger.warning("🔄 Multiple failures detected, attempting recovery...")
                                    socketio.emit('log_message', {'message': '🔄 Connection issues, attempting recovery...', 'level': 'warning'}, namespace='/')

                                    # Use exponential backoff delay from monitor
                                    delay = bot_state.connection_monitor.get_recovery_delay()
                                    logger.info(f"⏳ Waiting {delay:.1f}s before recovery attempt...")
                                    socketio.sleep(delay)

                                    if bot_state.connection_monitor.attempt_recovery(config.GEMP_USERNAME, config.GEMP_PASSWORD):
                                        logger.info("✅ Connection recovered")
                                        socketio.emit('log_message', {'message': '✅ Connection recovered', 'level': 'success'}, namespace='/')
                                    else:
                                        logger.error("❌ Recovery failed")
                                        socketio.emit('log_message', {'message': '❌ Recovery failed', 'level': 'error'}, namespace='/')
                            else:
                                logger.warning(f"⚠️  Game update failed: {error_reason}")

                    elif update_xml:
                        # Successful request - track it
                        if bot_state.connection_monitor:
                            bot_state.connection_monitor.record_success()

                        logger.debug(f"📥 Received update XML ({len(update_xml)} bytes)")

                        # Parse channel number and check for game end
                        try:
                            import xml.etree.ElementTree as ET
                            root = ET.fromstring(update_xml)
                            logger.debug(f"Update root element: <{root.tag}> with attributes: {root.attrib}")

                            # Update response has root element <update> (not <gameState>)
                            if root.tag == 'update':
                                # Update channel number
                                new_cn = int(root.get('cn', bot_state.channel_number))
                                if new_cn > bot_state.channel_number:
                                    logger.info(f"📈 Channel number updated: {bot_state.channel_number} -> {new_cn}")
                                    bot_state.channel_number = new_cn
                                else:
                                    logger.debug(f"Channel number unchanged: {new_cn}")

                                # Check for game events
                                game_events = root.findall('.//ge')
                                if len(game_events) > 0:
                                    # Log summary of event types
                                    event_types = {}
                                    for ev in game_events:
                                        t = ev.get('type', 'unknown')
                                        event_types[t] = event_types.get(t, 0) + 1
                                    logger.info(f"📬 Received {len(game_events)} game events: {event_types}")
                                    # Process all events iteratively
                                    logger.debug("🔄 Processing game update events...")
                                    bot_state.channel_number = process_events_iteratively(
                                        game_events,
                                        bot_state.game_id,
                                        bot_state.channel_number,
                                        bot_state.client,
                                        bot_state.event_processor
                                    )
                                    logger.debug(f"✅ Events processed, channel number: {bot_state.channel_number}")

                                    # Do location checks during Control phase (Battle Order rules)
                                    do_location_checks(
                                        bot_state.game_id,
                                        bot_state.client,
                                        bot_state.board_state,
                                        bot_state.strategy_controller,
                                        coordinator=bot_state.coordinator
                                    )

                                    # Check for turn change and notify chat manager
                                    if (bot_state.chat_manager and bot_state.board_state and
                                        bot_state.board_state.turn_number > bot_state.chat_manager.current_turn):
                                        bot_state.chat_manager.on_turn_start(
                                            bot_state.board_state.turn_number,
                                            bot_state.board_state
                                        )
                                else:
                                    logger.debug("No new game events")

                                # Poll for chat commands (but not during fast action phases)
                                # Chat poll can take 2-3s due to long-polling, skip during draw/activate
                                phase = bot_state.board_state.current_phase if bot_state.board_state else ""
                                skip_chat_poll = any(p in phase.lower() for p in ['draw', 'activate'])
                                if bot_state.command_handler and not skip_chat_poll:
                                    try:
                                        bot_state.command_handler.poll_and_handle_commands()
                                    except Exception as e:
                                        logger.error(f"Error polling chat commands: {e}")

                                # Check if game ended (set flag for cleanup below)
                                # Check both XML finished attribute AND board_state.game_winner
                                # (game_winner is set from message events, e.g., after concede)
                                if root.get('finished') == 'true':
                                    game_finished = True
                                    logger.info("🏁 Game finished (from game state)")
                                elif bot_state.board_state and bot_state.board_state.game_winner:
                                    game_finished = True
                                    logger.info(f"🏁 Game finished (winner detected: {bot_state.board_state.game_winner})")
                            else:
                                logger.warning(f"Unexpected root element: {root.tag}")

                        except Exception as e:
                            logger.error(f"Error parsing game update: {e}", exc_info=True)
                    else:
                        logger.warning("⚠️  Game update returned None (request failed)")

                # Skip hall check if we're in a failure state (network issues)
                # This prevents false "game ended" detection when network is down
                skip_hall_check = (bot_state.connection_monitor and
                                   bot_state.connection_monitor.consecutive_failures > 0)

                if skip_hall_check:
                    logger.debug(f"⏭️ Skipping hall check due to connection failures ({bot_state.connection_monitor.consecutive_failures})")
                    # Also check for stuck state - if no success for too long, force recovery
                    if bot_state.connection_monitor.should_force_recovery():
                        logger.warning("🚨 Stuck detected - forcing recovery attempt")
                        socketio.emit('log_message', {'message': '🚨 Connection stuck - forcing recovery...', 'level': 'warning'}, namespace='/')
                        delay = bot_state.connection_monitor.get_recovery_delay()
                        socketio.sleep(delay)
                        if bot_state.connection_monitor.attempt_recovery(config.GEMP_USERNAME, config.GEMP_PASSWORD):
                            logger.info("✅ Forced recovery successful")
                            socketio.emit('log_message', {'message': '✅ Connection recovered', 'level': 'success'}, namespace='/')
                        else:
                            logger.error("❌ Forced recovery failed")
                            socketio.emit('log_message', {'message': '❌ Recovery failed', 'level': 'error'}, namespace='/')

                # NOTE: Web client opens a new browser tab for games and does NOT poll
                # the hall during gameplay. Game end is detected from:
                # 1. Game update XML having finished="true" (handled above)
                # 2. Game update returning error (connection lost)
                # We no longer poll the hall during games to match web client behavior.

                # Handle game end cleanup
                if game_finished:
                    logger.info("🏁 Game ended!")
                    socketio.emit('log_message', {'message': '🏁 Game ended', 'level': 'info'}, namespace='/')

                    # Determine if we won
                    # Note: bot_won means the bot won; player_won means the human opponent won
                    bot_won = False
                    if bot_state.board_state:
                        # First check if game_winner was set from message events (most reliable)
                        if bot_state.board_state.game_winner:
                            bot_won = (bot_state.board_state.game_winner == config.GEMP_USERNAME or
                                       bot_state.board_state.game_winner == bot_state.board_state.my_player_name)
                            logger.info(f"Game result from message: {'Won' if bot_won else 'Lost'} "
                                       f"(winner: {bot_state.board_state.game_winner}, "
                                       f"reason: {bot_state.board_state.game_win_reason})")
                        else:
                            # Fallback: check life force totals
                            their_life = (bot_state.board_state.their_reserve_deck +
                                         bot_state.board_state.their_force_pile +
                                         bot_state.board_state.their_used_pile)
                            my_life = (bot_state.board_state.reserve_deck +
                                      bot_state.board_state.force_pile +
                                      bot_state.board_state.used_pile)
                            bot_won = their_life <= my_life  # We won if they have less life force
                            logger.info(f"Game result from life force: {'Won' if bot_won else 'Lost'} "
                                       f"(my life: {my_life}, their life: {their_life})")

                    # Send game end message
                    # chat_manager expects won=True when the PLAYER (human) won
                    player_won = not bot_won
                    if bot_state.chat_manager:
                        bot_state.chat_manager.on_game_end(won=player_won, board_state=bot_state.board_state)

                    # Note: Stats are recorded by chat_manager.on_game_end() above
                    # (record_game, record_game_result, deck stats, achievements)

                    # Notify table manager
                    if bot_state.table_manager:
                        bot_state.table_manager.on_game_ended()

                    # Leave chat system (must be before clearing game_id) - through coordinator for logging
                    if bot_state.game_id:
                        if bot_state.coordinator:
                            bot_state.coordinator.leave_chat(bot_state.game_id)
                        elif bot_state.client:
                            bot_state.client.leave_chat(bot_state.game_id)
                        logger.info(f"💬 Left chat system for game {bot_state.game_id}")

                    # Rotate log file (preserve game log with timestamp)
                    rotate_game_log(
                        opponent_name=bot_state.opponent_name,
                        won=bot_won
                    )

                    # Clear old game state
                    bot_state.current_table_id = None
                    bot_state.opponent_name = None
                    bot_state.game_id = None
                    bot_state.channel_number = 0
                    bot_state.state = GameState.IN_LOBBY  # Go back to lobby, it will auto-create table
                    socketio.emit('state_update', bot_state.to_dict(), namespace='/')
                    # Note: Table will be auto-created by IN_LOBBY state handler

                # Use shorter poll interval during fast action phases (draw/activate)
                phase = bot_state.board_state.current_phase if bot_state.board_state else ""
                fast_phase = any(p in phase.lower() for p in ['draw', 'activate'])
                poll_interval = 0.25 if fast_phase else config.GAME_POLL_INTERVAL
                socketio.sleep(poll_interval)

            else:
                # Unknown state or not ready yet
                socketio.sleep(0.5)

        except requests.RequestException as e:
            # Network errors - try to recover instead of stopping
            logger.error(f"⚠️ Network error in worker: {e}", exc_info=True)
            if bot_state.connection_monitor:
                should_recover = bot_state.connection_monitor.record_failure(str(e))
                if should_recover:
                    logger.warning("🔄 Network error triggered recovery attempt...")
                    socketio.emit('log_message', {'message': '🔄 Network error, attempting recovery...', 'level': 'warning'}, namespace='/')
                    delay = bot_state.connection_monitor.get_recovery_delay()
                    socketio.sleep(delay)
                    if bot_state.connection_monitor.attempt_recovery(config.GEMP_USERNAME, config.GEMP_PASSWORD):
                        logger.info("✅ Recovery successful after network error")
                        socketio.emit('log_message', {'message': '✅ Connection recovered', 'level': 'success'}, namespace='/')
                        continue  # Continue the main loop
                    else:
                        logger.error("❌ Recovery failed after network error")
            # If no monitor or recovery failed, enter error state but don't stop
            bot_state.state = GameState.ERROR
            bot_state.last_error = f"Network error: {e}"
            socketio.emit('state_update', bot_state.to_dict(), namespace='/')
            socketio.emit('log_message', {'message': f'Network error: {e}', 'level': 'error'}, namespace='/')
            socketio.sleep(5)  # Wait before retrying

        except Exception as e:
            # Non-network errors - log and stop
            logger.error(f"💥 Worker error: {e}", exc_info=True)
            bot_state.state = GameState.ERROR
            bot_state.last_error = str(e)
            socketio.emit('state_update', bot_state.to_dict(), namespace='/')
            socketio.emit('log_message', {'message': f'Error: {e}', 'level': 'error'}, namespace='/')
            bot_state.running = False

    logger.info("Bot worker greenlet stopped")


# Flask routes
@app.route('/')
def index():
    """Admin dashboard"""
    return render_template('dashboard.html', state=bot_state.to_dict())


@app.route('/health')
def health():
    """Health check endpoint for monitoring"""
    return {
        'status': 'ok',
        'bot_state': bot_state.state.value,
        'version': '0.1.0-alpha'
    }


@app.route('/board_state')
def board_state_view():
    """View current board state (for debugging)"""
    if not bot_state.board_state:
        return render_template('board_state.html', board_state=None)

    bs = bot_state.board_state

    # Build a simplified view for the template
    view_data = {
        'my_player': bs.my_player_name,
        'opponent': bs.opponent_name or "Unknown",
        'my_side': bs.my_side or "Unknown",
        'current_phase': bs.current_phase or "Unknown",
        'current_turn': bs.current_turn_player or "Unknown",
        'is_my_turn': bs.is_my_turn(),

        # Resources
        'my_force': bs.force_pile,
        'my_used': bs.used_pile,
        'my_reserve': bs.reserve_deck,
        'my_lost': bs.lost_pile,
        'my_hand_count': len(bs.cards_in_hand),

        'their_force': bs.their_force_pile,
        'their_used': bs.their_used_pile,
        'their_reserve': bs.their_reserve_deck,
        'their_lost': bs.their_lost_pile,
        'their_hand_count': bs.their_hand_size,

        # Power
        'my_total_power': bs.total_my_power(),
        'their_total_power': bs.total_their_power(),
        'power_advantage': bs.power_advantage(),
        'force_advantage': bs.force_advantage(),

        # Locations
        'locations': [],

        # Hand - DEBUG: log what we're sending
        'hand': [],
    }

    # Build hand with debug logging
    for c in bs.cards_in_hand:
        logger.info(f"🃏 Hand card: {c.card_title} | card_id={c.card_id} | blueprint={c.blueprint_id}")
        view_data['hand'].append({
            'blueprint_id': c.blueprint_id,
            'card_id': c.card_id,
            'name': c.card_title or c.blueprint_id,
            'type': c.card_type,
            'deploy': c.deploy
        })

    view_data['total_cards_in_play'] = len(bs.cards_in_play)

    # Add location details
    for i, loc in enumerate(bs.locations):
        if loc:
            # Prefer site_name (full name like "Tatooine: Mos Eisley")
            # Fall back to system_name (for space locations)
            # Finally fall back to blueprint_id
            loc_name = loc.site_name or loc.system_name or loc.blueprint_id
            view_data['locations'].append({
                'index': i,
                'name': loc_name,
                'my_power': bs.my_power_at_location(i),
                'their_power': bs.their_power_at_location(i),
                'my_cards': [{
                    'blueprint': c.blueprint_id,
                    'id': c.card_id,
                    'name': c.card_title or c.blueprint_id,
                    'power': c.power,
                    'ability': c.ability
                } for c in loc.my_cards],
                'their_cards': [{
                    'blueprint': c.blueprint_id,
                    'id': c.card_id,
                    'name': c.card_title or c.blueprint_id,
                    'power': c.power,
                    'ability': c.ability
                } for c in loc.their_cards],
            })

    return render_template('board_state.html', board_state=view_data)


# WebSocket event handlers
@socketio.on('connect')
def handle_connect():
    """Client connected to WebSocket"""
    logger.info('Admin UI connected via WebSocket')
    emit('state_update', bot_state.to_dict())


@socketio.on('disconnect')
def handle_disconnect():
    """Client disconnected from WebSocket"""
    logger.info('Admin UI disconnected from WebSocket')


@socketio.on('request_state')
def handle_request_state():
    """Client requesting current state"""
    emit('state_update', bot_state.to_dict())


def _start_bot_internal():
    """Internal function to start the bot - used by both manual start and auto-start"""
    if bot_state.state not in [GameState.STOPPED, GameState.ERROR]:
        logger.warning(f'Cannot start - bot is in state: {bot_state.state.value}')
        return False

    logger.info('🚀 Starting bot...')

    # Create GEMP client
    bot_state.client = GEMPClient(config.GEMP_SERVER_URL)
    bot_state.state = GameState.CONNECTING
    bot_state.last_error = None
    bot_state.running = True
    bot_state.current_tables = []

    # Start worker greenlet (NOT a thread!)
    socketio.start_background_task(bot_worker)
    return True


@socketio.on('start_bot')
def handle_start_bot():
    """Start the bot - create GEMP client and start worker greenlet"""
    if _start_bot_internal():
        emit('state_update', bot_state.to_dict())
        emit('log_message', {'message': '🚀 Bot starting...', 'level': 'info'})
    else:
        emit('log_message', {'message': f'Cannot start - bot is in state: {bot_state.state.value}', 'level': 'warning'})


@socketio.on('stop_bot')
def handle_stop_bot():
    """Stop the bot"""
    logger.info('🛑 Stop bot requested')
    bot_state.running = False
    bot_state.state = GameState.STOPPED

    # Clear table and game state (important: clear game_id so we rejoin on restart!)
    bot_state.current_table_id = None
    bot_state.opponent_name = None
    bot_state.current_tables = []
    bot_state.game_id = None
    bot_state.channel_number = 0
    bot_state.board_state = None
    bot_state.event_processor = None

    # Reset table manager to clear failure counter
    if bot_state.table_manager:
        bot_state.table_manager.reset()
        logger.info("🔄 TableManager reset")

    # Logout through coordinator for logging
    if bot_state.coordinator:
        bot_state.coordinator.logout()
    elif bot_state.client:
        bot_state.client.logout()

    emit('state_update', bot_state.to_dict())
    emit('log_message', {'message': '🛑 Bot stopped', 'level': 'info'})


@socketio.on('update_config')
def handle_update_config(data):
    """Update bot configuration from admin UI"""
    key = data.get('key')
    value = data.get('value')

    logger.info(f'Config update: {key} = {value}')

    # Update config and sync to GameStrategy if needed
    if key == 'max_hand_size':
        bot_state.config.MAX_HAND_SIZE = int(value)
        _sync_config_to_strategy()
    elif key == 'hand_soft_cap':
        bot_state.config.HAND_SOFT_CAP = int(value)
        _sync_config_to_strategy()
    elif key == 'force_gen_target':
        bot_state.config.FORCE_GEN_TARGET = int(value)
        _sync_config_to_strategy()
    elif key == 'max_reserve_checks':
        bot_state.config.MAX_RESERVE_CHECKS = int(value)
        _sync_config_to_strategy()
    elif key == 'deploy_threshold':
        bot_state.config.DEPLOY_THRESHOLD = int(value)
    elif key == 'battle_favorable_threshold':
        bot_state.config.BATTLE_FAVORABLE_THRESHOLD = int(value)
        _sync_config_to_strategy()
    elif key == 'battle_danger_threshold':
        bot_state.config.BATTLE_DANGER_THRESHOLD = int(value)
        _sync_config_to_strategy()
    elif key == 'bot_mode':
        bot_state.config.BOT_MODE = value

    emit('state_update', bot_state.to_dict())
    emit('log_message', {'message': f'Updated {key} to {value}'})


@socketio.on('change_server')
def handle_change_server(data):
    """Change GEMP server URL - only allowed when bot is stopped"""
    server_url = data.get('server_url')

    if not server_url:
        emit('log_message', {'message': 'No server URL provided', 'level': 'error'})
        return

    # Only allow when bot is stopped
    if bot_state.state not in [GameState.STOPPED, GameState.ERROR]:
        emit('log_message', {'message': 'Cannot change server while bot is running. Stop the bot first.', 'level': 'error'})
        emit('state_update', bot_state.to_dict())
        return

    logger.info(f'Changing GEMP server to: {server_url}')

    # Update config
    config.GEMP_SERVER_URL = server_url

    # Save to persistent settings
    settings.set_setting('gemp_server_url', server_url)

    # Recreate client with new URL
    from engine.client import GEMPClient
    bot_state.client = GEMPClient(server_url)

    emit('state_update', bot_state.to_dict())
    emit('log_message', {'message': f'✅ Server changed to: {server_url}', 'level': 'success'})


@socketio.on('toggle_auto_start')
def handle_toggle_auto_start(data):
    """Toggle auto-start setting"""
    enabled = data.get('enabled', False)

    logger.info(f'Auto-start {"enabled" if enabled else "disabled"}')

    # Save to persistent settings
    settings.set_setting('auto_start', enabled)

    emit('state_update', bot_state.to_dict())
    emit('log_message', {'message': f'✅ Auto-start {"enabled" if enabled else "disabled"}', 'level': 'success'})


def _sync_config_to_strategy():
    """Sync config values to GameStrategy instance"""
    if bot_state.strategy_controller and bot_state.strategy_controller.game_strategy:
        gs = bot_state.strategy_controller.game_strategy
        gs.force_generation_target = bot_state.config.FORCE_GEN_TARGET
        gs.max_reserve_checks_per_turn = bot_state.config.MAX_RESERVE_CHECKS
        gs.battle_favorable_threshold = bot_state.config.BATTLE_FAVORABLE_THRESHOLD
        gs.battle_danger_threshold = bot_state.config.BATTLE_DANGER_THRESHOLD
        logger.debug(f"Synced config to GameStrategy: gen_target={gs.force_generation_target}, max_reserve={gs.max_reserve_checks_per_turn}, battle_favorable={gs.battle_favorable_threshold}, battle_danger={gs.battle_danger_threshold}")


@socketio.on('create_table')
def handle_create_table(data):
    """Create a new game table"""
    import random

    deck_name = data.get('deck_name')
    table_name = data.get('table_name', config.TABLE_NAME)
    is_library = data.get('is_library', True)

    # Ensure table name has "Bot Table:" prefix
    if not table_name.startswith('Bot Table:'):
        table_name = f'Bot Table: {table_name}'

    # Handle random deck selection
    if deck_name == '__RANDOM__':
        if bot_state.library_decks:
            random_deck = random.choice(bot_state.library_decks)
            deck_name = random_deck.name
            is_library = True
            logger.info(f'🎲 Random deck selected: {deck_name}')
            emit('log_message', {'message': f'🎲 Random deck selected: {deck_name}', 'level': 'info'})
        else:
            emit('log_message', {'message': '❌ No library decks available for random selection', 'level': 'error'})
            return

    logger.info(f'📋 Create table requested: {table_name} with deck {deck_name} (library: {is_library})')

    if bot_state.state != GameState.IN_LOBBY:
        emit('log_message', {'message': 'Bot must be in lobby to create table', 'level': 'warning'})
        return

    if not bot_state.client and not bot_state.coordinator:
        emit('log_message', {'message': 'No GEMP client available', 'level': 'error'})
        return

    # Create table through coordinator for logging
    if bot_state.coordinator:
        table_id = bot_state.coordinator.create_table(deck_name, table_name, is_library=is_library)
    else:
        table_id = bot_state.client.create_table(deck_name, table_name, is_library=is_library)

    if table_id:
        bot_state.current_table_id = table_id
        bot_state.state = GameState.WAITING_FOR_OPPONENT

        # IMPORTANT: Also update table_manager state so deck name is tracked
        if bot_state.table_manager and bot_state.table_manager.state:
            bot_state.table_manager.state.current_table_id = table_id
            bot_state.table_manager.state.current_deck_name = deck_name
            # Persist to file for restart recovery
            from engine.table_manager import _save_table_state
            _save_table_state(table_id, deck_name)
            logger.info(f"📝 Table manager state updated: deck={deck_name}")

        emit('state_update', bot_state.to_dict())
        emit('log_message', {'message': f'✅ Table created: {table_name} (deck: {deck_name})', 'level': 'success'})
    else:
        emit('log_message', {'message': '❌ Failed to create table', 'level': 'error'})


@socketio.on('leave_table')
def handle_leave_table():
    """Leave the current table"""
    logger.info('🚪 Leave table requested')

    if not bot_state.current_table_id:
        emit('log_message', {'message': '⚠️ Not at any table', 'level': 'warning'})
        return

    if not bot_state.client and not bot_state.coordinator:
        emit('log_message', {'message': '❌ No GEMP client available', 'level': 'error'})
        return

    # Drop from the table through coordinator for logging
    if bot_state.coordinator:
        success = bot_state.coordinator.leave_table(bot_state.current_table_id)
    else:
        success = bot_state.client.leave_table(bot_state.current_table_id)

    if success:
        logger.info(f'✅ Left table {bot_state.current_table_id}')
        emit('log_message', {'message': '✅ Left table', 'level': 'success'})

        # Clear table state
        bot_state.current_table_id = None
        bot_state.opponent_name = None
        bot_state.state = GameState.IN_LOBBY

        emit('state_update', bot_state.to_dict())
    else:
        logger.warning('Failed to leave table gracefully, clearing state anyway')
        emit('log_message', {'message': '⚠️ Left table (may need manual cleanup)', 'level': 'warning'})

        # Clear state anyway
        bot_state.current_table_id = None
        bot_state.opponent_name = None
        bot_state.state = GameState.IN_LOBBY

        emit('state_update', bot_state.to_dict())


def _auto_start_check():
    """Check if auto-start is enabled and start the bot after a delay"""
    import eventlet
    eventlet.sleep(3)  # Wait for server to be fully ready

    if user_settings.get('auto_start', False):
        logger.info('🚀 Auto-start enabled - starting bot automatically...')
        if _start_bot_internal():
            logger.info('✅ Bot auto-started successfully')
        else:
            logger.error('❌ Bot auto-start failed')
    else:
        logger.info('ℹ️ Auto-start disabled - waiting for manual start')


if __name__ == '__main__':
    logger.info(f'=' * 60)
    logger.info(f'🤖 Rando Cal Bot Starting')
    logger.info(f'Version: 0.2.0-alpha (Phase 2 - Networking)')
    logger.info(f'Host: {config.HOST}:{config.PORT}')
    logger.info(f'GEMP Server: {config.GEMP_SERVER_URL}')
    logger.info(f'GEMP Username: {config.GEMP_USERNAME}')
    logger.info(f'Bot Mode: {config.BOT_MODE}')
    logger.info(f'Auto-start: {user_settings.get("auto_start", False)}')
    logger.info(f'=' * 60)

    # Schedule auto-start check (runs after server starts)
    socketio.start_background_task(_auto_start_check)

    # Run Flask with SocketIO
    socketio.run(
        app,
        host=config.HOST,
        port=config.PORT,
        debug=config.DEBUG
    )
