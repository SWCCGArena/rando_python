"""
Event Processor

Parses XML game events and updates the BoardState accordingly.
Handles all event types: PCIP, RCIP, MCIP, GS, P, TC, GPC, etc.

Ported from Unity C# GameCommsHelper.cs
"""

import xml.etree.ElementTree as ET
from typing import Optional
import logging
from .board_state import BoardState, LocationInPlay
from .card_loader import get_card

logger = logging.getLogger(__name__)


class EventProcessor:
    """
    Processes GEMP XML game events and updates board state.
    """

    def __init__(self, board_state: BoardState):
        self.board_state = board_state
        # Callbacks for card placement events (for achievements, etc.)
        self._on_card_placed_callbacks = []
        # Callbacks for battle damage events
        self._on_battle_damage_callbacks = []
        # Callbacks for battle start events
        self._on_battle_start_callbacks = []
        # Flag to indicate we're processing historical events (catching up)
        # When True, skip chat-related callbacks to avoid re-posting old messages
        self.catching_up = False
        # Track highest battle damage during current battle
        # Only send chat/record score when battle ends (EB event)
        self._pending_battle_damage = 0

    def register_card_placed_callback(self, callback):
        """
        Register a callback to be called when a card is placed on the board.

        Callback signature: callback(card_title: str, blueprint_id: str, zone: str, owner: str)
        """
        self._on_card_placed_callbacks.append(callback)

    def register_battle_damage_callback(self, callback):
        """
        Register a callback to be called when battle damage is detected from messages.

        Callback signature: callback(damage: int)
        """
        self._on_battle_damage_callbacks.append(callback)

    def register_battle_start_callback(self, callback):
        """
        Register a callback to be called when a battle starts.

        Callback signature: callback()
        """
        self._on_battle_start_callbacks.append(callback)

    def _notify_battle_start(self):
        """Notify all registered callbacks that a battle started"""
        # Skip chat-related callbacks when catching up on historical events
        if self.catching_up:
            logger.debug("⚔️ Skipping battle start callback (catching up)")
            return
        for callback in self._on_battle_start_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error(f"Error in battle start callback: {e}")

    def _notify_battle_damage(self, damage: int):
        """Notify all registered callbacks that battle damage occurred"""
        # Skip chat-related callbacks when catching up on historical events
        if self.catching_up:
            logger.debug(f"💥 Skipping battle damage callback (catching up): {damage}")
            return
        for callback in self._on_battle_damage_callbacks:
            try:
                callback(damage)
            except Exception as e:
                logger.error(f"Error in battle damage callback: {e}")

    def _notify_card_placed(self, card_title: str, blueprint_id: str, zone: str, owner: str):
        """Notify all registered callbacks that a card was placed"""
        # Skip chat-related callbacks when catching up on historical events
        if self.catching_up:
            return
        for callback in self._on_card_placed_callbacks:
            try:
                callback(card_title, blueprint_id, zone, owner)
            except Exception as e:
                logger.error(f"Error in card placed callback: {e}")

    def process_event(self, event: ET.Element):
        """
        Process a single game event and update board state.

        Event types ported from C# GameCommsHelper._RealParseGameEvent():
        - P: Participant (player names/sides)
        - TC: Turn Change
        - GPC: Game Phase Change
        - RCFP/RLFP: Remove Card From Play / Remove Lost From Play
        - MCIP: Move Card In Play
        - PCIP/RCIP/PCIPAR: Put/Replace Card In Play (all handled same way)
        - GS: Game State (force piles, power at locations)
        - M: Message
        - D: Decision (handled elsewhere)
        - SB/SD/SLC/SA: Start Battle/Duel/Lightsaber Combat/Attack
        - EB/EA/ED/ELC: End Battle/Attack/Duel/Lightsaber Combat
        - IP: In-Progress card animation (ignore)
        - CAC: Card Action Choice animation (ignore)

        Args:
            event: XML element representing the event (<ge> tag)
        """
        event_type = event.get('type', '')

        # Log events at DEBUG level (app.py already logs important ones at INFO)
        if event_type not in ['GS', 'M', 'IP', 'CAC']:  # Skip very verbose events
            logger.debug(f"📬 Event type={event_type}: {dict(event.attrib)}")

        # === Card Placement Events (C# treats PCIP, RCIP, PCIPAR the same) ===
        if event_type in ['PCIP', 'RCIP', 'PCIPAR']:
            self._handle_pcip(event)

        # === Card Removal Events ===
        elif event_type in ['RCFP', 'RLFP']:
            self._handle_remove_card(event)

        # === Card Movement Events ===
        elif event_type == 'MCIP':
            self._handle_mcip(event)

        # === Game State Events ===
        elif event_type == 'GS':
            self._handle_gs(event)

        # === Player/Turn Events ===
        elif event_type == 'P':
            self._handle_participant(event)
        elif event_type == 'TC':
            self._handle_turn_change(event)
        elif event_type == 'GPC':
            self._handle_phase_change(event)

        # === Battle Events (track in_battle state) ===
        elif event_type in ['SB', 'SD', 'SLC', 'SA']:
            self._handle_start_battle(event)
        elif event_type in ['EB', 'EA', 'ED', 'ELC']:
            self._handle_end_battle(event)

        # === Message Events (check for game end) ===
        elif event_type == 'M':
            self._handle_message(event)

        # === Events we intentionally ignore ===
        elif event_type in ['D', 'IP', 'CAC']:
            # D: Decision (handled by decision_handler)
            # IP: In-Progress animation state
            # CAC: Card Action Choice animation
            pass

        else:
            logger.debug(f"Unhandled event type: {event_type}")

    # ========== Event Handlers ==========

    def _handle_pcip(self, event: ET.Element):
        """
        Handle PCIP (Put Card In Play) event.

        Adds a card to play at a location, in hand, attached, etc.
        Ported from C# GameCommsHelper - PCIP, RCIP, PCIPAR all handled the same way.
        """
        # Log at DEBUG level (main event already logged)
        logger.debug(f"📥 PCIP event: {dict(event.attrib)}")

        card_id = event.get('cardId', '')
        blueprint_id = event.get('blueprintId', '')
        zone = event.get('zone', '')
        owner = event.get('zoneOwnerId', '')
        target_card_id = event.get('targetCardId', '')
        location_index_str = event.get('locationIndex', '-1')
        system_name = event.get('systemName', '')

        try:
            location_index = int(location_index_str)
        except ValueError:
            location_index = -1

        # Handle LOCATIONS zone specially (creates LocationInPlay, not CardInPlay)
        if zone == "LOCATIONS":
            self._handle_location_placement(card_id, blueprint_id, owner, location_index, system_name)
            return  # Exit early for locations

        # === All other zones (HAND, TABLE, ATTACHED, etc.) ===
        try:
            self.board_state.update_cards_in_play(
                card_id=card_id,
                target_card_id=target_card_id if target_card_id else None,
                blueprint_id=blueprint_id,
                zone=zone,
                owner=owner,
                location_index=location_index
            )
        except Exception as e:
            logger.error(f"❌ Error in update_cards_in_play: {e}")

        # Get card title for logging
        card = self.board_state.cards_in_play.get(card_id)
        card_title = card.card_title if card else blueprint_id
        logger.debug(f"🃏 Card added: {card_title} ({blueprint_id}) to {zone}")

        # Notify strategy controller of deployments at locations (for optimization)
        # This invalidates the location's cached cardInfo check so it will be re-checked
        if zone == "AT_LOCATION" and location_index >= 0:
            if self.board_state.strategy_controller and location_index < len(self.board_state.locations):
                loc = self.board_state.locations[location_index]
                if loc:
                    self.board_state.strategy_controller.on_card_deployed(loc.card_id)

        # === SIDE DETECTION ===
        # If we haven't detected our side yet, check from cards in our HAND
        # HAND cards are reliable - other zones can have cards swapped by game effects
        # Skip hidden cards (-1_X) which don't have side info
        if zone == "HAND" and not blueprint_id.startswith('-1_'):
            if not self.board_state.my_side and owner == self.board_state.my_player_name:
                card_metadata = get_card(blueprint_id)
                if card_metadata and card_metadata.side:
                    self.board_state.my_side = card_metadata.side.lower()
                    logger.info(f"🎭 Detected my side: {self.board_state.my_side} (from {card_title})")
                    # Update strategy controller and game strategy if they exist
                    if self.board_state.strategy_controller:
                        self.board_state.strategy_controller.my_side = self.board_state.my_side
                        if self.board_state.strategy_controller.game_strategy:
                            self.board_state.strategy_controller.game_strategy.my_side = self.board_state.my_side

        # Notify callbacks (for achievements, etc.)
        self._notify_card_placed(card_title, blueprint_id, zone, owner)

    def _handle_location_placement(self, card_id: str, blueprint_id: str, owner: str,
                                    location_index: int, system_name: str):
        """Handle LOCATIONS zone - creates a LocationInPlay"""
        # Load card metadata for location details
        card_metadata = get_card(blueprint_id)

        # Get full site name from card metadata
        site_name = card_metadata.title if card_metadata else system_name

        # Extract system name from site name
        # Format: "System: Site" or just "System"
        if ':' in site_name:
            extracted_system = site_name.split(':')[0].strip()
        else:
            extracted_system = site_name

        # If system_name from XML is empty, use extracted system
        if not system_name:
            system_name = extracted_system

        # Determine location type from card metadata
        is_site = False
        is_space = False
        is_ground = False

        if card_metadata:
            # Check sub_type for location type
            # subType will be "Site", "System", or "Sector"
            if card_metadata.sub_type:
                subtype_lower = card_metadata.sub_type.lower()
                is_site = 'site' in subtype_lower
                # Systems and Sectors are SPACE ONLY locations (even if they have Planet icon)
                is_system_or_sector = 'system' in subtype_lower or 'sector' in subtype_lower
                if is_system_or_sector:
                    is_space = True

            # Determine ground/space from icons for sites
            if is_site:
                # Ground if has Interior, Exterior, or Planet icons
                has_ground_icons = (card_metadata.is_interior or
                                   card_metadata.is_exterior or
                                   card_metadata.has_planet_icon)
                if has_ground_icons:
                    is_ground = True

                # Space if has Space icon or is a starship site
                if card_metadata.has_space_icon or card_metadata.is_starship_site:
                    is_space = True

                # Docking bays are BOTH space AND ground (starships can dock, characters can be there)
                if card_metadata.is_docking_bay:
                    is_space = True
                    is_ground = True

            # Log icons for debugging
            if card_metadata.icons:
                logger.debug(f"Location {site_name} icons: {card_metadata.icons}")

        location = LocationInPlay(
            card_id=card_id,
            blueprint_id=blueprint_id,
            owner=owner,
            location_index=location_index,
            system_name=system_name,  # "Yavin 4"
            site_name=site_name,      # "Yavin 4: Massassi Throne Room"
            is_site=is_site,
            is_space=is_space,
            is_ground=is_ground,
        )
        self.board_state.add_location(location)

        loc_type = []
        if is_space: loc_type.append("Space")
        if is_ground: loc_type.append("Ground")
        loc_type_str = "/".join(loc_type) if loc_type else "Unknown"

        # Log with both system and site names
        if system_name != site_name and is_site:
            logger.info(f"📍 Location added: {site_name} (System: {system_name}) [{loc_type_str}] at index {location_index}")
        else:
            logger.info(f"📍 Location added: {site_name} [{loc_type_str}] at index {location_index}")

        # Notify callbacks (for achievements, etc.)
        self._notify_card_placed(site_name, blueprint_id, "LOCATIONS", owner)

    def _handle_remove_card(self, event: ET.Element):
        """
        Handle RCFP/RLFP (Remove Card From Play / Remove Lost From Play) events.

        These events ONLY remove cards. RCIP is now handled by _handle_pcip
        since C# treats PCIP/RCIP/PCIPAR all the same way (as placements).

        Ported from C# AIBoardStateTracker.ParseRemoveCardEvent()
        """
        # Cards can be specified in multiple ways
        card_id = event.get('cardId', '')
        other_card_ids = event.get('otherCardIds', '')

        # Collect all IDs to remove
        ids_to_remove = []
        if card_id:
            ids_to_remove.append(card_id)
        if other_card_ids:
            ids_to_remove.extend(other_card_ids.split(','))

        for cid in ids_to_remove:
            if cid.strip():
                self.board_state.remove_card(cid.strip())
                logger.debug(f"➖ Removed card: {cid.strip()}")

    def _handle_mcip(self, event: ET.Element):
        """
        Handle MCIP (Move Card In Play) event.

        Moves a card between zones, locations, or reattaches.
        """
        card_id = event.get('cardId', '')
        zone = event.get('zone', '')
        target_card_id = event.get('targetCardId', '')
        location_index_str = event.get('locationIndex', '-1')

        try:
            location_index = int(location_index_str)
        except ValueError:
            location_index = -1

        self.board_state.update_card(
            card_id=card_id,
            zone=zone,
            location_index=location_index,
            target_card_id=target_card_id if target_card_id else None
        )
        logger.debug(f"🔄 Card moved: {card_id} to {zone}")

    def _handle_gs(self, event: ET.Element):
        """
        Handle GS (Game State) event.

        Updates force piles, hand sizes, power at locations, generation.
        """
        # Force generation
        dark_gen = event.get('darkForceGeneration', '0')
        light_gen = event.get('lightForceGeneration', '0')
        self.board_state.dark_generation = int(dark_gen)
        self.board_state.light_generation = int(light_gen)

        # Set activation based on our side
        if self.board_state.my_side == "dark":
            self.board_state.activation = int(dark_gen)
        elif self.board_state.my_side == "light":
            self.board_state.activation = int(light_gen)
        else:
            # If side not yet known, use the larger value
            self.board_state.activation = max(int(dark_gen), int(light_gen))

        # Parse player zones
        player_zones = event.findall('.//playerZones')
        matched_my_zones = False
        for zone_element in player_zones:
            player_name = zone_element.get('name', '')

            if player_name == self.board_state.my_player_name:
                # My zones
                matched_my_zones = True
                new_force = int(zone_element.get('FORCE_PILE', '0'))
                # Log if force changes significantly (for debugging)
                if new_force != self.board_state.force_pile and new_force > 0:
                    logger.debug(f"💰 Force pile updated: {self.board_state.force_pile} -> {new_force}")
                self.board_state.force_pile = new_force
                self.board_state.used_pile = int(zone_element.get('USED_PILE', '0'))
                self.board_state.reserve_deck = int(zone_element.get('RESERVE_DECK', '0'))
                self.board_state.lost_pile = int(zone_element.get('LOST_PILE', '0'))
                self.board_state.out_of_play = int(zone_element.get('OUT_OF_PLAY', '0'))
                self.board_state.hand_size = int(zone_element.get('HAND', '0'))
                self.board_state.sabacc_hand = int(zone_element.get('SABACC_HAND', '0'))
            else:
                # Opponent zones
                self.board_state.their_force_pile = int(zone_element.get('FORCE_PILE', '0'))
                self.board_state.their_used_pile = int(zone_element.get('USED_PILE', '0'))
                self.board_state.their_reserve_deck = int(zone_element.get('RESERVE_DECK', '0'))
                self.board_state.their_lost_pile = int(zone_element.get('LOST_PILE', '0'))
                self.board_state.their_out_of_play = int(zone_element.get('OUT_OF_PLAY', '0'))
                self.board_state.their_hand_size = int(zone_element.get('HAND', '0'))
                self.board_state.their_sabacc_hand = int(zone_element.get('SABACC_HAND', '0'))

        # Log warning if we didn't match any zones for ourselves
        if player_zones and not matched_my_zones:
            all_names = [z.get('name', '?') for z in player_zones]
            logger.warning(f"⚠️ GS zones didn't match my_player_name='{self.board_state.my_player_name}'. "
                          f"Zone names: {all_names}")

        # Parse power at locations
        dark_power_element = event.find('.//darkPowerAtLocations')
        if dark_power_element is not None:
            self.board_state.dark_power_at_locations.clear()
            for attr_name, attr_value in dark_power_element.attrib.items():
                # Attribute name can be "_0", "_1" or "locationIndex0", "locationIndex1"
                # Extract the numeric part
                numeric_part = ''.join(filter(str.isdigit, attr_name))
                if numeric_part:
                    index = int(numeric_part)
                    self.board_state.dark_power_at_locations[index] = int(attr_value)

        light_power_element = event.find('.//lightPowerAtLocations')
        if light_power_element is not None:
            self.board_state.light_power_at_locations.clear()
            for attr_name, attr_value in light_power_element.attrib.items():
                # Attribute name can be "_0", "_1" or "locationIndex0", "locationIndex1"
                # Extract the numeric part
                numeric_part = ''.join(filter(str.isdigit, attr_name))
                if numeric_part:
                    index = int(numeric_part)
                    self.board_state.light_power_at_locations[index] = int(attr_value)

        # Parse battle attrition and damage (used during damage segment)
        dark_attrition = event.get('darkBattleAttritionRemaining', '0')
        dark_damage = event.get('darkBattleDamageRemaining', '0')
        light_attrition = event.get('lightBattleAttritionRemaining', '0')
        light_damage = event.get('lightBattleDamageRemaining', '0')

        self.board_state.dark_attrition_remaining = int(dark_attrition)
        self.board_state.dark_damage_remaining = int(dark_damage)
        self.board_state.light_attrition_remaining = int(light_attrition)
        self.board_state.light_damage_remaining = int(light_damage)

        # Log if we're in damage segment with pending attrition/damage
        total_pending = int(dark_attrition) + int(dark_damage) + int(light_attrition) + int(light_damage)
        if total_pending > 0:
            logger.info(f"⚔️ Battle damage: Dark attrition={dark_attrition}, damage={dark_damage} | "
                       f"Light attrition={light_attrition}, damage={light_damage}")

        # Only log game state if values changed (avoid spam from multiple GS events per response)
        new_state = (self.board_state.force_pile, self.board_state.total_my_power(), self.board_state.reserve_deck)
        if not hasattr(self, '_last_gs_state') or self._last_gs_state != new_state:
            self._last_gs_state = new_state
            logger.info(f"📊 Game state updated: Force={new_state[0]}, Power={new_state[1]}, Reserve={new_state[2]}")

    def _handle_participant(self, event: ET.Element):
        """
        Handle P (Participant) event.

        Sets player names and sides.
        Ported from C# GameCommsHelper.ParsePartcipantEvent()
        """
        participant_id = event.get('participantId', '')
        all_participants = event.get('allParticipantIds', '')
        side = event.get('side', '')

        # Parse allParticipantIds to find opponent (like C# does)
        if all_participants:
            for participant in all_participants.split(','):
                participant = participant.strip()
                if participant and participant != self.board_state.my_player_name:
                    if not self.board_state.opponent_name:
                        self.board_state.opponent_name = participant
                        logger.info(f"👥 Opponent: {participant}")

        # Set our side if provided
        if participant_id == self.board_state.my_player_name and side:
            self.board_state.my_side = side.lower()
            logger.info(f"👤 My side: {side}")

    def _handle_turn_change(self, event: ET.Element):
        """
        Handle TC (Turn Change) event.

        Updates whose turn it is and resets per-turn tracking.
        """
        participant_id = event.get('participantId', '')
        self.board_state.current_turn_player = participant_id

        # Reset force activated this turn when turn changes to us
        if participant_id == self.board_state.my_player_name:
            self.board_state.force_activated_this_turn = 0

        logger.info(f"🔄 Turn: {participant_id}")

    def _handle_phase_change(self, event: ET.Element):
        """
        Handle GPC (Game Phase Change) event.

        Updates current phase (DEPLOY, BATTLE, MOVE, DRAW, CONTROL).
        Parses turn number from phase string (e.g., "Deploy (turn #2)").

        During Control phase, triggers location checks for Battle Order rules.
        """
        import re

        from .decision_handler import DecisionHandler

        phase = event.get('phase', '')
        old_phase = self.board_state.current_phase
        self.board_state.current_phase = phase

        # Notify decision tracker of phase change (resets loop detection)
        DecisionHandler.notify_phase_change(phase)

        # Parse turn number from phase string: "Deploy (turn #2)" -> 2
        turn_match = re.search(r'turn #(\d+)', phase)
        if turn_match:
            new_turn = int(turn_match.group(1))
            if new_turn != self.board_state.turn_number:
                self.board_state.turn_number = new_turn
                # New turn - reset strategy controller per-turn tracking
                if self.board_state.strategy_controller:
                    self.board_state.strategy_controller.start_new_turn(new_turn)
                    # Update game strategy with current board state
                    self.board_state.strategy_controller.update_strategy(self.board_state)
                    logger.info(f"📊 Strategy updated for turn {new_turn}")

        # Notify strategy controller of phase change (for location check optimization)
        if self.board_state.strategy_controller:
            self.board_state.strategy_controller.on_phase_change(phase)

        # Control phase - update my_side on strategy controller if needed
        if 'Control' in phase and self.board_state.strategy_controller:
            if self.board_state.my_side and self.board_state.strategy_controller.my_side != self.board_state.my_side:
                self.board_state.strategy_controller.my_side = self.board_state.my_side
                logger.info(f"📊 Strategy controller: side set to {self.board_state.my_side}")

        logger.info(f"⏭️  Phase: {phase} (turn {self.board_state.turn_number})")

    def _handle_start_battle(self, event: ET.Element):
        """
        Handle SB/SD/SLC/SA (Start Battle/Duel/Lightsaber Combat/Attack) events.

        Sets in_battle flag and tracks battle location for strategic decisions.
        Ported from C# BotAIHelper.StartBattle()
        """
        event_type = event.get('type', '')
        self.board_state.in_battle = True

        # Track battle location for damage assignment decisions
        location_index_str = event.get('locationIndex', '-1')
        try:
            self.board_state.current_battle_location = int(location_index_str)
        except ValueError:
            self.board_state.current_battle_location = -1

        battle_types = {
            'SB': 'Battle',
            'SD': 'Duel',
            'SLC': 'Lightsaber Combat',
            'SA': 'Attack',
        }
        battle_name = battle_types.get(event_type, 'Combat')
        logger.info(f"⚔️  {battle_name} started at location {self.board_state.current_battle_location}")

        # Notify callbacks for regular battles only (not duels/lightsaber combat)
        if event_type == 'SB':
            self._notify_battle_start()

    def _handle_end_battle(self, event: ET.Element):
        """
        Handle EB/EA/ED/ELC (End Battle/Attack/Duel/Lightsaber Combat) events.

        Clears in_battle flag and battle location.
        Also triggers battle damage callback with the final (highest) damage.
        Ported from C# BotAIHelper.EndBattle()
        """
        event_type = event.get('type', '')
        self.board_state.in_battle = False
        self.board_state.current_battle_location = -1  # Clear battle location
        self.board_state.clear_hit_cards()  # Clear hit tracking for new battle

        battle_types = {
            'EB': 'Battle',
            'EA': 'Attack',
            'ED': 'Duel',
            'ELC': 'Lightsaber Combat',
        }
        battle_name = battle_types.get(event_type, 'Combat')

        # Now that battle is over, send the final damage notification
        if self._pending_battle_damage > 0:
            logger.info(f"🏁 {battle_name} ended - final damage: {self._pending_battle_damage}")
            self._notify_battle_damage(self._pending_battle_damage)
            self._pending_battle_damage = 0  # Reset for next battle
        else:
            logger.info(f"🏁 {battle_name} ended")

    def _handle_message(self, event: ET.Element):
        """
        Handle M (Message) events.

        Checks for:
        - Game-ending messages (winner/loser)
        - Battle damage messages (e.g., "10 battle damage")

        Ported from C# AIBotModeAstrogator.SendBattleMessage
        """
        message = event.get('message', '')

        # Check for battle damage messages
        # Format: "X battle damage" where X is a number before "battle"
        # C# parsing: splits message and looks for number before "battle"
        if ' battle' in message.lower():
            self._parse_battle_damage(message)

        # Check for winner message: "PlayerName is the winner due to: Reason"
        if 'is the winner due to:' in message:
            import re
            match = re.match(r'^(.+?) is the winner due to: (.+)$', message)
            if match:
                winner_name = match.group(1)
                reason = match.group(2)
                self.board_state.game_winner = winner_name
                self.board_state.game_win_reason = reason
                logger.info(f"🏆 Game winner detected: {winner_name} ({reason})")

        # Also check for loser message for redundancy: "PlayerName lost due to: Reason"
        elif 'lost due to:' in message:
            import re
            match = re.match(r'^(.+?) lost due to: (.+)$', message)
            if match:
                loser_name = match.group(1)
                reason = match.group(2)
                # If we know the loser, we can infer the winner
                if not self.board_state.game_winner:
                    # Winner is whoever isn't the loser
                    if loser_name != self.board_state.my_player_name:
                        self.board_state.game_winner = self.board_state.my_player_name
                    else:
                        # We lost - opponent won
                        # We don't know opponent's name here, but we know we didn't win
                        self.board_state.game_winner = "opponent"
                    self.board_state.game_win_reason = reason
                    logger.info(f"🏁 Game ended: {loser_name} lost ({reason})")

    def _parse_battle_damage(self, message: str):
        """
        Parse battle damage from message text.

        Matches C# AIBotModeAstrogator.SendBattleMessage parsing:
        - Splits message by spaces
        - Looks for number preceding "battle" word
        - Example: "10 battle damage" -> damage = 10

        NOTE: Battle damage is reported multiple times during a battle as destiny
        draws happen. We track the HIGHEST damage and only send chat/record score
        when the battle ends (EB event).

        Args:
            message: Raw message text from event
        """
        tokens = message.split()
        previous_token = None

        for token in tokens:
            if token.lower() == 'battle' and previous_token is not None:
                try:
                    damage = int(previous_token)
                    if damage > 0:
                        # Track highest damage during this battle
                        # Only notify when battle ends (see _handle_end_battle)
                        if damage > self._pending_battle_damage:
                            logger.info(f"💥 Battle damage updated: {self._pending_battle_damage} -> {damage}")
                            self._pending_battle_damage = damage
                        else:
                            logger.debug(f"💥 Battle damage {damage} (pending: {self._pending_battle_damage})")
                        return
                except ValueError:
                    pass
            previous_token = token
