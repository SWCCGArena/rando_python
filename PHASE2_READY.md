# Phase 2 Ready to Test! 🚀

**Date**: 2025-11-22
**Status**: Phase 2 Implementation Complete - READY FOR TESTING

---

## What Was Built

Phase 2 networking layer is complete! The bot can now:

✅ **Connect to GEMP server**
✅ **Login with credentials**
✅ **Fetch hall tables** (updates every 3 seconds)
✅ **Display tables in admin UI** (real-time)
✅ **Create game tables**

---

## Files Created

1. **engine/models.py** - Data structures
   - `GameTable` - Table info with players
   - `Player` - Player info
   - `DeckInfo` - Deck metadata
   - `GameInfo` - Active game info

2. **engine/parser.py** - XML parsing
   - `parse_hall_tables()` - Parse table list from XML
   - `parse_login_response()` - Check login success
   - `parse_deck_list()` - Parse deck list
   - Error handling for malformed XML

3. **engine/client.py** - GEMP HTTP client
   - `login()` - Authenticate with GEMP
   - `get_hall_tables()` - Fetch table list
   - `create_table()` - Create new game table
   - `get_library_decks()` - Get available decks
   - `logout()` - Clean disconnect

4. **app.py** - Updated with worker thread
   - `bot_worker()` - Background polling loop
   - States: STOPPED → CONNECTING → IN_LOBBY → WAITING_FOR_OPPONENT
   - Real-time WebSocket updates
   - Create table from admin UI

---

## Configuration

Credentials are now set in `config.py`:
- **Username**: `rando_cal`
- **Password**: `battmann`
- **Server**: `http://localhost:8082/gemp-swccg-server/`

You can override with environment variables:
```bash
export GEMP_USERNAME="rando_cal"
export GEMP_PASSWORD="battmann"
export GEMP_SERVER_URL="http://localhost:8082/gemp-swccg-server/"
```

---

## How to Test

### 1. Restart Flask App

```bash
# Stop current app (Ctrl+C)
cd /opt/gemp/rando_cal_working/new_rando
./run.sh
```

### 2. Open Admin UI

Navigate to your nginx proxy URL

### 3. Test Login

1. Click **"Start Bot"** button
2. Watch the activity log for connection messages
3. Status should change: `stopped` → `connecting` → `in_lobby`
4. Tables panel should populate with real tables from GEMP

**Expected Logs:**
```
[timestamp] 🚀 Bot starting...
[timestamp] ✅ Connected to GEMP server
```

### 4. Test Table Listing

Once in `in_lobby` state:
- Tables should appear in the "Hall Tables" panel
- List updates every 3 seconds automatically
- You should see real tables from the GEMP server

### 5. Test Table Creation

1. Enter a table name (or use default "Rando Cal Game")
2. Select a deck from dropdown (placeholder for now)
3. Click **"Create Table"** button
4. Table should be created on GEMP server
5. Status changes to `waiting_for_opponent`

**Expected Logs:**
```
[timestamp] 📋 Create table requested: Rando Cal Game with deck Hunt Down...
[timestamp] ✅ Table created: Rando Cal Game
```

### 6. Test Stop

1. Click **"Stop Bot"** button
2. Worker thread should stop
3. Status returns to `stopped`
4. Connection cleaned up

---

## What You Should See

### Status Panel
- **Stopped**: Red/gray dot
- **Connecting**: Orange pulsing dot
- **In Lobby**: Green dot
- **Waiting for Opponent**: Green dot

### Tables Panel
Real table data:
```
Table Name (status)
Players: player1, player2
```

### Activity Log
```
[21:04:23] 🚀 Bot starting...
[21:04:24] ✅ Connected to GEMP server
[21:04:24] Hall: 0 tables
[21:04:27] Hall: 2 tables
```

### WebSocket Updates
Tables refresh automatically every 3 seconds without page reload!

---

## Troubleshooting

### Login Fails

**Check:**
1. GEMP server is running at `localhost:8082`
2. Credentials are correct (`rando_cal` / `battmann`)
3. Check logs: `logs/rando.log`

**Expected in logs:**
```
2025-11-22 21:04:24 - engine.client - INFO - Attempting login to http://localhost:8082/gemp-swccg-server/ as 'rando_cal'
2025-11-22 21:04:24 - engine.client - INFO - ✅ Login successful
```

### No Tables Showing

**Check:**
1. Are there actually tables in the hall?
2. Check browser console for JavaScript errors
3. Check network tab - should see WebSocket connection
4. Check logs for XML parsing errors

### Create Table Fails

**Check:**
1. Bot must be in `in_lobby` state first
2. Deck name must be valid (Phase 2 uses placeholder, will fix in Phase 3)
3. Check logs for HTTP response codes

---

## API Endpoints Used

Phase 2 uses these GEMP endpoints:

```
POST /login
  - Authenticates user
  - Returns session cookie

GET /hall?participantId=null
  - Gets list of tables
  - Returns XML with table data

POST /hall
  - Creates new table
  - Data: deckName, sampleDeck, format
```

---

## Next Steps - Phase 3

Once Phase 2 is tested and working:

**Phase 3: Game Joining**
1. Detect opponent joining table
2. Join game session
3. Send chat introduction
4. Implement chat commands
5. Detect game end

See `IMPLEMENTATION_PLAN.md` Phase 3 for details.

---

## Architecture Notes

### Worker Thread Pattern

```
Main Thread (Flask)          Worker Thread
     |                            |
     |-- Start Bot -->            |
     |                        Login to GEMP
     |                            |
     |<-- State Update ------     |
     |                        Poll Hall (3s)
     |<-- State Update ------     |
     |                        Poll Hall (3s)
     |                            |
```

### State Machine

```
STOPPED
   ↓ (Start button)
CONNECTING
   ↓ (Login success)
IN_LOBBY
   ↓ (Create table)
WAITING_FOR_OPPONENT
   ↓ (Opponent joins - Phase 3)
JOINING_GAME
   ↓
PLAYING
```

---

## Phase 2 Checklist - All Complete! ✅

- [x] Create engine/models.py
- [x] Create engine/parser.py
- [x] Create engine/client.py
- [x] Implement bot worker thread
- [x] Update socket handlers
- [x] Login to GEMP
- [x] Fetch hall tables
- [x] Create tables
- [x] Real-time UI updates
- [ ] **Test with real GEMP server** ← YOU ARE HERE

---

## Testing Checklist

When you test, verify:

- [ ] Bot starts successfully
- [ ] Login works (check logs)
- [ ] Tables appear in UI
- [ ] Tables update automatically
- [ ] Can create table
- [ ] Bot stops cleanly
- [ ] No errors in browser console
- [ ] No errors in logs
- [ ] WebSocket stays connected
- [ ] Status dot changes colors correctly

---

## Congratulations! 🎉

If all tests pass, **Phase 2 is complete!**

**Progress**: ~33% (2 of 6 phases done)

**Next milestone**: Phase 3 - Game joining and chat

---

Ready to test? Restart the Flask app and click "Start Bot"! 🚀
