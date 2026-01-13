# Deploy Plan Execution Failures - Analysis

This document captures cases where the bot made a deployment plan but couldn't execute it correctly because GEMP didn't offer the planned locations as valid targets.

## Summary Statistics

| Category | Total Count | Games Affected | Status |
|----------|-------------|----------------|--------|
| "aboard:" re-pilot failures | ~67 | 8 games | **FIXED** (zone check added) |
| Deployment order not enforced | - | - | **FIXED** (order enforcement) |
| Pilot-ship card_id tracking | - | - | **FIXED** (PCIP event update) |
| Location card_id tracking | - | - | **FIXED** (pending location update) |
| Deploy restriction enforcement | ~14 | 4 games | **FIXED** (restriction check in STEP 4) |
| System location failures | ~16 | 5 games | **FIXED** (is_site check) |
| Total plan failures | ~97 | 15 games | |
| Backup targets used successfully | 32 | - | Working |

---

## Issue 1: False RE-PILOT Plans for HAND Cards - **FIXED**

### The Problem

The `_find_unpiloted_ships_in_play()` function was incorrectly finding ships **in hand** as "unpiloted ships in play". This happened because it didn't check the card's `zone` field.

### Example (from `rando_20251206_084226_vs_oboe-wan_loss.log`)

Red 6 was **IN HAND**:
```
[0] •Red 6 (Starship) - Power: 3, Deploy: 2   ← IN HAND!
```

But the function incorrectly reported:
```
🚀 Found UNPILOTED ship in play: •Red 6 (#190) at unknown (idx=-1)
```

The `idx=-1` and `at unknown` were clues that the card wasn't actually at a location - it was in hand!

This caused the planner to create invalid RE-PILOT plans for ships that weren't even deployed yet:
```
❌ WRONG: Plan: •Theron Nett -> aboard:190:•Red 6 (RE-PILOT)
✅ RIGHT: Plan: •Red 6, •Theron Nett -> •Yavin 4 (ship+pilot combo)
```

### The Fix

Added zone check in `_find_unpiloted_ships_in_play()`:
```python
# CRITICAL: Only consider cards actually AT a location (not in hand, lost pile, etc.)
if card.zone != "AT_LOCATION":
    continue
```

Now RE-PILOT plans are only generated for ships that are ACTUALLY on the board.

### Correct Deployment Order for Unpiloted Ship + Pilot

When deploying an unpiloted ship with a pilot from hand:
1. Deploy the **SHIP** first to the location
2. GEMP asks "Do you want to simultaneously deploy a pilot?"
3. Bot says YES and selects the pilot
4. Ship deploys piloted

The bot should NOT try to deploy the pilot first - the ship must be on the board first!

---

## Issue 1.5: Deployment Order Enforcement - **FIXED**

### The Problem

When deploying an unpiloted ship with a pilot, the correct order is:
1. Deploy the SHIP first
2. GEMP asks "Do you want to simultaneously deploy a pilot?"
3. Bot says YES and selects pilot

But the bot wasn't enforcing deployment order, so it might try to deploy a character before a ship.

### The Fix

Added deployment order enforcement in `deploy_planner.py`:
- `get_pending_card_types()` - Checks what card types are still pending
- `should_deploy_card_now()` - Checks if a card should deploy based on type ordering

Deployment order:
1. **Locations** deploy first (opens new deployment options)
2. **Ships/Vehicles** deploy second
3. **Characters** deploy last

### Fallback Logic

To prevent the bot from hanging, if higher-priority cards are pending but NOT available (GEMP not offering them), lower-priority cards can deploy:
```python
# If locations are pending but not in the GEMP offer, allow ship/character deploy
if pending['locations']:
    if _pending_type_available('locations'):
        return (False, "Wait: locations must deploy first")
    else:
        # Fallback: locations not available, allow this card
        return (True, "Locations pending but not available")
```

---

## Issue 1.6: Pilot-Ship Card ID Tracking - **FIXED**

### The Problem

When deploying a pilot to board a ship that's being deployed from hand, the planner didn't know the ship's `card_id` at plan time (GEMP assigns card IDs when cards deploy). This caused pilots to target the system location instead of the specific ship.

Example:
```
Plan: •Tey How -> •Tatooine (system)
But GEMP expects: •Tey How -> aboard ship #331 (Blockade Support Ship)
```

### The Fix

1. **Added ship tracking fields to `DeploymentInstruction`**:
   - `aboard_ship_name` - Ship name for logging
   - `aboard_ship_blueprint_id` - Ship type identifier
   - `aboard_ship_card_id` - Assigned when ship deploys (None initially)

2. **PCIP event handling in `event_processor.py`**:
   - When a ship deploys and gets assigned a card_id, find all pilot instructions waiting for that ship
   - Update `aboard_ship_card_id` on matching instructions
   - Log the update: `"📋 Deploy plan updated: Blockade Support Ship assigned card_id=331"`

3. **Pilot-ship matching in `deploy_evaluator.py`**:
   - When evaluating pilot deployment, check if instruction has `aboard_ship_card_id`
   - If yes, give +200 bonus to matching ship target
   - If ship not in GEMP's offer, fall back to system location as backup

### Code Changes

**`engine/deploy_planner.py`** - DeploymentInstruction fields:
```python
@dataclass
class DeploymentInstruction:
    # ... existing fields ...
    aboard_ship_name: Optional[str] = None
    aboard_ship_blueprint_id: Optional[str] = None
    aboard_ship_card_id: Optional[str] = None  # Set when ship deploys
```

**`engine/event_processor.py`** - PCIP handler:
```python
if zone == "AT_LOCATION" and owner == self.board_state.my_player_name:
    if self.board_state.current_deploy_plan:
        self.board_state.current_deploy_plan.update_deployed_card_id(
            blueprint_id, card_id, card_title
        )
```

---

## Issue 1.7: Location Card ID Tracking - **FIXED**

### The Problem

When deploying a character to a location that's being deployed from hand in the same phase, the planner doesn't know the location's `card_id` at plan time (GEMP assigns card IDs when cards deploy).

### The Fix

Added `__post_init__` to DeploymentInstruction that auto-detects "planned_" prefixed card IDs:

```python
def __post_init__(self):
    if self.target_location_id and self.target_location_id.startswith("planned_"):
        self.target_location_blueprint_id = self.target_location_id[8:]  # Skip "planned_"
        self.target_location_pending = True
        self.target_location_id = None  # Clear the placeholder ID
```

When the location deploys via PCIP event, `update_deployed_card_id()` updates matching instructions.

---

## Issue 1.8: Deploy Restriction Enforcement - **FIXED**

### The Problem

Characters with "Deploys only on Tatooine" restrictions (like Jawas, Kalit) were being included in deployment plans for other systems (like Coruscant: Xizor's Palace).

The restriction checking code existed in `_generate_all_ground_plans` but was missing from STEP 4's contested location handling.

### The Fix

Added restriction filtering in STEP 4 before calling `_find_optimal_combination`:

```python
for loc in contested:
    # Filter characters by deploy restrictions for THIS location
    location_chars = []
    for char in available_chars:
        restrictions = char.get('deploy_restriction_systems', [])
        if restrictions:
            # Check if location matches any allowed system
            loc_clean = loc.name.lstrip('•').strip()
            can_deploy = any(
                loc_clean.lower().startswith(sys.lower()) or
                (loc_clean.split(':')[0].strip().lower() == sys.lower() if ':' in loc_clean else False)
                for sys in restrictions
            )
            if not can_deploy:
                continue  # Skip this character for this location
        location_chars.append(char)
```

Now Kalit can only be planned for Tatooine locations, not Coruscant or other systems.

---

## Issue 2: System Location Deployment (16 occurrences) - **FIXED**

### The Problem

Characters **cannot deploy directly to system locations** (like •Tatooine, •Yavin 4). They must deploy to **sites** (like •Tatooine: Cantina) or **docking bays**.

But the planner was targeting system locations:

```
Plan: •Tey How -> •Tatooine (id=186)
Backup: •Naboo (V)

GEMP offered: ['181', '184', '185', '211', '257', '331']
(These are sites like •Tatooine: Cantina, not the system)

Result: Neither primary nor backup available
```

### Example (from `rando_20251206_040047_vs_Gilporshin_loss.log`)

```
03:58:51 - Plan says: •Tey How -> •Tatooine
   Backup: •Naboo (V)
   GEMP offered card_ids: ['181', '184', '185', '211', '257', '331']

   DEBUG: board_state locations:
   - ('186', '•Tatooine')  ← SYSTEM (not offered for character deploy)
   - ('184', '•Tatooine: Cantina') ← SITE (offered)
   - ('185', '•Tatooine: Docking Bay 94') ← SITE (offered)

   WARNING: Primary (•Tatooine) not in offered - holding back
```

### Root Cause

The deploy planner was including system locations in its target list for characters, but GEMP correctly only offers sites/docking bays for character deployment.

### The Fix

Added `loc.is_site` check to all character ground target filtering in `deploy_planner.py`:
- Main `char_ground_targets` list (line ~3730)
- Newly deployed locations loop (line ~3762)
- `crushable_ground` list (line ~3784)
- `reinforceable_ground` list (line ~3807)
- Vehicle `ground_targets` list (line ~4760)

Also added logging to show when systems are excluded:
```python
excluded_systems = [loc.name for loc in locations if loc.is_ground and not loc.is_site]
if excluded_systems:
    logger.info(f"   ⏭️ System locations (chars need sites): {excluded_systems}")
```

---

## Issue 3: Character Deployment Restrictions (14 occurrences)

### The Problem

Some characters have "Deploys only on X" restrictions that the planner doesn't fully respect:

**Example 1: Jawas can only deploy to •Tatooine: Jawa Camp**

```
Plan: Jawa -> •Tatooine: Hutt Trade Route (Desert)
Backup: •Xizor's Palace: Uplink Station

GEMP offered: ['169']  (only •Tatooine: Jawa Camp)

Result: Neither Hutt Trade Route nor Xizor's Palace offered
```

**Example 2: •Kalit (a Jawa) planned for Xizor's Palace**

```
Plan: •Kalit -> •Xizor's Palace: Uplink Station
Backup: •Coruscant: Xizor's Palace

GEMP offered: ['137', '138', '169']  (Tatooine locations only)

Result: Plan fails - Kalit can only deploy to Tatooine
```

### Affected Locations

| Location | Failures | Issue |
|----------|----------|-------|
| •Xizor's Palace: Uplink Station | 10 | Character restrictions |
| •Tatooine: Hutt Trade Route | 2 | Jawa restrictions |
| •First Light: Reception Area | 4 | Unknown restriction |
| •Dagobah: Training Area | 2 | May need specific characters |
| •Cloud City: West Gallery | 2 | Unknown restriction |

### Fix Required

The deploy planner needs to check character deployment restrictions more thoroughly:
1. Parse "Deploys only on X" from character gametext
2. Cross-reference with available locations
3. Only plan deployments to locations the character can actually deploy to

---

## Issue 4: Backup Target System Working (32 successes)

The backup target system IS working correctly when the primary is unavailable but backup is valid:

```
04:32:58 - Primary •Dagobah: Training Area unavailable
   Card 186 is the BACKUP target (+150)
   Brain chose: Deploy to •First Light: Bar
```

This shows the backup system works - the issue is when BOTH primary and backup are invalid.

---

## Recommendations

### Priority 1: Fix RE-PILOT targeting

The most impactful fix (~67 failures). Options:
1. Store the ship's location in the plan, not the ship card ID
2. When executing "aboard:" plans, look up where the ship is located
3. When GEMP asks for location, map the ship card ID to its location

### Priority 2: Filter system locations for characters

When building `char_ground_targets`:
```python
# Current (wrong):
char_ground_targets = [loc for loc in locations if loc.is_ground...]

# Fixed:
char_ground_targets = [loc for loc in locations
                       if loc.is_ground
                       and ':' in loc.name  # Must be a site, not a system
                       ...]
```

### Priority 3: Respect character deploy restrictions

The `deploy_restriction_systems` property exists but may not be fully used:
- Ensure characters with Tatooine-only restrictions don't plan for Coruscant
- Parse "Deploys only on X" more thoroughly

### Priority 4: Improve backup assignment for aboard targets

RE-PILOT plans should have the ship's **location** as the backup target, not another ship.

---

## Files to Modify

1. **`engine/deploy_planner.py`**
   - `_find_unpiloted_ships_in_play()` - Should also store ship's location
   - RE-PILOT plan generation - Use ship's location as target
   - System location filtering for characters

2. **`engine/evaluators/deploy_evaluator.py`**
   - Handle "aboard:" targets by looking up ship location
   - Better debugging when targets don't match

3. **`engine/card_loader.py`**
   - Ensure deploy restrictions are fully parsed

---

## Test Cases to Add

```python
def test_repilot_uses_ship_location_not_ship_id():
    """When re-piloting, target should be ship's location"""
    pass

def test_characters_cannot_deploy_to_systems():
    """Character deploy targets exclude system locations"""
    pass

def test_jawa_only_deploys_to_jawa_camp():
    """Characters with restrictions respect them"""
    pass
```

---

*Generated: 2025-12-06*
