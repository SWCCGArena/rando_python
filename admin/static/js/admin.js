/**
 * Rando Cal Admin UI - JavaScript
 *
 * Handles WebSocket communication, UI updates, and user interactions.
 */

// Connect to SocketIO
const socket = io();

// State management
let botState = {
    state: 'stopped',
    config: {},
    last_error: null,
    tables: [],
    opponent: null
};

// Connection status indicator
let connectionStatusEl = null;

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    console.log('Rando Cal Admin UI loaded');

    // Add connection status indicator
    addConnectionStatus();

    // Initial UI update
    updateUI();

    addLog('Admin UI initialized', 'info');
});

// Socket event handlers
socket.on('connect', () => {
    console.log('Connected to bot server via WebSocket');
    updateConnectionStatus(true);
    addLog('Connected to server', 'success');

    // Request current state
    socket.emit('request_state');
});

socket.on('disconnect', () => {
    console.log('Disconnected from bot server');
    updateConnectionStatus(false);
    addLog('Disconnected from server', 'error');
});

socket.on('state_update', (data) => {
    console.log('State update received:', data);
    botState = data;
    updateUI();
});

socket.on('log_message', (data) => {
    addLog(data.message, data.level || 'info');
});

socket.on('error', (error) => {
    console.error('Socket error:', error);
    addLog(`Error: ${error}`, 'error');
});

// Connection status management
function addConnectionStatus() {
    connectionStatusEl = document.createElement('div');
    connectionStatusEl.className = 'connection-status disconnected';
    connectionStatusEl.textContent = 'Disconnected';
    document.body.appendChild(connectionStatusEl);
}

function updateConnectionStatus(connected) {
    if (!connectionStatusEl) return;

    if (connected) {
        connectionStatusEl.className = 'connection-status connected';
        connectionStatusEl.textContent = 'Connected';
    } else {
        connectionStatusEl.className = 'connection-status disconnected';
        connectionStatusEl.textContent = 'Disconnected';
    }
}

// UI update functions
function updateUI() {
    updateStatus();
    updateConfig();
    updateTables();
    updateGameState();
    updateBoardVisualization();
    updateButtons();
    updateErrorMessage();
    updateDeckList();
    updateBotStats();
}

function updateStatus() {
    const statusText = document.getElementById('status-text');
    const statusDot = document.getElementById('status-dot');

    if (statusText) {
        statusText.textContent = botState.state || 'unknown';
    }

    if (statusDot) {
        statusDot.className = 'dot';

        // Add state-specific classes
        if (botState.state === 'playing' || botState.state === 'in_lobby') {
            statusDot.classList.add('running');
        } else if (botState.state === 'error') {
            statusDot.classList.add('error');
        } else if (botState.state === 'connecting') {
            statusDot.classList.add('connecting');
        }
    }
}

function updateConfig() {
    if (!botState.config) return;

    // Update server selector
    const serverSelect = document.getElementById('gemp-server-select');
    const serverWarning = document.getElementById('server-change-warning');
    const botMode = document.getElementById('bot-mode');
    const tableNameDisplay = document.getElementById('table-name-display');

    if (serverSelect) {
        // Update selected value based on current server
        const currentServer = botState.config.gemp_server || '';
        if (currentServer.includes('localhost')) {
            serverSelect.value = 'http://localhost:8082/gemp-swccg-server/';
        } else if (currentServer.includes('starwarsccg.org')) {
            serverSelect.value = 'https://gemp.starwarsccg.org/gemp-swccg-server/';
        }

        // Enable/disable based on bot state - only allow changes when stopped
        const isStopped = botState.state === 'stopped' || botState.state === 'error';
        serverSelect.disabled = !isStopped;
        if (serverWarning) {
            serverWarning.style.display = isStopped ? 'none' : 'inline';
        }
    }

    if (botMode) botMode.textContent = botState.config.bot_mode || 'Not set';
    if (tableNameDisplay) tableNameDisplay.textContent = botState.config.table_name || 'Not set';

    // Update auto-start checkbox
    const autoStartCheckbox = document.getElementById('auto-start-checkbox');
    if (autoStartCheckbox && botState.config.auto_start !== undefined) {
        autoStartCheckbox.checked = botState.config.auto_start;
    }

    // Update slider values - Hand Management
    const maxHandSize = document.getElementById('max-hand-size');
    const maxHandValue = document.getElementById('max-hand-value');
    if (maxHandSize && botState.config.max_hand_size !== undefined) {
        maxHandSize.value = botState.config.max_hand_size;
        if (maxHandValue) maxHandValue.textContent = botState.config.max_hand_size;
    }

    const handSoftCap = document.getElementById('hand-soft-cap');
    const handSoftCapValue = document.getElementById('hand-soft-cap-value');
    if (handSoftCap && botState.config.hand_soft_cap !== undefined) {
        handSoftCap.value = botState.config.hand_soft_cap;
        if (handSoftCapValue) handSoftCapValue.textContent = botState.config.hand_soft_cap;
    }

    // Update slider values - Force Economy
    const forceGenTarget = document.getElementById('force-gen-target');
    const forceGenTargetValue = document.getElementById('force-gen-target-value');
    if (forceGenTarget && botState.config.force_gen_target !== undefined) {
        forceGenTarget.value = botState.config.force_gen_target;
        if (forceGenTargetValue) forceGenTargetValue.textContent = botState.config.force_gen_target;
    }

    const maxReserveChecks = document.getElementById('max-reserve-checks');
    const maxReserveChecksValue = document.getElementById('max-reserve-checks-value');
    if (maxReserveChecks && botState.config.max_reserve_checks !== undefined) {
        maxReserveChecks.value = botState.config.max_reserve_checks;
        if (maxReserveChecksValue) maxReserveChecksValue.textContent = botState.config.max_reserve_checks;
    }

    // Update slider values - Battle Strategy
    const deployThreshold = document.getElementById('deploy-threshold');
    const deployThresholdValue = document.getElementById('deploy-threshold-value');
    if (deployThreshold && botState.config.deploy_threshold !== undefined) {
        deployThreshold.value = botState.config.deploy_threshold;
        if (deployThresholdValue) deployThresholdValue.textContent = botState.config.deploy_threshold;
    }

    const battleFavorable = document.getElementById('battle-favorable');
    const battleFavorableValue = document.getElementById('battle-favorable-value');
    if (battleFavorable && botState.config.battle_favorable_threshold !== undefined) {
        battleFavorable.value = botState.config.battle_favorable_threshold;
        if (battleFavorableValue) battleFavorableValue.textContent = botState.config.battle_favorable_threshold;
    }

    const battleDanger = document.getElementById('battle-danger');
    const battleDangerValue = document.getElementById('battle-danger-value');
    if (battleDanger && botState.config.battle_danger_threshold !== undefined) {
        battleDanger.value = botState.config.battle_danger_threshold;
        if (battleDangerValue) battleDangerValue.textContent = botState.config.battle_danger_threshold;
    }
}

function updateTables() {
    const tablesList = document.getElementById('tables-list');
    if (!tablesList) return;

    if (!botState.tables || botState.tables.length === 0) {
        tablesList.innerHTML = '<p class="placeholder">No tables available</p>';
        return;
    }

    tablesList.innerHTML = '';
    botState.tables.forEach(table => {
        const tableDiv = document.createElement('div');
        tableDiv.className = 'table-item';

        const players = table.players && table.players.length > 0
            ? table.players.join(', ')
            : 'No players';

        tableDiv.innerHTML = `
            <strong>${escapeHtml(table.name)}</strong>
            <span style="color: #666;">(${escapeHtml(table.status)})</span>
            <br>
            <small>Players: ${escapeHtml(players)}</small>
        `;
        tablesList.appendChild(tableDiv);
    });
}

function updateGameState() {
    const gameStateEl = document.getElementById('game-state');
    if (!gameStateEl) return;

    if (botState.opponent) {
        gameStateEl.innerHTML = `
            <p><strong>Opponent:</strong> ${escapeHtml(botState.opponent)}</p>
            <p><strong>Table:</strong> ${escapeHtml(botState.current_table_id || 'Unknown')}</p>
        `;
    } else {
        gameStateEl.innerHTML = '<p class="placeholder">No game in progress</p>';
    }

    // Update board state summary
    const boardStateEl = document.getElementById('board-state-summary');
    if (boardStateEl) {
        if (botState.board_state) {
            boardStateEl.style.display = 'block';

            const bs = botState.board_state;
            document.getElementById('bs-phase').textContent = bs.phase || '-';
            document.getElementById('bs-my-turn').textContent = bs.my_turn ? '✅ Yes' : '❌ No';
            document.getElementById('bs-my-side').textContent = bs.my_side ? bs.my_side.toUpperCase() : '-';
            document.getElementById('bs-force').textContent = bs.force || '0';
            document.getElementById('bs-reserve').textContent = bs.reserve || '0';
            document.getElementById('bs-hand').textContent = bs.hand_size || '0';

            const myPowerEl = document.getElementById('bs-my-power');
            const theirPowerEl = document.getElementById('bs-their-power');
            myPowerEl.textContent = bs.my_power || '0';
            theirPowerEl.textContent = bs.their_power || '0';

            // Color code power values
            myPowerEl.style.color = bs.my_power > bs.their_power ? '#4CAF50' : (bs.my_power < bs.their_power ? '#f44336' : '#999');
            theirPowerEl.style.color = bs.their_power > bs.my_power ? '#4CAF50' : (bs.their_power < bs.my_power ? '#f44336' : '#999');

            document.getElementById('bs-locations').textContent = (bs.locations && bs.locations.length) || '0';
        } else {
            boardStateEl.style.display = 'none';
        }
    }
}

function updateBoardVisualization() {
    const boardViz = document.getElementById('board-visualization');
    if (!boardViz) return;

    if (!botState.board_state || !botState.board_state.locations) {
        boardViz.innerHTML = '<span class="placeholder">Waiting for game to start...</span>';
        return;
    }

    const bs = botState.board_state;
    let html = '';

    // Header
    html += `<div class="board-header">`;
    html += `<strong>Phase:</strong> ${escapeHtml(bs.phase || 'Unknown')} | `;
    html += `<strong>Turn:</strong> ${bs.my_turn ? 'Mine' : 'Theirs'}\n`;
    html += `</div>\n\n`;

    // Resources comparison
    html += `<div class="board-resources">`;
    html += `<div class="resource-col">`;
    html += `  <strong class="my-label">My Resources:</strong>\n`;
    html += `  Force: ${bs.force}  Used: ${bs.used}  Reserve: ${bs.reserve}  Lost: ${bs.lost}\n`;
    html += `  Hand: ${bs.hand_size} cards\n`;
    html += `</div>`;
    html += `<div class="resource-col">`;
    html += `  <strong class="their-label">Their Resources:</strong>\n`;
    html += `  Force: ${bs.their_force}  Used: ${bs.their_used}  Reserve: ${bs.their_reserve}  Lost: ${bs.their_lost}\n`;
    html += `  Hand: ${bs.their_hand_size} cards\n`;
    html += `</div>`;
    html += `</div>\n\n`;

    // Power summary
    const powerDiff = bs.power_advantage || 0;
    const powerSymbol = powerDiff > 0 ? '↑' : (powerDiff < 0 ? '↓' : '=');
    const powerClass = powerDiff > 0 ? 'winning' : (powerDiff < 0 ? 'losing' : 'tied');
    html += `<div class="board-power ${powerClass}">`;
    html += `Power: <strong>${bs.my_power}</strong> vs <strong>${bs.their_power}</strong> `;
    html += `<span class="advantage">(${powerSymbol} ${Math.abs(powerDiff)})</span>`;
    html += `</div>\n\n`;

    // Locations
    html += `<div class="board-locations">`;
    html += `<strong>🏛️  Locations (${bs.locations.length}):</strong>\n\n`;

    if (bs.locations && bs.locations.length > 0) {
        bs.locations.forEach(loc => {
            html += `<div class="location">`;
            // Show both system and site name if they're different
            let locName = escapeHtml(loc.site_name || loc.system_name || 'Unknown');
            if (loc.is_site && loc.system_name && loc.site_name && loc.system_name !== loc.site_name) {
                locName = `${escapeHtml(loc.site_name)} <small>(${escapeHtml(loc.system_name)})</small>`;
            }
            // Add location type badges
            let typeBadges = '';
            if (loc.is_space) typeBadges += '<span class="loc-type-badge space">Space</span>';
            if (loc.is_ground) typeBadges += '<span class="loc-type-badge ground">Ground</span>';
            html += `  <div class="loc-name">${locName} ${typeBadges}</div>\n`;
            html += `  <div class="loc-power">Power: <span class="my-power">${loc.my_power}</span> vs <span class="their-power">${loc.their_power}</span></div>\n`;

            if (loc.my_cards && loc.my_cards.length > 0) {
                html += `  <div class="loc-cards my-cards-section">\n`;
                html += `    <div class="cards-label">My Cards:</div>\n`;
                loc.my_cards.forEach(card => {
                    html += `    • ${escapeHtml(card.name)}`;
                    if (card.power || card.ability) {
                        html += ` [P:${card.power}/A:${card.ability}]`;
                    }
                    if (card.card_id) html += ` <span class="card-id">#${card.card_id}</span>`;
                    html += `\n`;
                });
                html += `  </div>\n`;
            }

            if (loc.their_cards && loc.their_cards.length > 0) {
                html += `  <div class="loc-cards their-cards-section">\n`;
                html += `    <div class="cards-label">Their Cards:</div>\n`;
                loc.their_cards.forEach(card => {
                    html += `    • ${escapeHtml(card.name)}`;
                    if (card.power || card.ability) {
                        html += ` [P:${card.power}/A:${card.ability}]`;
                    }
                    if (card.card_id) html += ` <span class="card-id">#${card.card_id}</span>`;
                    html += `\n`;
                });
                html += `  </div>\n`;
            }

            html += `</div>\n`;
        });
    } else {
        html += `  <div class="empty">No locations yet</div>\n`;
    }
    html += `</div>\n\n`;

    // Hand
    html += `<div class="board-hand">`;
    html += `<strong>🃏  My Hand (${bs.hand ? bs.hand.length : 0}):</strong>\n`;
    if (bs.hand && bs.hand.length > 0) {
        bs.hand.forEach(card => {
            html += `  • ${escapeHtml(card.name)}`;
            if (card.type) html += ` (${card.type})`;
            if (card.deploy !== undefined && card.deploy !== null) html += ` - Deploy: ${card.deploy}`;
            if (card.card_id) html += ` <span class="card-id">#${card.card_id}</span>`;
            html += `\n`;
        });
    } else {
        html += `  <div class="empty">No cards in hand</div>\n`;
    }
    html += `</div>`;

    boardViz.innerHTML = html;
}

function updateButtons() {
    const btnStart = document.getElementById('btn-start');
    const btnStop = document.getElementById('btn-stop');
    const btnCreateTable = document.getElementById('btn-create-table');
    const btnLeaveTable = document.getElementById('btn-leave-table');

    if (!btnStart || !btnStop) return;

    const state = botState.state;

    // Start/Stop buttons
    if (state === 'stopped' || state === 'error') {
        btnStart.disabled = false;
        btnStop.disabled = true;
    } else {
        btnStart.disabled = true;
        btnStop.disabled = false;
    }

    // Table management buttons
    if (btnCreateTable && btnLeaveTable) {
        const hasTable = botState.current_table_id !== null && botState.current_table_id !== undefined;

        if (hasTable) {
            // We have a table - disable create, show leave
            btnCreateTable.disabled = true;
            btnCreateTable.style.opacity = '0.5';
            btnLeaveTable.style.display = 'inline-block';
        } else {
            // No table - enable create, hide leave
            btnCreateTable.disabled = false;
            btnCreateTable.style.opacity = '1';
            btnLeaveTable.style.display = 'none';
        }
    }
}

function updateErrorMessage() {
    const errorMsg = document.getElementById('error-message');
    if (!errorMsg) return;

    if (botState.last_error) {
        errorMsg.textContent = botState.last_error;
        errorMsg.style.display = 'block';
    } else {
        errorMsg.textContent = '';
        errorMsg.style.display = 'none';
    }
}

function updateDeckList() {
    const deckSelect = document.getElementById('deck-select');
    if (!deckSelect) return;

    // Only update if we have decks
    if (!botState.decks || (!botState.decks.library && !botState.decks.user)) {
        return;
    }

    // Clear existing options
    deckSelect.innerHTML = '';

    // Add "Random" option at the top (default)
    const randomOption = document.createElement('option');
    randomOption.value = '__RANDOM__';
    randomOption.textContent = '🎲 Random Deck';
    randomOption.dataset.isLibrary = 'true';
    randomOption.selected = true;
    deckSelect.appendChild(randomOption);

    // Add library decks
    if (botState.decks.library && botState.decks.library.length > 0) {
        const libraryGroup = document.createElement('optgroup');
        libraryGroup.label = 'Library Decks';

        botState.decks.library.forEach(deck => {
            const option = document.createElement('option');
            option.value = deck.name;
            option.textContent = `${deck.name} (${deck.side || 'unknown'})`;
            option.dataset.isLibrary = 'true';
            libraryGroup.appendChild(option);
        });

        deckSelect.appendChild(libraryGroup);
    }

    // Add user decks
    if (botState.decks.user && botState.decks.user.length > 0) {
        const userGroup = document.createElement('optgroup');
        userGroup.label = 'My Decks';

        botState.decks.user.forEach(deck => {
            const option = document.createElement('option');
            option.value = deck.name;
            option.textContent = `${deck.name} (${deck.side || 'unknown'})`;
            option.dataset.isLibrary = 'false';
            userGroup.appendChild(option);
        });

        deckSelect.appendChild(userGroup);
    }

    // If no decks at all, show placeholder
    if (deckSelect.options.length === 0) {
        const option = document.createElement('option');
        option.textContent = 'No decks available';
        option.disabled = true;
        deckSelect.appendChild(option);
    }
}

function updateBotStats() {
    if (!botState.bot_stats) return;

    const stats = botState.bot_stats;

    const totalGames = document.getElementById('stats-total-games');
    const wins = document.getElementById('stats-wins');
    const losses = document.getElementById('stats-losses');
    const winRate = document.getElementById('stats-win-rate');
    const players = document.getElementById('stats-players');
    const achievements = document.getElementById('stats-achievements');

    if (totalGames) totalGames.textContent = stats.total_games || 0;
    if (wins) wins.textContent = stats.total_wins || 0;
    if (losses) losses.textContent = stats.total_losses || 0;
    if (winRate) winRate.textContent = (stats.win_rate || 0) + '%';
    if (players) players.textContent = stats.unique_players || 0;
    if (achievements) achievements.textContent = stats.total_achievements || 0;
}

// Control functions
function startBot() {
    console.log('Starting bot...');
    socket.emit('start_bot');
    addLog('Start command sent', 'info');
}

function stopBot() {
    console.log('Stopping bot...');
    socket.emit('stop_bot');
    addLog('Stop command sent', 'info');
}

function sendConfigUpdate(key, value) {
    console.log(`Updating config: ${key} = ${value}`);
    socket.emit('update_config', { key, value });

    // Update display immediately
    const displayId = key.replace(/_/g, '-') + '-value';
    const display = document.getElementById(displayId);
    if (display) {
        display.textContent = value;
    }
}

function changeServer(serverUrl) {
    // Only allow changing when bot is stopped
    if (botState.state !== 'stopped' && botState.state !== 'error') {
        addLog('Cannot change server while bot is running. Stop the bot first.', 'warning');
        // Reset selector to current value
        updateConfig();
        return;
    }

    console.log(`Changing GEMP server to: ${serverUrl}`);
    socket.emit('change_server', { server_url: serverUrl });
    addLog(`Changing server to: ${serverUrl}`, 'info');
}

function toggleAutoStart(enabled) {
    console.log(`Toggling auto-start: ${enabled}`);
    socket.emit('toggle_auto_start', { enabled: enabled });
    addLog(`Auto-start ${enabled ? 'enabled' : 'disabled'}`, 'info');
}

function createTable() {
    const tableName = document.getElementById('new-table-name').value;
    const deckSelect = document.getElementById('deck-select');
    const deckName = deckSelect.value;

    // Check if it's a library deck
    const selectedOption = deckSelect.options[deckSelect.selectedIndex];
    const isLibrary = selectedOption.dataset.isLibrary === 'true';

    console.log(`Creating table: ${tableName} with deck: ${deckName} (library: ${isLibrary})`);

    socket.emit('create_table', {
        table_name: tableName,
        deck_name: deckName,
        is_library: isLibrary
    });

    addLog(`Creating table "${tableName}"...`, 'info');
}

function leaveTable() {
    if (confirm('Are you sure you want to leave the current table?')) {
        console.log('Leaving table...');
        socket.emit('leave_table');
        addLog('Leaving table...', 'info');
    }
}

// Logging
function addLog(message, level = 'info') {
    const logPanel = document.getElementById('activity-log');
    if (!logPanel) return;

    const entry = document.createElement('p');
    entry.className = 'log-entry';

    const timestamp = new Date().toLocaleTimeString();

    const timestampSpan = document.createElement('span');
    timestampSpan.className = 'timestamp';
    timestampSpan.textContent = `[${timestamp}]`;

    const msgSpan = document.createElement('span');
    msgSpan.className = 'log-msg';
    msgSpan.textContent = message;

    // Color code by level
    if (level === 'error') {
        msgSpan.style.color = '#f44336';
    } else if (level === 'success') {
        msgSpan.style.color = '#4CAF50';
    } else if (level === 'warning') {
        msgSpan.style.color = '#ff9800';
    }

    entry.appendChild(timestampSpan);
    entry.appendChild(document.createTextNode(' '));
    entry.appendChild(msgSpan);

    logPanel.insertBefore(entry, logPanel.firstChild);

    // Keep only last 100 entries
    while (logPanel.children.length > 100) {
        logPanel.removeChild(logPanel.lastChild);
    }
}

// Utility functions
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
    // Ctrl/Cmd + S: Start bot
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        if (!document.getElementById('btn-start').disabled) {
            startBot();
        }
    }

    // Ctrl/Cmd + X: Stop bot
    if ((e.ctrlKey || e.metaKey) && e.key === 'x') {
        e.preventDefault();
        if (!document.getElementById('btn-stop').disabled) {
            stopBot();
        }
    }
});

// Periodic state refresh (every 5 seconds)
setInterval(() => {
    if (socket.connected) {
        socket.emit('request_state');
    }
}, 5000);

console.log('Admin JS loaded and ready');
