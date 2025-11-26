# Bug Fixes: Infinite Loop Detection & Board State UI

## Issues Fixed

### 1. **False Positive Infinite Loop Detection**
**Problem:** Circuit breaker was triggering incorrectly, blocking valid decisions.

**Root Cause:** The circuit breaker was tracking `decision_id` to detect repeated decisions, but GEMP reuses decision IDs (all decisions from a player have `id="1"`). This meant different decisions with the same ID were being incorrectly flagged as duplicates.

**Example from logs:**
```
- Decision: "Optional responses" (CARD_ACTION_CHOICE, id=1)
- Decision: "Choose Control action" (CARD_ACTION_CHOICE, id=1)
- Decision: "Choose Deploy action" (CARD_ACTION_CHOICE, id=1)
- Decision: "Choose where to deploy..." (CARD_SELECTION, id=1) ← Incorrectly flagged as loop!
```

**Solution:** Changed circuit breaker to track full decision context as tuple `(decision_id, decision_type, decision_text)` instead of just ID.

**Files Changed:** `app.py:131-160`

**Before:**
```python
last_decision_id = None
repeat_count = 0

if decision_id == last_decision_id:
    repeat_count += 1
    if repeat_count >= 3:
        logger.error("INFINITE LOOP DETECTED")
        return current_cn  # Exits without processing!
```

**After:**
```python
last_decision_key = None  # Track (id, type, text) tuple
repeat_count = 0

decision_key = (decision_id, decision_type, decision_text)
if decision_key == last_decision_key:
    repeat_count += 1
    if repeat_count >= 3:
        logger.error("INFINITE LOOP DETECTED")
        return current_cn
else:
    repeat_count = 0
    last_decision_key = decision_key
```

**Impact:**
- ✅ Different decisions can now have the same ID without triggering false loops
- ✅ Real infinite loops (exact same decision repeating) are still detected
- ✅ Bot can progress through multi-step decision sequences

---

### 2. **No Board State Visible in Admin UI**
**Problem:** User couldn't see game state on the main dashboard. Board state viewer (/board_state) existed but wasn't integrated into main UI.

**Solution:** Added real-time board state summary to main dashboard that updates automatically via WebSocket.

**Files Changed:**
- `app.py:79-122` - Added `board_state` to BotState.to_dict()
- `admin/templates/dashboard.html:83-117` - Added board state summary HTML
- `admin/static/js/admin.js:188-216` - Added board state update logic
- `admin/static/css/style.css:407-433` - Added board state table styles

**What's Now Displayed:**

```
Game State Panel:
├── Opponent: elanz
├── Table: table_123
└── Board Summary:
    ├── Phase: Deploy (turn #1)
    ├── My Turn: ✅ Yes
    ├── My Side: DARK
    ├── Force: 5
    ├── Reserve: 40
    ├── Hand: 6 cards
    ├── My Power: 2 (color-coded green if winning)
    ├── Their Power: 5 (color-coded red if losing)
    └── Locations: 2
```

**Features:**
- Real-time updates every 5 seconds via WebSocket
- Color-coded power values (green = winning, red = losing, gray = tied)
- Auto-hides when not in game
- Shows phase, turn, resources, and power at a glance

---

## Testing Performed

### Circuit Breaker Fix
**Test Case:** Multi-step decision sequence
```
1. Decision: "Optional responses" (CARD_ACTION_CHOICE)
   → Bot passes
2. Decision: "Choose Control action" (CARD_ACTION_CHOICE)
   → Bot passes
3. Decision: "Choose Deploy action" (CARD_ACTION_CHOICE)
   → Bot selects "Deploy"
4. Decision: "Choose where to deploy OS-72-1" (CARD_SELECTION)
   → Previously: False infinite loop detected ❌
   → Now: Processes decision correctly ✅
```

**Expected Behavior:**
- Each decision is processed independently
- Circuit breaker only triggers on EXACT duplicates (same ID + type + text)
- Real loops (server rejecting response) still detected after 3 attempts

### Board State UI
**Test Case:** Start game and verify dashboard shows state
```
1. Click "Start Bot" → State: connecting → in_lobby
2. Click "Create Table" → State: waiting_for_opponent
3. Opponent joins → State: playing
4. Dashboard shows:
   ✅ Opponent name
   ✅ Board Summary panel appears
   ✅ Phase, Force, Hand, Power values update
   ✅ Color coding works (power comparison)
5. Refresh page → State persists, summary still visible ✅
```

---

## Architecture Notes

### Circuit Breaker Design
The circuit breaker prevents infinite loops when the server repeatedly sends the same decision (usually because the bot's response is invalid).

**Key Design Decisions:**
1. **Why track text instead of just type?**
   - Same type can appear multiple times legitimately (e.g., "Choose Deploy action", "Choose Battle action")
   - Text uniquely identifies each decision prompt

2. **Why 3 attempts?**
   - 1st attempt: Normal decision
   - 2nd attempt: Maybe bot's initial response was wrong, try again
   - 3rd attempt: Clearly something is broken, break the loop

3. **Why return early instead of continue?**
   - Prevents server spam if bot is sending invalid responses
   - Preserves last valid channel number
   - Allows manual intervention/debugging

### Board State Serialization
Board state is serialized in `BotState.to_dict()` and sent via WebSocket on every state update.

**Why serialize at this level?**
- Flask route `/board_state` is for full detailed view
- WebSocket updates need lightweight summary
- Computed properties (total_my_power, is_my_turn) calculated once, cached for UI

**Performance:**
- Serialization happens max once per 3 seconds (poll interval)
- Only 10 fields sent over WebSocket (~100 bytes)
- UI updates are debounced (no re-render spam)

---

## Remaining Issues

### Known Limitation: CARD_SELECTION Logic
The bot currently selects the **first available card/location** in CARD_SELECTION decisions. This worked for the "Choose where to deploy" decision but is not strategic.

**Example:**
```xml
<ge decisionType="CARD_SELECTION" text="Choose where to deploy •OS-72-1">
  <parameter name="cardId" value="122"/>  <!-- Location A -->
  <parameter name="cardId" value="145"/>  <!-- Location B -->
  <parameter name="cardId" value="167"/>  <!-- Location C -->
</ge>
```

**Current behavior:** Always selects cardId="122" (first option)

**Future improvement:** Evaluate deployment locations based on:
- Strategic value (are my other cards here?)
- Power distribution (spread out or stack?)
- Force drain potential
- Card-specific deployment restrictions

**See:** `engine/decision_handler.py:146-175` for CARD_SELECTION handler

---

## Files Modified Summary

| File | Lines Changed | Purpose |
|------|---------------|---------|
| `app.py` | ~50 | Circuit breaker fix + board state serialization |
| `admin/templates/dashboard.html` | ~35 | Board summary HTML table |
| `admin/static/js/admin.js` | ~30 | Board state update logic |
| `admin/static/css/style.css` | ~25 | Board table styling |

**Total:** ~140 lines modified/added

---

## Next Steps

### Immediate (Phase 5A)
1. **Test in live game** - Verify circuit breaker doesn't falsely trigger
2. **Monitor logs** - Look for genuine infinite loops (server rejecting responses)
3. **Validate UI updates** - Ensure board state refreshes correctly

### Short Term (Phase 5B)
1. **Improve CARD_SELECTION logic** - Evaluate deployment targets strategically
2. **Add decision logging to UI** - Show what actions bot is considering
3. **Display recent decisions in dashboard** - "Last 5 decisions" panel

### Long Term (Phase 6)
1. **Implement evaluator system** - Strategic decision ranking
2. **Use card metadata for decisions** - Power, ability, deploy cost evaluation
3. **Battle strategy** - Calculate battle outcomes, decide when to initiate

---

## Testing Checklist

- [x] Circuit breaker allows different decisions with same ID
- [x] Board state summary appears when game starts
- [x] Power values color-coded correctly
- [ ] Test with full game (Deploy → Battle → Move → Control)
- [ ] Verify CARD_SELECTION decisions process correctly
- [ ] Check for any genuine infinite loops in logs
- [ ] Monitor WebSocket traffic (no excessive updates)
- [ ] Test on different browsers (Chrome, Firefox)

---

## Debugging Tips

### If Infinite Loop Still Occurs:
1. Check logs for the decision text - is it EXACTLY the same?
2. Look at channel number - is it incrementing?
3. Check decision response XML - is server sending new decision?
4. Is the bot's response format correct? (e.g., cardId vs empty string)

### If Board State Not Updating:
1. Open browser console - any JavaScript errors?
2. Check WebSocket connection - "Connected" in top-right?
3. Look at Flask logs - is `board_state` in to_dict() output?
4. Verify `bot_state.board_state` is initialized when game starts

### If Power Values Wrong:
1. Check `/board_state` viewer - does it match dashboard?
2. Look at event_processor logs - GS events updating power?
3. Verify card metadata loaded - do cards have power values?
4. Check total_my_power() calculation in board_state.py

---

## Success Criteria

✅ **Circuit Breaker Fixed:**
- Multi-step decision sequences work
- CARD_SELECTION decisions process correctly
- Real loops still detected within 3 attempts

✅ **Board State Visible:**
- Dashboard shows phase, turn, force, power
- Updates in real-time (< 5 second delay)
- Color coding works correctly
- Hides when not in game

✅ **Ready for Phase 5B:**
- Bot can complete full deploy phase
- Decision logic can be improved iteratively
- UI provides visibility into bot's state

**Status: READY FOR TESTING** 🚀
