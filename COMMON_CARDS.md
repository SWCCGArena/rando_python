# Common Cards Analysis

Analysis of 67 production bot decks. Cards are prioritized for custom AI handling based on:
1. How many decks contain them (frequency)
2. Card type (Interrupts/Effects first - most complex decision points)

---

## Top Priority: Interrupts (16.5 avg/deck)

These appear in 15%+ of decks and have complex timing/decision mechanics:

### Barrier Cards (34% of decks)
| Blueprint | Side | Card Name |
|-----------|------|-----------|
| `1_249` | DS | **Imperial Barrier** |
| `1_105` | LS | **Rebel Barrier** |

**Mechanic:** Use 1 Force to prevent deployed character/starship from battling or moving this turn.
**AI Priority:** HIGH - Defensive response to opponent deployments.

---

### Destiny Manipulation (34% of decks)
| Blueprint | Side | Card Name |
|-----------|------|-----------|
| `200_54` | LS | **Jedi Levitation (V)** |
| `200_123` | DS | **Sith Fury (V)** |

**Mechanic (USED):** Take drawn character destiny into hand OR cancel/redraw destiny.
**Mechanic (LOST):** Once per game, 4 Force to retrieve character from Lost Pile.
**AI Priority:** HIGH - Critical for destiny optimization.

---

### Battle Damage Cancellation (28% LS / 24% DS)
| Blueprint | Side | Card Name |
|-----------|------|-----------|
| `2_50` | LS | **Houjix** |
| `2_132` | DS | **Ghhhk** |

**Mechanic:** If you lost battle and have no cards to forfeit, cancel all remaining battle damage.
**AI Priority:** HIGH - Survival interrupt, immune to Sense.

---

### Starting Interrupts (31% LS / 18% DS)
| Blueprint | Side | Card Name |
|-----------|------|-----------|
| `9_51` | LS | **Heading For The Medical Frigate** |
| `9_139` | DS | **Prepared Defenses** |

**Mechanic (USED):** +1 to battle destiny just drawn.
**Mechanic (STARTING):** Deploy up to 3 free Effects with "deploy on table".
**AI Priority:** MEDIUM - Starting selection handled elsewhere.

---

### Weapon Enhancement (30% of decks)
| Blueprint | Side | Card Name |
|-----------|------|-----------|
| `10_23` | LS | **Sorry About The Mess & Blaster Proficiency** |

**Mechanic (USED):** +3 to weapon destiny total.
**Mechanic (LOST):** Fire weapon during control phase (hit = lost) OR place smuggler in Reserve.
**AI Priority:** MEDIUM - Weapon usage timing.

---

### Character Protection (25% of decks)
| Blueprint | Side | Card Name |
|-----------|------|-----------|
| `6_61` | LS | **Blaster Deflection** |
| `209_21` | LS | **Odin Nesloor & First Aid** |

**Mechanic (Blaster Deflection):** Cancel weapon targeting ability > 4; re-target blasters at ability > 4.
**Mechanic (Odin Nesloor):** 1 Force when character about to be hit - forfeit protected, immune to attrition.
**AI Priority:** HIGH - Reactive protection.

---

### Utility/Cancel Cards (24-25% of decks)
| Blueprint | Side | Card Name |
|-----------|------|-----------|
| `210_24` | LS | **Quite A Mercenary (V)** |
| `204_18` | LS | **Escape Pod & We're Doomed** |
| `12_152` | DS | **Masterful Move & Endor Occupation** |
| `201_13` | LS | **Hear Me Baby, Hold Together (V)** |

**Common mechanics:** Cancel opponent cards, retrieve cards from Reserve Deck.
**AI Priority:** MEDIUM - Situational responses.

---

### Force Retrieval/Command (19% of decks)
| Blueprint | Side | Card Name |
|-----------|------|-----------|
| `9_137` | DS | **Imperial Command** |
| `203_17` | LS | **Rebel Leadership (V)** |

**Mechanic:** Take admiral/general into hand OR add battle destiny OR prevent opponent adding.
**AI Priority:** MEDIUM - Deck search and battle manipulation.

---

### Combo Interrupts (16% of decks)
| Blueprint | Side | Card Name |
|-----------|------|-----------|
| `11_29` | LS | **A Jedi's Resilience** |
| `209_48` | DS | **Lana Dobreed & Sacrifice** |
| `10_39` | DS | **Ghhhk & Those Rebels Won't Escape Us** |

**Various defensive/offensive mechanics.**
**AI Priority:** MEDIUM.

---

## Top Priority: Effects (7.4 avg/deck)

### Starting Effects (48% LS / 43% DS)
| Blueprint | Side | Card Name |
|-----------|------|-----------|
| `200_35` | LS | **Anger, Fear, Aggression (V)** |
| `200_110` | DS | **Knowledge And Defense (V)** |

**Mechanic:** Deploy with Defensive Shields face-down. 4x per game, play card from here.
**AI Priority:** HIGH - Core game mechanic for Defensive Shield deployment.

---

### Force Generation (36% of decks)
| Blueprint | Side | Card Name |
|-----------|------|-----------|
| `200_47` | LS | **Wokling (V)** |

**Mechanic:** Personal Force gen = 2. Once per game, 3 Force to take 0-cost Effect deploying on card.
**AI Priority:** MEDIUM - Passive effect.

---

### Common Effects (10-15% of decks)
| Blueprint | Side | Card Name |
|-----------|------|-----------|
| `200_34` | LS | **A Good Blaster At Your Side** |
| `200_114` | DS | **You'll Be Dead!** |
| `9_126` | DS | **Inconsequential Losses** |
| `200_45` | LS | **Sai'torr Kal Fas (V)** |
| `6_56` | LS | **Projection Of A Skywalker** |
| `6_149` | DS | **Scum And Villainy** |
| `7_55` | LS | **Cloud City Celebration** |
| `221_39` | DS | **Tentacle (V)** |
| `9_39` | LS | **Squadron Assignments** |

**AI Priority:** LOW-MEDIUM - Passive bonuses or specific triggers.

---

## Secondary Priority: Characters (19.8 avg/deck)

Most common characters (15%+ of decks):

| Blueprint | Side | Card Name | Decks |
|-----------|------|-----------|-------|
| `200_2` | LS | Anakin Skywalker, Padawan Learner | 28% |
| `212_3` | DS | Aurra Sing With Blaster Rifle | 25% |
| `204_9` | LS | Rey | 24% |
| `204_3` | LS | Captain Hera Syndulla | 24% |
| `200_86` | DS | Mara Jade With Lightsaber | 22% |
| `10_5` | LS | Corran Horn | 21% |
| `12_114` | DS | P-59 | 21% |
| `207_5` | LS | General Leia Organa | 19% |
| `204_11` | LS | Solo | 19% |

**AI Priority:** Characters handled by existing deploy logic.

---

## Summary Statistics

| Card Type | Unique Cards | Total Copies | Avg/Deck |
|-----------|--------------|--------------|----------|
| Interrupt | 220 | 1,108 | 16.5 |
| Effect | 146 | 493 | 7.4 |
| Character | 379 | 1,326 | 19.8 |
| Location | 185 | 403 | 6.0 |
| Starship | 90 | 247 | 3.7 |
| Weapon | 59 | 233 | 3.5 |
| Vehicle | 25 | 80 | 1.2 |
| Objective | 29 | 53 | 0.8 |
| Device | 12 | 22 | 0.3 |

---

## Recommended AI Implementation Order

### Phase 1: Critical Interrupts (appear in 24-34% of decks)
1. **Barrier cards** (`1_249`, `1_105`) - Prevent opponent from acting after deploy
2. **Destiny manipulation** (`200_54`, `200_123`) - Take/redraw character destinies
3. **Battle damage cancel** (`2_50`, `2_132`) - Houjix/Ghhhk survival
4. **Character protection** (`6_61`, `209_21`) - Respond to weapon targeting

### Phase 2: Common Interrupts (15-24% of decks)
5. **Weapon enhancement** (`10_23`) - Boost weapon destiny
6. **Cancel/utility cards** - Various opponent cancellations
7. **Command cards** (`9_137`, `203_17`) - Admiral/general retrieval

### Phase 3: Effects
8. **Starting effects** - Already handled by deck building
9. **Passive effects** - Lower priority, often just bonuses

---

## Blueprint ID Quick Reference

```
# HIGH PRIORITY - Defensive Interrupts
1_249   = Imperial Barrier (DS)
1_105   = Rebel Barrier (LS)
2_132   = Ghhhk (DS)
2_50    = Houjix (LS)

# HIGH PRIORITY - Destiny Manipulation
200_54  = Jedi Levitation (V) (LS)
200_123 = Sith Fury (V) (DS)

# HIGH PRIORITY - Character Protection
6_61    = Blaster Deflection (LS)
209_21  = Odin Nesloor & First Aid (LS)

# MEDIUM PRIORITY - Weapon/Battle
10_23   = Sorry About The Mess & Blaster Proficiency (LS)
9_137   = Imperial Command (DS)
203_17  = Rebel Leadership (V) (LS)

# MEDIUM PRIORITY - Utility/Cancel
210_24  = Quite A Mercenary (V) (LS)
204_18  = Escape Pod & We're Doomed (LS)
12_152  = Masterful Move & Endor Occupation (DS)
201_13  = Hear Me Baby, Hold Together (V) (LS)

# Starting Interrupts (deck building)
9_51    = Heading For The Medical Frigate (LS)
9_139   = Prepared Defenses (DS)
```
