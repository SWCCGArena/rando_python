# Neural Deploy Planner

A neural network trained via PPO (Proximal Policy Optimization) to make deployment decisions in SWCCG. Designed as a drop-in replacement for the rules-based `DeployPhasePlanner`.

## Overview

The neural planner learns to answer: **"Which cards should I deploy, and where?"**

Instead of hand-coded rules, it learns deployment strategy through self-play against the rules-based bot, receiving rewards for winning games.

## Architecture

### State Representation (640 dimensions)

```
[0:64]     Global features    - turn, force, life force, drain gap, phase
[64:448]   Location features  - 16 locations × 24 features each
[448:480]  Hand aggregate     - total power, card counts, affordable cards
[480:640]  Per-card features  - 8 cards × 20 features each
```

**Per-card features** (the key improvement over v1):
- power, deploy_cost, ability (normalized)
- is_character, is_starship, is_vehicle, is_pilot
- **is_ground_deployable, is_space_deployable** (prevents ship-to-ground errors)
- can_afford, is_unique (main character)
- power_efficiency (power/deploy ratio)
- destiny, forfeit

### Network Architecture (2.7M parameters)

```
Input: 640-dim state
       │
       ├── Global Encoder ──────────────────────────────► [384]
       │   (MLP on global + hand aggregate)
       │
       ├── Location Encoder ────► Self-Attention ───────► [16 × 384] ──► Pool ──► [192]
       │   (16 locations as tokens)
       │
       └── Card Encoder ────────► Self-Attention ───────► [8 × 384]
                                        │
                                        ▼
                                Cross-Attention ────────► [8 × 384] ──► Pool ──► [192]
                                (cards attend to locations)

Combined: [384 + 192 + 192] = [768] ──► MLP ──► [384]
                                         │
                                         ├──► Policy Head ──► [21] logits
                                         └──► Value Head  ──► [1] value
```

**Key design choices**:
- **Location self-attention**: Learns spatial relationships (e.g., adjacent locations, contested areas)
- **Card self-attention**: Learns card relationships (e.g., pilots and ships, character combos)
- **Cross-attention**: Cards attend to locations → learns "where should this card go?"
- **Separate encoders**: Prevents the network from confusing card features with location features

### Action Space (21 discrete actions)

| Index | Action | Description |
|-------|--------|-------------|
| 0 | HOLD_BACK | Don't deploy this phase |
| 1-16 | DEPLOY_TO_LOC_N | Deploy to location index N |
| 17 | DEPLOY_LOCATION | Deploy a location card |
| 18 | ESTABLISH_GROUND | Auto-select best ground location |
| 19 | ESTABLISH_SPACE | Auto-select best space location |
| 20 | REINFORCE_BEST | Reinforce most threatened position |

Invalid actions are masked to -inf before softmax.

## Files

```
engine/neural_planner/
├── __init__.py              # Exports main classes
├── neural_deploy_planner.py # Main interface (NeuralDeployPlanner class)
├── state_encoder.py         # BoardState → 640-dim tensor
├── network.py               # PyTorch model (DeployPolicyNetwork)
├── trainer.py               # PPO training (PPOTrainer, PPOConfig)
├── experience.py            # Experience, GameTrajectory, ExperienceBatch
├── collector.py             # ExperienceCollector - gathers training data
├── trajectory_io.py         # Save/load trajectories to JSON files
├── action_decoder.py        # Action int → DeploymentPlan
├── rewards.py               # Reward shaping functions
└── README.md                # This file

training/
├── train_neural.py          # Main training script with progress display
└── run_training_game.py     # Run single/parallel games for training

models/
└── deploy_planner.pt        # Trained model weights (gitignored)

configs/
└── neural.json              # Config for neural bot
```

## Training

### Quick Start

```bash
cd new_rando
source venv/bin/activate

# Run 500 games with 5 parallel bot pairs
python training/train_neural.py --games 500 --batch-size 5 --parallel 5 --device cuda

# Continue from existing model
python training/train_neural.py --games 1000 --resume --device cuda
```

### Bot Pairs for Parallel Training

5 bot pairs allow up to 5 simultaneous games:

| Pair | Neural (creator) | Rules (joiner) | Ports |
|------|-----------------|----------------|-------|
| 0 | rando_cal | randoblu | 6001, 6002 |
| 1 | randored | randogre | 6003, 6004 |
| 2 | rando5 | rando6 | 6005, 6006 |
| 3 | rando11 | rando8 | 6007, 6008 |
| 4 | rando9 | rando10 | 6009, 6010 |

### Training Metrics

Watch these metrics during training:

| Metric | Good Sign | Bad Sign |
|--------|-----------|----------|
| **Win Rate** | Trending up from 50% | Stuck at 45-50% |
| **Policy Loss** | Small, stable (0.1-0.3) | Large spikes, unstable |
| **Value Loss** | Decreasing over time | Not decreasing |
| **Entropy** | Slowly decreasing | Collapsed to near 0 with no win improvement |

**Warning signs**:
- Entropy drops to <0.3 but win rate stays at ~45% → network converged to bad policy
- Value loss not decreasing → network not learning to predict outcomes

### Reward Shaping

| Signal | Value | Trigger |
|--------|-------|---------|
| Game win | +1.0 | Episode end |
| Game loss | -1.0 | Episode end |

Currently using sparse rewards only. Intermediate shaping (drain gap, power advantage) was tested but didn't improve learning.

## Integration

### Enable Neural Planner

In `configs/neural.json`:
```json
{
  "neural_deploy": {
    "enabled": true,
    "model_path": "models/deploy_planner.pt",
    "confidence_threshold": 0.3
  }
}
```

Set `STRATEGY_CONFIG` environment variable:
```bash
export STRATEGY_CONFIG=configs/neural.json
```

### Training Mode

To collect training data during games:
```bash
export NEURAL_TRAINING_MODE=1
export NEURAL_DEVICE=cpu  # Use CPU for bot inference (GPU for training)
```

Trajectories are saved to `training_data/trajectories/`.

## Development History

### v1: Simple MLP (512 dims)
- Aggregate hand features only (total power, card counts)
- 629K parameters
- **Result**: Converged to ~45% win rate, entropy collapsed
- **Problem**: Network couldn't distinguish between different cards in hand

### v2: Per-card Features (640 dims)
- Added 8 cards × 20 features = 160 dims for individual cards
- Includes power, deploy, ability, type, can_afford per card
- Still simple MLP architecture

### v3: Attention Architecture (current)
- Separate encoders for global/locations/cards
- Self-attention over locations and cards
- Cross-attention: cards attend to locations
- 2.7M parameters, <1ms inference
- **Status**: Being evaluated

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| Win rate stuck at 45% | Insufficient state info or model capacity | Use v3 architecture with per-card features |
| Entropy collapsed quickly | Learning rate too high or model too small | Reduce LR, increase hidden_dim |
| Training crashes with NaN | Numerical instability | Check log_prob clamping in network.py |
| Trajectories not saving | NEURAL_TRAINING_MODE not set | Export env var before running bot |
| Wrong bot using neural | Config not loaded | Check STRATEGY_CONFIG path |

## Future Improvements

1. **Action space refinement**: Instead of "deploy to location X", output "deploy card Y to location X"
2. **Opponent modeling**: Encode opponent's visible cards and predict their strategy
3. **Self-play**: Train against itself instead of rules bot for continued improvement
4. **Battle/Move phases**: Extend neural planning beyond deploy phase

## References

- PPO paper: "Proximal Policy Optimization Algorithms" (Schulman et al., 2017)
- Attention: "Attention Is All You Need" (Vaswani et al., 2017)
