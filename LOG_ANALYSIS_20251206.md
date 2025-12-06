# Log Analysis - December 6, 2025

Analysis of overnight bot gameplay logs to identify errors, loops, and deployment planning issues.

## Summary

| Category | Count | Severity |
|----------|-------|----------|
| Python Errors | 1 | HIGH |
| HTTP Timeouts | 2 | LOW (recovered) |
| Loop Detections | ~20 | LOW (handled) |
| Safety Corrections | 1 | MEDIUM |
| Deploy Plan Failures | Multiple | HIGH |

---

## 1. Critical Python Error

### AttributeError: `card_sub_type`

**File**: `engine/evaluators/card_selection_evaluator.py:1506`

**Error**:
```
AttributeError: 'Card' object has no attribute 'card_sub_type'
```

**Full Traceback** (from `rando_20251206_071930_vs_MGB_loss.log`):
```
File "engine/decision_handler.py", line 516, in _use_brain
    decision = brain.make_decision(context)
File "brain/static_brain.py", line 67, in make_decision
    best_action = self.combined_evaluator.evaluate_decision(evaluator_context)
File "engine/evaluators/base.py", line 404, in evaluate_decision
    actions = evaluator.evaluate(context)
File "engine/evaluators/card_selection_evaluator.py", line 75, in evaluate
    return self._evaluate_starting_deploy(context)
File "engine/evaluators/card_selection_evaluator.py", line 1506, in _evaluate_starting_deploy
    is_battleground = "battleground" in (card_meta.card_sub_type or "").lower()
```

**Root Cause**: The `Card` class (from board_state.py) doesn't have a `card_sub_type` attribute. The code is trying to access card metadata but using the wrong attribute name.

**Fix Needed**:
- Check actual Card class attributes in `board_state.py`
- Use correct attribute (possibly `card_type` or need to fetch from card_loader)

---

## 2. HTTP/Network Issues

### Connection Timeouts (Recovered)

**File**: `rando_20251206_141520_vs_lepstein01_win.log`

```
14:10:55 - Game update request failed: HTTPSConnectionPool read timed out (15s)
14:11:11 - Game update request failed: HTTPSConnectionPool read timed out (15s)
14:11:33 - Connection recovered successfully
```

**Assessment**: LOW severity - Bot handled recovery properly.

---

## 3. Loop Detection Issues (HANDLED)

### Force Drain Loops

**File**: `rando_20251206_052347_vs_oboe-wan_loss.log`

```
05:01:19 - LOOP DETECTED: 2-decision sequence repeated 2x
   Step 1: CARD_ACTION_CHOICE:Choose Control action or Pass -> '0'
   Step 2: CARD_ACTION_CHOICE:Choose Control action or Pass -> '0'
```

**Assessment**: LOW severity - The loop detection system correctly identified and broke these mild loops by random passing. This is working as designed.

---

## 4. Safety Corrections

### Empty Response Forced

**File**: `rando_20251206_180041_vs_jlock_loss.log`

```
17:48:38 - Brain chose: '' | Pass / Do nothing
17:48:38 - SAFETY FORCED: ACTION_CHOICE empty response -> using last action '1' (no cancel found)
```

**Assessment**: MEDIUM - The brain selected "pass" but the decision required an actual selection. Safety system caught and corrected it.

---

## 5. CRITICAL: Deployment Planning Failures

### Issue A: Piloted Starships Not Being Deployed

**Scenario** (`rando_20251206_081839_vs_oboe-wan_loss.log` at 08:17:33):

Bot has in hand:
- `•Han, Chewie, And The Falcon (V)` - **has_pilot (8 power)**, cost=6
- `•Wedge In Red Squadron 1` - **has_pilot (6 power)**, cost=4
- `•Red 8` - needs_pilot (0 power), cost=2
- `•Red 2` - needs_pilot (0 power), cost=2

Board state:
- Space target available: `•Yavin 4` (2 opponent icons, 1 our icon)
- Force available: 7 (6 for deploying)
- Unpiloted ships on board: `•Red 8 (#224)`, `•Red 2 (#253)`

**Result**:
```
📊 Generated 0 ground plans, 0 space plans
📋 FINAL PLAN: HOLD BACK - have 4 starships but no space targets
```

**Problem**: Bot has piloted starships that could deploy to space, but the planner only logs:
```
⚠️ Space: 1 unpiloted ship(s) but NO pilots in hand
```

**The planner ignores the 2 piloted ships (Falcon, Wedge) that could deploy independently!**

---

### Issue B: "No Good Deployment Options" With Available Resources

**Scenario** (`rando_20251206_062045_vs_auzi_loss.log` at 06:18:04):

Board analysis shows:
- Ground targets available: `•Xizor's Palace: Uplink Station`, `•Coruscant: Xizor's Palace`, etc.
- Space targets available: `•Naboo`, `•Rendili`
- Hand has vehicles: `Sandcrawler (#229)`, `Sandcrawler (#258)`, `•Bravo 4`

**Result**:
```
📊 Generated 0 ground plans, 0 space plans
📋 FINAL PLAN: HOLD BACK - No good deployment options
```

**Problem**: The planner sees targets but generates 0 plans because there are "no characters in hand" - even though vehicles/starships exist that could be useful.

---

### Issue C: High Force Held Back

**Multiple Games**: Bot had 6-14+ force available but held back deploying.

Example (`rando_20251206_014451_vs_Schwill_loss.log`):
```
01:24:58 - 🔍 _get_all_deployable_cards: 8 cards in hand, 14 force available
01:24:58 - 📋 FINAL PLAN: HOLD BACK - have 1 starships but no space targets
```

**Bot had 14 force and 8 cards but deployed nothing** because it couldn't create a space plan for its 1 starship.

---

## 6. Specific Scenarios for Review

### Scenario 1: Falcon Should Have Deployed

**Game**: `rando_20251206_081839_vs_oboe-wan_loss.log`
**Time**: 08:17:33
**Turn**: 7
**Life Force**: 7 (low)

**Hand**:
| Card | Type | Power | Cost |
|------|------|-------|------|
| •Luke Skywalker (V) | Character | 3 | 3 | (SKIPPED - already on board) |
| •Red 8 | Starship | 0* | 2 | needs pilot |
| Rebel Barrier | Interrupt | - | - | |
| •Han, Chewie, And The Falcon (V) | Starship | **8** | 6 | **has pilot** |
| •Red 2 | Starship | 0* | 2 | needs pilot |
| •Wedge In Red Squadron 1 | Starship | **6** | 4 | **has pilot** |

**Board**:
- •Yavin 4 (space): our=5 power, their=0
- •Endor: Landing Platform: their=7 power (Lord Vader)
- •Death Star II: Throne Room: their=9 power (Emperor)

**Decision**: Bot should have deployed Wedge (4 cost) or Falcon (6 cost) to Yavin 4 for space control, but instead HELD BACK.

---

### Scenario 2: Sandcrawlers Could Deploy

**Game**: `rando_20251206_062045_vs_auzi_loss.log`
**Time**: 06:18:04
**Turn**: 5
**Life Force**: 9 (critical)

**Available vehicles on board**:
- `•Bravo 4` (unpiloted starfighter)
- `Sandcrawler (#229)` (ground vehicle)
- `Sandcrawler (#258)` (ground vehicle)

**Ground targets listed**:
- `•Tatooine: Jawa Camp` (exterior, 0 power each side)
- `•Coruscant: Xizor's Palace` (exterior, 0 our power)

**Decision**: Bot couldn't deploy because "No good deployment options" - but Sandcrawlers could theoretically deploy to exterior locations.

---

## 7. Root Cause Analysis

### Deploy Planner Logic Gap

The `deploy_planner.py` generates plans based on:
1. Characters -> Ground locations
2. Starships -> Space locations (but focuses on unpiloted ships needing pilots)
3. Vehicles -> Exterior ground locations

**The gap**:
- Piloted starships (permanent pilots like Falcon, Wedge in Red Squadron 1) are logged as `has_pilot` but NOT included in space deployment plans
- The planner only considers "unpiloted ships need pilots" scenario
- If no characters in hand AND unpiloted ships exist, it concludes "no good options" even if piloted ships could deploy

### Code Location to Fix

File: `engine/deploy_planner.py`

The space planning logic needs to:
1. Include piloted starships as independent deployment options
2. Not skip all space plans just because unpiloted ships exist without pilots

---

## 8. Recommendations

### Priority 1: Fix card_sub_type AttributeError - FIXED
- [x] Check `Card` class in `board_state.py` for correct attribute name
- [x] Update `card_selection_evaluator.py:1506` to use `sub_type` instead of `card_sub_type`

### Priority 2: Fix Piloted Starship Deployment - FIXED
- [x] Added `reinforceable_space` logic to `deploy_planner.py` (similar to existing `reinforceable_ground`)
- [x] Space locations we control but haven't fortified (< 10 power) are now valid deployment targets
- [x] Updated HOLD_BACK condition to check `space_targets` instead of just `uncontested_space`
- [x] All 190 deploy planner tests pass

### Priority 3: Review "No Good Options" Logic
- [ ] When planner says "no good options" but has force + cards, log what's being rejected and why
- [ ] Consider deploying vehicles to exterior locations even without character support
- [ ] Consider deploying Effects/Interrupts that don't need characters

### Priority 4: Leftover Force Optimization
- [ ] If plan completes with >3 force remaining, try to find additional deployments
- [ ] Consider deploying weapons to armed characters
- [ ] Consider deploying Effects that could help

---

## Files Analyzed

| File | Opponent | Result | Key Issues |
|------|----------|--------|------------|
| rando_20251206_071930_vs_MGB_loss.log | MGB | Loss | card_sub_type error |
| rando_20251206_052347_vs_oboe-wan_loss.log | oboe-wan | Loss | Loop detection (handled) |
| rando_20251206_081839_vs_oboe-wan_loss.log | oboe-wan | Loss | Piloted starships not deployed |
| rando_20251206_062045_vs_auzi_loss.log | auzi | Loss | "No good options" with resources |
| rando_20251206_014451_vs_Schwill_loss.log | Schwill | Loss | High force held back |
| rando_20251206_180041_vs_jlock_loss.log | jlock | Loss | Safety correction |
| rando_20251206_141520_vs_lepstein01_win.log | lepstein01 | Win | HTTP timeouts (recovered) |

---

*Generated: 2025-12-06*
