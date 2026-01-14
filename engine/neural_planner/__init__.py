"""
Neural Deploy Planner

A neural network-based replacement for the rules-based DeployPhasePlanner.
Uses PPO (Proximal Policy Optimization) trained via self-play.

Training happens on powerful hardware (GPU), but inference runs on CPU
for deployment on low-resource machines.

Components:
- StateEncoder: Converts BoardState to fixed-size tensor
- DeployPolicyNetwork: Actor-critic neural network
- ActionDecoder: Converts network output to DeploymentPlan
- NeuralDeployPlanner: Drop-in replacement for DeployPhasePlanner
"""

from .neural_deploy_planner import NeuralDeployPlanner
from .state_encoder import StateEncoder
from .network import DeployPolicyNetwork
from .action_decoder import ActionDecoder
from .collector import TrainingNeuralPlanner, ExperienceCollector
from .trajectory_io import save_trajectory, load_trajectory, load_trajectories_from_dir

__all__ = [
    'NeuralDeployPlanner',
    'StateEncoder',
    'DeployPolicyNetwork',
    'ActionDecoder',
    'TrainingNeuralPlanner',
    'ExperienceCollector',
    'save_trajectory',
    'load_trajectory',
    'load_trajectories_from_dir',
]
