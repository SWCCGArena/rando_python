# SWCCG Rules Reference for Bot Logic

Condensed from the Advanced Rulebook 2023 Edition. Focus on rules that affect bot decision-making.

---

## 1. Turn Structure

A turn consists of six consecutive phases executed by one player:

```
Mandatory Start of Turn Events
Optional Start of Turn Events
    ↓
1. Activate Phase   - Activate Force from Reserve Deck to Force Pile
2. Control Phase    - Initiate Force drains at controlled locations
3. Deploy Phase     - Deploy cards from hand
4. Battle Phase     - Initiate battles
5. Move Phase       - Move characters/vehicles/starships
6. Draw Phase       - Draw cards into hand
    ↓
Mandatory End of Turn Events
Optional End of Turn Events
```

**Start of Turn Timing**:
1. Mandatory start of turn actions resolve first (as automatic actions)
2. Optional start of turn actions may then be initiated (active player gets first chance)
3. Only start of turn actions (or valid responses) are legal at this time
4. Once all resolved and both players pass, Activate Phase begins

**Important**: Anything that "happens during a turn" never happens before the first turn begins or after the last turn ends. Top-level actions (like Used Interrupts) cannot be initiated before first turn or after last turn.

**Dark Side goes first** by default, unless cards specify otherwise.

---

## 2. Force Generation & Activation

**Force Generation** = the amount of Force you may activate during your Activate Phase.

**Calculation**:
1. Count Force icons on YOUR side of each location on table
2. Add 1 for personal Force (you always generate 1)
3. Add 1 for each of your Jedi Masters (Light) or Dark Jedi Masters (Dark)
4. Apply any "total Force generation" modifiers

**Critical**: This total is LOCKED once the count is completed. Only "beginning of turn" actions can affect it. If opponent cancels a Presence Of The Force mid-phase, your total remains unchanged for that turn.

**Activating Force**:
- Each activation is a separate action (move one card at a time from Reserve to Force Pile)
- During Activate Phase, presence is NOT required to generate at locations with your icons
- You are NOT required to activate all Force you're entitled to
- When cards/rules INSTRUCT you to activate Force (e.g., Blue Milk), you MUST activate all of it

**Force Icons vs Jedi Master Icons**:
- Force icons (lightsabers on locations) count toward Force generation AND Force drains
- Jedi/Dark Jedi Master icons are NOT Force icons - they add to total generation only
- Master icons cannot be canceled by effects that cancel Force icons (like Sleen)

**Force Generation vs Force Drains**:
- Cards modifying "Force drain" affect drains only
- Cards modifying "Force generation" affect generation only
- Cards modifying "Force icons" affect BOTH

**Strategic Note**: When Reserve Deck < 10 cards, consider NOT activating all Force to keep cards available for destiny draws.

---

## 3. Life Force & Win Condition

**You lose when Reserve Deck + Force Pile + Used Pile are ALL empty.**

Life Force consists of:
- Reserve Deck (draw from here)
- Force Pile (available to spend)
- Used Pile (spent Force, recirculates)
- Unresolved destiny draws
- Sabacc hand (if applicable)

Cards in hand, on table, or Lost Pile are NOT Life Force.

**Re-circulating**: At end of EACH player's turn, Used Pile goes under Reserve Deck. This is mandatory.

---

## 4. Force Drains & Control Phase

**Force Drain** = causing opponent to lose Force based on their icons at locations you control.

**How to Force Drain**:
- During YOUR Control Phase, at each location you CONTROL
- Once per location per turn
- Force draining costs 0 Force (but this can be modified)
- Opponent loses Force = number of their icons at that location (plus modifiers)

**Participation Rules** (Critical for bot):
- ALL your characters, vehicles, starships, weapons, and devices at the drain location participate
- Each card may only participate in ONE Force drain per turn
- If a card already participated in a drain this turn, you CANNOT drain at its current location even if you haven't drained there yet

**Example**: Luke drains at Mos Eisley, then moves to Cantina. You cannot drain at Cantina because Luke already participated this turn (even though BoShek there hasn't).

**"Instead of Force Draining"**:
- Cannot use unless you CAN legally Force drain at that location
- You must control the location
- It IS legal to drain for 0 (no icons), so "instead of" works even with 0 icons
- Using this text means no Force drain at that location this turn

**"May Force Drain" Droids** (EV-9D9, Probe Droid):
- Can control location ONLY for initiating/enhancing Force drains
- Cannot control for other purposes (unless undercover)

**Force Drain Modifiers vs Limiters**:
- Modifiers: add/subtract/multiply/divide the drain amount
- Limiters (like Resistance, We're Doomed): cap maximum loss but don't modify the drain
- "May not be modified by opponent" prevents modifications but NOT resets or limiters
- "May not be canceled by opponent" prevents direct cancellation but NOT reacts that provide presence

**Timing of Responses**:
- "Whenever you/opponent Force drain(s)" or "if just initiated" → during Response step, before resolution
- "If you/opponent just Force drained" → after resolution, as action comes off stack

---

## 5. Presence, Present & Location Control

### Presence (Spiritual/Mystical)
**Having presence at a location** = (1) total ability ≥ 1 at that location, OR (2) having a Presence icon there.

- Cards inside starships/enclosed vehicles still contribute ability toward presence at that location
- No card creates presence at more than one location
- Inactive cards NEVER provide presence

### Present (Physical)
**Present** = physically at a specific place. Four places a card can be present:
1. At a site location
2. At a sector location
3. At a system location
4. On an enclosed vehicle/starship (bridge, cockpit, cargo bay) or in a prison

**Key distinction**: A character aboard an enclosed ship is AT the system but NOT PRESENT at the system.

**Present vs Present With**:
- "Present" = card present at a location (even if referencing card isn't present itself)
- "Present with" = two+ cards present TOGETHER (same enclosed vehicle or same location AND both present there)
- "Where present" = that card must be present at its location for text to apply

### Location Control Definitions

| Term | Definition |
|------|------------|
| **Control** | You have presence AND opponent does not |
| **Occupy** | You have presence (regardless of opponent) |
| **Unoccupied** | NO player has presence (but undercover spy prevents unoccupied) |

### Prepositions (at, on, aboard, with)

**"On" a system**: Present at any related site/sector, OR inside ship/vehicle present at related site/sector

**"At" a system**: On that planet, OR orbiting that system, OR inside ship orbiting that system

**"Aboard/On" a ship/vehicle**: At any related site, OR at bridge/cockpit/cargo bay

**"With"**: Both at same location. A card CAN be "at same location" as itself but CANNOT be "with" itself.

### Alone/Lone
- **Character alone**: Active, and you have no other cards with ability or characters at that location
- **Starship/vehicle alone**: Only active characters/vehicles/starships you have at location are aboard that ship/vehicle
- Combo cards (Artoo & Threepio) and multi-permanent-pilot ships are NOT alone

---

## 6. Losing Force

When you lose Force, choose cards from:
- Hand
- Top of Reserve Deck
- Force Pile
- Used Pile
- Unresolved destiny draws

"Lose X Force from [location]" = lose from there first, remainder from anywhere.

**Important**: "Losing Force" (generic) is different from losing a specific card. Cards like "Honor Of The Jedi" or "It Could Be Worse" reduce generic Force loss, NOT specific card losses (like Monnok forcing you to lose cards from hand).

---

## 7. Action System

### Three Steps of Every Action
1. **Initiation**: Meet conditions, choose targets, pay costs
2. **Responses**: Opponent responds first, then alternate
3. **Result**: Action completes

Once initiated, an action continues even if conditions change mid-action.

### Four Types of Actions

| Type | Top-Level? | Optional? | Examples |
|------|-----------|-----------|----------|
| **Optional** | Yes | Yes | Deploying, Force draining, initiating battle |
| **Mandatory** | Yes | No | Drawing asteroid destiny, creature attacks |
| **Just Actions** | No (response) | Yes | Playing Sense on Alter, reacting to Force drain |
| **Automatic** | No (response) | No | Completing Utinni Effect, Scum & Villainy retrieval |

### The Stack
- Current action is on top
- Can only respond to current action
- Opponent gets first response to your actions
- Two consecutive passes = action resolves
- Automatic actions happen before just actions

### "At Any Time" Actions
These can be used during any phase BUT are still top-level actions. They CANNOT respond to unrelated actions.

---

## 8. Targeting Rules

### Implied Target Rule
Cannot initiate action unless ALL required targets exist:
- Cannot cancel something that doesn't exist
- Cannot modify a value that doesn't exist (droids are exception - treat ability as 0)
- Cannot draw/search from empty deck or pile (except destiny draws still attempted)
- Cannot look at empty hand

**Exceptions**:
1. Optional targets (indicated by "if any")
2. Things gained by the action
3. Unknown targets (cards in decks you can't see)
4. "All cards" targeting (ignores immune members)

### Empty Deck/Pile Rules
When empty, you MAY NOT:
- Take, deploy, exchange, steal cards from it
- Search, peek, examine, reveal cards in it
- Draw cards from it (except destiny - that just fails)
- Shuffle it

You MAY:
- Use "0 Force" or "entire Force Pile" even if empty
- Retrieve Force (even with empty Lost Pile)
- Place cards under empty deck/pile

---

## 9. Card States

Every card is in exactly ONE state:

### Active
Card doing what it says. Most cards on table.

### Inactive
On table but not functioning:
- Captured starships
- Missing characters
- Stolen weapons (carried but not usable)
- Suspended Effects
- Some stacked cards

Inactive cards:
- Still count toward uniqueness
- Still affected by "all cards" targeting
- Never provide presence
- Count toward capacity limits if aboard

### Supporting
Not on table but affecting game:
- Cards stacked on It Is The Future You See
- Combat cards under characters
- Face-down cards (always supporting)
- Cards on grabbers

### Unit of Force
Cards in hand, Life Force, or Lost Pile. Not on table.

### Out of Play
Removed from game. Unique characters/vehicles/starships out of play prevent that player from deploying other versions.

---

## 10. Values & Modifiers

### Modifiers vs Resets

**Modifiers**: Add, subtract, multiply, divide from base value.
- Multiple copies of same card are NOT cumulative unless stated
- Applied in order they occur

**Reset**: Change to specific new value (e.g., "forfeit = 0").
- Value becomes unmodifiable until card leaves play
- Competing resets: lower value wins

### Important Distinctions
- "Limited" = restricted below normal entitlement (canceling modifiers is NOT limiting)
- "Free" = ignores base cost but NOT separate costs
- "Up to X" means "1 to X" (zero not valid unless X is 0)
- Undefined values = zero (but "no hyperdrive" cannot be modified)
- Negative values floor at zero

### Separate Costs
Additional costs added by other cards (e.g., "must first use X Force"). These are paid BEFORE base cost and are NOT affected by "free" or cost modifiers.

---

## 11. Drawing Destiny

### Steps
1. Draw top card of Reserve Deck (reveal it)
2. Apply automatic draw modifiers
3. Automatic actions (Krayt Dragon Bones, etc.)
4. Just actions (opponent first)
5. Complete draw (floor at 0, place in Used)
6. Repeat if more draws required
7. Total all destiny values + modifiers
8. Complete (failed if no total)

### Key Points
- Destiny draw is mandatory except battle destiny and "may draw"
- Empty Reserve Deck = cannot draw (destiny fails)
- Canceled destiny draw still counts against limits (unless redrawn)
- Destiny VALUE and destiny CARD are separate entities

### Substituted Destinies
Using another value instead of drawing (e.g., starfighter maneuver):
- Cannot be modified, canceled, or reset
- Does NOT count against destiny limits
- Still considered "just drawn" for triggering effects
- Physical card providing value is NOT "just drawn"

### Failed Destiny Draw
If you cannot complete ANY destiny draw:
- You fail the action
- Result goes in opponent's favor
- Opponent does NOT choose result - it's automatic

---

## 12. Canceling & Suspending

### Canceling Actions
- Prevents action from having result
- Costs already paid remain paid
- Once-per-turn actions cannot be re-initiated if canceled
- Canceled cards go to Lost Pile (except destiny draws go to Used)

### Canceling Game Text
- Clears game text for duration
- Statistics, title, icons unaffected
- If game text defines a stat, that stat becomes undefined
- When text returns, illegal situations cause card to be lost

### Characters/Vehicles/Starships Cannot Be Canceled

---

## 13. Location Restrictions (Never Rules)

Cards restricted to certain locations by rule:
- **Characters**: Sites only (unless aboard ship/vehicle). Cannot deploy to Death Star: Trench.
- **Vehicles**: Exterior sites only (Cloud Cars/Patrol Craft can be at cloud sectors)
- **Capital Starships**: Systems and asteroid sectors only
- **Starfighters**: Exterior sites, systems, and sectors
- **Nothing deploys to holosites**

"Never" restrictions cannot be circumvented and apply even if game text canceled.

---

## 14. The One Rule

When card allows action during specific timeframe with singular language ("one", "a", "an"):
- May only perform once per timeframe
- Multiple copies = once per copy

**Does NOT apply** when:
- No timeframe given
- Action not singular ("any" instead of "one")

**"Once Per Game"**: Cannot be initiated again even if canceled, by either player with any card of same title.

---

## 15. Just Deployed vs Just Played

- **Just Deployed**: After deployment resolves, responses can occur
- **Just Played**: After initiation but BEFORE result (responses during step 2)

---

## 16. Just Lost

A "just lost" card:
- Is already in Lost Pile
- Can be relocated (e.g., Bacta Tank)
- Cards deployed on it are already lost separately
- Only the just-lost card is relocated (not attached cards)
- Removing from Lost Pile = no longer "just lost"

---

## 17. Before Actions

Some actions happen "before" another action:
- Occurs BEFORE the other action is even initiated
- May require "backing up" play
- Cannot back up from your own actions

---

## 18. Duration of Effects

If no duration specified:
- During battle: lasts until end of battle or card leaves play
- Outside battle: lasts until card leaves play
- Weapon effects: always last until card leaves play

---

## 19. Conditions vs Costs

**Conditions**: Requirements to initiate (e.g., "if you occupy...")
**Costs**: What you pay (Force, placing cards out of play)

"Free" and "ignore conditions" are independent - one doesn't affect the other.

---

## 20. Ability Requirements

"All your ability is provided by X" (e.g., Scum And Villainy requiring aliens):
- Must have at least one card with ability meeting condition
- Must have NO cards with ability violating condition
- Only checks active cards
- Modifying total ability doesn't affect this check

---

## 21. Deploy Phase & Deployment

**Deploy Phase** = Third phase of each turn, in which you deploy cards from hand to table.

### Deployment Requirements

**Characters, Vehicles, Starships**: Cannot deploy to a location unless you have:
- Presence there, OR
- At least one Force icon on your side of that location
- **Exception**: Spies can deploy without presence/icons (then go undercover)

**Weapons, Devices, Effects, Creatures**: May deploy wherever appropriate, with or without presence or Force icons.

### Deployment Restrictions (Three Types)

**Location Deployment Restrictions**:
- Presence/Force icon requirement (characters, vehicles, starships)
- Dagobah: Characters, vehicles, starships, Utinni Effects cannot deploy
- Ahch-To: Same restrictions as Dagobah
- Shielded Sites (Hoth Energy Shield): Dark Side characters/vehicles/starships cannot deploy
- Game text restrictions on specific cards

**Rules Deployment Restrictions**:
- Capital starships → systems and asteroid sectors only
- Vehicles → exterior sites only
- Characters → sites only (unless aboard ship/vehicle)
- TIEs → docking bays only when landing
- Nothing deploys to holosites

**Objective Deployment Restrictions**:
- Cards your objective says you cannot deploy
- Cards that require a specific objective to be on table

**Important**: A card that "ignores location deployment restrictions" can deploy to Dagobah, shielded sites, or without presence/icons. But it CANNOT ignore rules restrictions (e.g., capital starship still can't go to a site) or objective restrictions.

### Deployment Costs

- Paying deploy cost is part of initiation
- If deployment is canceled, Force is still used
- "Free" ignores base cost but NOT separate costs
- "Deploys -X" is a modifier, not a restriction

### Simultaneous Deployment

When two cards deploy simultaneously (e.g., pilot + starship from Squadron Assignments):
- One must deploy ON the other
- If this cannot occur (capacity restrictions), deployment fails
- Both cards must be valid for deployment

### Persona Rules (Critical for Bot)

Different versions of a unique character/device/starship/vehicle/weapon are the same **persona**.

**Key Rules**:
- Cannot have more than one version of a unique persona on table at a time
- BOTH players may each have the same unique persona on table simultaneously
- If you control two of same persona (rare), must immediately place one in Lost Pile

**Persona Exceptions**:
- Captive on table → Light cannot deploy that persona
- Stolen starship/vehicle/weapon/device → other player cannot deploy that persona

**Targeting Personas**: Any card targeting a persona may target any card containing that persona (e.g., "Boba Fett" can target Boba Fett In Slave I).

---

## 22. Battle Phase & Battles

**Battle Phase** = Fourth phase of each turn, in which you may initiate battles.

### Battle Requirements

- **Condition**: Both you AND opponent have presence at the location
- **Cost**: 1 Force (can be modified or made free)
- **Limit**: One battle per location per turn
- **Card Limit**: Each character/vehicle/starship may battle only once per turn

**Critical**: Cards are considered to have participated from the moment battle is initiated. Even if they move away or battle is canceled, they cannot battle again that turn.

### Steps of a Battle

```
1. INITIATE THE BATTLE (pay 1 Force, both players need presence)
   ↓
   Automatic actions (e.g., Scum And Villainy retrieval)
   Optional responses / Reacts (opponent first)
   ↓
2. WEAPONS SEGMENT
   - Fire weapons (each weapon once per battle)
   - Hit cards turned sideways (still participate)
   - Players alternate actions, initiator first
   - Two passes → proceed to Power Segment
   ↓
3. POWER SEGMENT
   a. Draw Power Destinies (initiator first)
   b. Draw Battle Destinies (initiator first)
   c. Draw Attrition Destinies (initiator first)
   d. Optional Power Segment Actions
   e. Total Power calculated
   f. Attrition calculated
   g. Winner/Loser determined
   ↓
4. DAMAGE SEGMENT
   - Forfeit hit cards
   - Satisfy attrition (by forfeiting)
   - Satisfy battle damage (forfeit or lose Force)
   - Players alternate, initiator first
   ↓
5. END OF BATTLE
```

### Battle Destiny (Critical for Bot)

**Basic Rule**: You may draw 1 battle destiny if you have **4 or more ability** participating in the battle.

**Ability That Counts**:
- Cards that are PRESENT during the battle
- Characters/permanent pilots that are PILOTING or DRIVING

**"Draws X Battle Destiny If Unable To Otherwise"**:
- Only applies if you have fewer than X draws by other means
- OVERRIDES conditions like "Ability of 6 required to draw battle destiny"
- Still optional (drawing battle destiny is always optional)

**Automatic vs Optional Modifiers**:
- Automatic modifiers (no "may"): checked when drawing begins
- Optional modifiers (Interrupts, "may add"): played during weapons segment, locked in

### Power Calculation

```
Total Power = Base Power of present cards
            + Power destinies drawn
            + Total battle destiny
            + All modifiers (locations, game text, etc.)
```

**Winner**: Higher total power wins. Ties = no winner/loser.

### Attrition

**Attrition** = Your total battle destiny becomes attrition against your opponent.

**Critical**: If you don't successfully complete ANY battle destiny draws, there is NO attrition against opponent (cannot be modified). This is different from attrition of 0.

**Satisfying Attrition**: Must forfeit cards whose forfeit values total at least the attrition amount.
- Hit cards count toward satisfying attrition when forfeited
- If all remaining cards are immune to attrition, ignore remainder
- Cannot compare immunity to remaining attrition - only to TOTAL attrition at start of damage segment

### Battle Damage

**Battle Damage** = Difference between winner's and loser's total power (loser only).

**Satisfying Battle Damage**:
- Forfeit cards (each counts up to its forfeit value)
- Lose Force from hand or Life Force (each card = 1 unit)
- Every forfeited card (including hits) counts toward BOTH attrition AND battle damage

### Forfeit

- Only during damage segment
- Must have forfeit value
- Must be participating in battle
- Must be either HIT or satisfying attrition/battle damage
- Forfeiting a card loses everything deployed on it

### Immune to Attrition

Cards with "immune to attrition < X" need not be forfeited if total attrition is less than X.

**Key Points**:
- Immunity is checked at START of damage segment against TOTAL attrition
- Cannot recheck later after satisfying some attrition
- Enclosed ship/vehicle immunity protects cards aboard bridge/cockpit/cargo bay
- Multiple immunity sources: highest value wins
- Immune cards may still be VOLUNTARILY forfeited

**Gaining vs Losing Immunity**: When in conflict, losing immunity overrides gaining immunity.

### Battle Ends Prematurely

If all presence is removed from either side BEFORE the damage segment:
- Battle ends immediately (no damage segment)
- Hit cards are still lost
- This happens instantly, not as an action

---

## 23. Movement

**Move Phase** = Fifth phase of each turn, in which you move characters, vehicles, starships.

### Three Types of Movement

| Type | Description | Limit |
|------|-------------|-------|
| **Regular** | Normal moves using landspeed, hyperspeed, etc. | One per card per turn |
| **Unlimited** | Embarking, disembarking, between capacity slots | No limit |
| **React** | Moving/deploying as response to battle/drain | Special rules |

### Regular Moves

All occur during your move phase unless card says otherwise.

| Move Type | Cost | Notes |
|-----------|------|-------|
| Landspeed | 1 Force | Characters: landspeed 1. Vehicles: as printed. Cannot reverse direction mid-move. |
| Hyperspeed | 1 Force | Requires astromech/nav computer. Cannot end in deep space (except Death Stars). |
| Landing/Taking Off | 1 Force | Free at docking bays. TIEs require docking bays. See Cloud Sectors below. |
| Sector Movement | 1 Force | Starfighters can move 2 sectors per move. |
| Shuttling | 1 Force | +1 for each cloud sector. Free with shuttle vehicle. |
| Docking Bay Transit | See card | Move any/all characters + vehicles as group. Characters aboard moving vehicle are NOT considered moving. |
| Location Text | See card | If no cost specified, 1 Force. Each card using text is a separate action. |
| To/From Death Star | 1 Force | When Death Star orbits same system (no hyperspeed needed). |
| Starship/Vehicle Sites | Free (you) | Move between your ship/vehicle site and ship card. Opponent may use (see cost on site). |

**Hyperspeed Details**:
- Moving 0 parsecs STILL requires hyperspeed > 0 AND astromech/nav computer
- Exception: Moving between Death Star and system it orbits doesn't require hyperspeed

**Cloud Sector Rules**:
- If cloud sectors are deployed at a system, starships cannot land/take off directly between system and exterior sites
- Must "fly through" cloud sectors first
- Can only land from (or take off to) the lowest-altitude cloud sector

**Landspeed Range**:
- Some locations require extra landspeed to move to/from (e.g., Tatooine: Desert)
- This reduces effective range of cards moving through
- Cannot reverse direction mid-move; once stopped, regular move is complete

### Unlimited Moves (Free)

- **Embarking/Disembarking**: Between your vehicle/landed starship and site it's present at. Cannot embark/disembark as PART of another movement (e.g., Nabrun Leids doesn't let you disembark vehicle).
- **Moving Between Capacity Slots**: Pilot ↔ passenger during deploy/move phase. Free.
- **Moving Between Docked Starships**: During move phase, dock two starships at same system/sector for 1 Force. Transfer any characters/vehicles/starships (capacity permitting), then undock. Requires at least one piloted and one with ship-docking capability (all capital starships have this by rule). This is movement for the STARSHIPS only, not the cards transferred.
- **Prisoner Transfers**: Dark Side only, during move phase. Escort delivering/taking captive to/from prison. Free unlimited move for BOTH escort and captive. Captive that cannot move cannot undergo prisoner transfer.
- **Moving to/from Starship/Vehicle Sites**: Free for you. Opponent may also use (follow cost on site card).

### Movement Restrictions

- Unpiloted vehicles/starships cannot move
- "Cannot move" prevents all regular, unlimited, and react moves
- Being "carried" is NOT movement (card that cannot move can still be carried)
- "Free" movement cannot be modified (if docking bay transit is free, nothing can add cost)
- When moving cards as a group with differing costs (e.g., Nabrun Leids), pay the highest cost

### "Move Away" Rules

When a card must "move away":
1. **Characters**: Use landspeed to adjacent site. Auto-disembark from vehicle/starship if successfully moving.
2. **Vehicles**: Use landspeed to adjacent site, or sector movement to adjacent sector.
3. **Starships**: Use hyperspeed to another system, or sector movement. Taking off/landing is NOT valid for "move away".

**Initiation**: Must verify at least one valid destination exists (don't check range or costs yet). Target cards to move. Pay costs on the action itself (not movement costs yet).

**Result**: Each card attempts to move. If one fails (out of range, cannot move), continue with next. Cards don't need to move to same destination. Movement costs paid during this step.

### "Moves Like a Character"

Cards with this text may:
- Use landspeed of 1
- Use docking bay transit
- Embark/disembark
- Move between docked starships
- Shuttle

But they:
- Do NOT take up passenger capacity
- CANNOT be affected by cards that work on characters (e.g., Nabrun Leids)

### "Moves Like a Starfighter"

Cards with this text (e.g., squadrons, Hound's Tooth, Mynock) obey all starfighter movement rules but:
- Are NOT starfighters for targeting
- Cannot embark on ships without appropriate capacity (Mynock can't board Home One - no creature capacity)

### Moving Through Locations

When using landspeed >1 or starfighter sector movement to pass through locations:

**Before moving**: Check full path is within range, and that nothing at final location prevents movement from initial location.

**Costs**: Pay based on initial → final location. Intermediate locations passed through are "free" (cannot be modified).

**At each intermediate location**:
- Card is considered AT that location as it passes through
- Can trigger automatic actions (Utinni Effects, etc.)
- Can satisfy conditions (control of location)
- If forced to stop (driver goes missing, game text blocks further movement), movement ends there
- Cannot initiate new actions until movement finishes

**Important**: "Passing through" ≠ "ends at". Cards that trigger when you END at a location don't trigger when passing through.

---

## 24. Attacks (Creatures)

Creatures do not participate in battles - they participate in **attacks**.

### Key Differences from Battles

| Battle | Attack |
|--------|--------|
| Costs 1 Force | Free |
| Requires presence on both sides | No presence required |
| Creates attrition and battle damage | No attrition or battle damage |
| Cards forfeit to satisfy damage | No forfeiting - defeated = eaten |
| May not attack your own cards | You may attack your own creatures |

### Types of Attacks

**Creature Attacking**: During your battle phase, your creature MUST attack (mandatory):
- Target selected randomly from potential targets
- Attacker Total = Ferocity + modifiers
- Defender Total = Power + destiny (if 4+ ability) + modifiers
- If Attacker > Defender, target is "eaten" (lost)

**Attacking a Creature**: During your battle phase, you MAY attack creatures (optional):
- All your characters/vehicles/starships present participate
- Attacker Total = Total power + destiny (if 4+ ability) + modifiers
- Defender Total = Ferocity + Defense Value + modifiers
- If Attacker > Defender, creature is lost

**Creatures Attacking Each Other**: Automatic when two creatures present together:
- Compare ferocity totals
- Lower ferocity creature is eaten (ties = both lost)

### Attack Structure

Attacks follow the same segments as battles:
1. Initiate (target selected randomly)
2. Weapons Segment (can fire at creature if weapon says "targets creatures")
3. Power Segment (compare totals)
4. Damage Segment (defeated creature lost, hit creatures lost)

### Creatures Cannot Be Targeted During Battle

Weapons cannot normally target creatures during a battle (only during attacks).

---

## 25. Draw Phase & End of Turn

**Draw Phase** = Sixth and final phase, draw cards from Force Pile into hand.

### Drawing Cards

- Each draw is a separate action (you may draw, do another action, then continue drawing)
- No hand size limit
- Drawing is optional unless required by a card (e.g., "draw up to 2 cards" from Reserve Deck when deploying Deneb Both)
- "Up to X" draws mean at least 1 (cannot choose 0 unless X is 0)
- May draw from other sources if card specifies (e.g., Ishi Tib draws from Reserve Deck)

**Strategic Note**: Leave Force in your Force Pile for reacting and playing Interrupts during opponent's turn. Don't draw everything.

### Re-circulation

After drawing phase completes:
- BOTH players place their Used Pile under their Reserve Deck (mandatory)
- This happens at end of EACH player's turn
- Cards spent during opponent's turn remain in Used Pile until YOUR turn ends

### End of Turn Sequence

```
1. Re-circulate: Both players place Used Pile under Reserve Deck (mandatory)
2. Mandatory end of turn actions resolve (as automatic actions)
   - "Until end of turn" effects cease
   - Maintenance costs must be paid
3. Optional end of turn actions (player whose turn ended gets first chance, then alternate)
4. Both pass → Turn ends, next player's turn begins IMMEDIATELY
```

**Critical Rules**:
- Only end of turn actions (or valid responses) are legal at this time
- For timing purposes, it is still the turn of the player whose turn just ended (affects uniqueness, "once per turn" cards)
- **No time between turns** - once both players pass, next turn starts immediately with Start of Turn

**Maintenance Costs**: Cards with maintenance icons must be paid during end of turn. This is MANDATORY and resolves as automatic action. Must be paid BEFORE any optional end of turn actions.

**"Until End of Turn"**: Effects cease as automatic actions. Player whose turn just ended chooses order. These must resolve before optional end of turn actions.

**Example**: If a card's game text was canceled "until end of turn", the text is restored during end of turn. If that card has a maintenance cost, it must still be paid (since we're still in end of turn).

---

## 26. Bot-Critical Decision Points

### When Evaluating Deploy
- Check deployment conditions (game text + rules)
- Check presence/Force icon requirement for non-spies
- Verify location type restrictions
- Calculate total cost (base + separate costs + modifiers)

### When Evaluating Battle
- Check if able to draw battle destiny (ability requirements at location)
- Calculate power totals
- Consider attrition and battle damage
- Check immunity to attrition

### When Responding
- Automatic actions MUST happen first
- Cannot respond with unrelated "at any time" actions
- Two passes ends response window

### When Losing Force
- Can choose source (hand, Reserve, Force Pile, Used)
- "From X" means start there, remainder from anywhere
- Cannot use loss-reducers on specific-card losses

### When Drawing Destiny
- Cannot draw with empty Reserve Deck
- Substituted destinies bypass limits
- Failed draws = lose the contested action

---

## 27. Common Gotchas

1. **Canceled actions still count as "once per turn"**
2. **"At any time" is NOT a response - it's top-level only**
3. **Resets make values unmodifiable**
4. **Inactive cards still count for uniqueness**
5. **Empty pile = cannot initiate searches, but CAN retrieve**
6. **"Up to X" excludes zero**
7. **Opponent gets first response to your actions**
8. **Automatic actions before just actions**
9. **Destiny value and destiny card are separate**
10. **"Free" doesn't cover separate costs**
11. **Force generation total is locked after count** - mid-phase changes don't affect your activation limit
12. **Each card can only participate in one Force drain per turn** - even if it moves to another location
13. **"Present" ≠ "Present with"** - Vader on walker can target characters present at site, but isn't present with them
14. **Undercover spies prevent unoccupied** - location isn't unoccupied if undercover spy is there
15. **"At" ≠ "On"** - Luke aboard Red 5 at Hoth system is "at Hoth" but not "on Hoth"
16. **No attrition without battle destiny** - If you draw no battle destiny, opponent has NO attrition against them (not 0 attrition)
17. **4 ability for battle destiny** - Passengers of enclosed vehicles/starships don't count toward the 4 ability requirement
18. **Immunity checked once** - Compare to TOTAL attrition at start of damage segment, not remaining after forfeits
19. **Forfeits count twice** - Every forfeited card satisfies BOTH attrition AND battle damage simultaneously
20. **Battle ends if presence removed** - If all presence removed before damage segment, battle ends immediately (but hit cards still lost)
21. **Regular move limit** - Each card can only make ONE regular move per turn, but unlimited moves are unlimited
22. **Carried ≠ Moving** - Card that "cannot move" can still be carried aboard a moving ship/vehicle
23. **Deploy cost paid even if canceled** - Paying deploy cost is part of initiation, not result
24. **Persona blocks deploy** - Cannot deploy Luke if Commander Luke is already on table (same persona)
25. **Creature attacks mandatory** - Your creatures MUST attack during your battle phase if valid target exists
26. **0 parsecs still needs hyperspeed** - Moving 0 parsecs with hyperspeed requires hyperspeed > 0 AND astromech/nav computer
27. **Cloud sectors block landing** - If cloud sectors deployed at system, must fly through them; cannot land/take off directly
28. **"Moves like" ≠ "is"** - Card that "moves like a starfighter" obeys starfighter movement rules but cannot be targeted as one
29. **Moving through triggers** - When moving through locations with landspeed >1, you're considered AT each location passed through
30. **End of turn is still that turn** - For timing/uniqueness purposes, end of turn is still that player's turn
31. **Maintenance before optional** - Maintenance costs are mandatory and must be paid before optional end of turn actions
32. **No time between turns** - Once both players pass at end of turn, next turn starts immediately (no window for actions)
33. **"Free" cannot be modified** - If movement is free (e.g., docking bay transit at certain bays), no card can add cost to it
34. **Docking moves starships, not cargo** - Moving between docked starships is movement for the SHIPS, not the characters transferred
35. **Landed starships can't "move away"** - Taking off is not a valid option for a starship making a "move away" action

---

## 28. Glossary of Key Terms

| Term | Meaning |
|------|---------|
| Activate | Move cards from Reserve Deck to Force Pile |
| Use Force | Move cards from Force Pile to Used Pile |
| Lose Force | Move cards to Lost Pile |
| Retrieve | Move top of Lost Pile to Used Pile |
| Re-circulate | Move Used Pile under Reserve Deck (end of each turn) |
| Deploy | Place card on table (usually costs Force) |
| Play | Use an Interrupt or similar card |
| Present | Physically at a specific place |
| Presence | Having ability ≥1 OR Presence icon at a location |
| Present With | Two cards present together at same place |
| Control | Occupy and opponent doesn't |
| Occupy | Have presence at location |
| Battleground | Location where battles can occur (icons on both sides) |
| Alone | Character with no other ability/characters you control at location |
| Here | "At this location" (context-dependent) |
| Persona | All versions of a unique character/ship/vehicle/weapon/device |
| Attrition | Force loss from opponent's battle destiny (must forfeit to satisfy) |
| Battle Damage | Power difference when losing battle (can forfeit or lose Force) |
| Forfeit | Lose card from battle to satisfy attrition/battle damage/hits |
| Hit | Card targeted by successful weapon fire (turned sideways, must be forfeited) |
| Participating | Cards at battle location that are part of battle |
| Excluded | Removed from participating in current battle (inactive for duration) |
| Landspeed | Movement attribute for characters/creatures/vehicles at sites |
| Hyperspeed | Movement attribute for starships between systems |
| Regular Move | Normal movement (one per card per turn) |
| Unlimited Move | Embarking, disembarking, capacity slot changes (no limit) |
| React | Move/deploy in response to battle or Force drain |
| Ferocity | Creature's equivalent of power (used in attacks) |
| Defense Value | Creature's resistance to being hit by weapons |
| Eaten | Lost to creature attack (functionally same as "lost") |
| Shuttling | Moving character/vehicle between exterior site and capital starship at system |
| Docking Bay Transit | Moving as group between any two docking bays |
| Embarking | Moving onto a vehicle/starship |
| Disembarking | Moving off of a vehicle/starship |
| Ship-Docking Capability | Ability to dock with another starship for transfers (all capital starships have by rule) |
| Cloud Sector | Sector location between system and exterior sites; must fly through to land/take off |
| Deep Space | Location with no system; Death Stars can move to deep space |
| Relocation | Moving a card between locations (unlimited move, not same as regular movement) |
| Carrying | When a card is aboard another card; carried cards move with carrier but aren't "moving" |
| Maintenance Cost | Force that must be paid each turn to keep certain cards in play |
| Follow | When one card's movement triggers another card to also move |
| Move Away | Specific type of movement triggered by certain cards; has limited valid destinations |

---

## 29. XML Decision Type Mapping

Based on GEMP implementation:

| Decision Type | Bot Should Consider |
|--------------|---------------------|
| `CARD_ACTION_CHOICE` | Deploy, move, battle, draw actions |
| `CARD_SELECTION` | Which cards to target/choose |
| `ARBITRARY_CARDS` | Selecting from revealed cards |
| `MULTIPLE_CHOICE` | Yes/no, cancel options, text choices |
| `INTEGER` | Force activation amounts, numeric choices |

---

*Reference: Advanced Rulebook 2023 Edition, Chapters 1-8 (detailed)*
