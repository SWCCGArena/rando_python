#!/usr/bin/env python3
"""
Parse game logs and extract metrics.

Usage:
    python tools/parse_results.py results/20251228_143000/

Metrics extracted:
- Winner (from filename)
- Turn count (from log)
- Deploy failure rate
- Force drains
- Battle count
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Any


def parse_log_file(log_path: Path) -> Dict[str, Any]:
    """
    Parse a single game log and extract metrics.

    Returns:
        Dict with extracted metrics
    """
    metrics = {
        'path': str(log_path),
        'filename': log_path.name,
        'won': '_win' in log_path.name,
        'turn_count': 0,
        'deploy_failures': 0,
        'force_drains': 0,
        'battles_initiated': 0,
        'decisions_made': 0,
        'plan_executions': 0,
    }

    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        # Count turn changes
        metrics['turn_count'] = len(re.findall(r'Turn (\d+)', content))

        # Count deploy failures
        metrics['deploy_failures'] = len(re.findall(r'PLAN.*FAILED|not in offered', content, re.IGNORECASE))

        # Count force drains (emoji in logs)
        metrics['force_drains'] = content.count('🩸') + len(re.findall(r'force drain', content, re.IGNORECASE))

        # Count battles initiated
        metrics['battles_initiated'] = len(re.findall(r'initiating battle|battle initiated', content, re.IGNORECASE))

        # Count decisions made
        metrics['decisions_made'] = len(re.findall(r'🎯|Decision processed|chose:', content))

        # Count plan executions
        metrics['plan_executions'] = len(re.findall(r'📋 Executing plan|Plan:.*->|Deployment plan', content))

        # Extract opponent from filename
        match = re.search(r'_vs_([^_]+)_', log_path.name)
        if match:
            metrics['opponent'] = match.group(1)

    except Exception as e:
        metrics['error'] = str(e)

    return metrics


def parse_results_directory(results_dir: Path) -> Dict[str, Any]:
    """
    Parse all game logs in a results directory.

    Returns:
        Dict with aggregated results
    """
    games_dir = results_dir / "games"
    if not games_dir.exists():
        games_dir = results_dir  # Maybe logs are directly in results_dir

    # Find all log files
    log_files = list(games_dir.glob("*.log"))
    if not log_files:
        print(f"No log files found in {games_dir}")
        return {}

    # Group by game (bot1 and bot2 logs for same game)
    game_logs: Dict[str, List[Path]] = {}
    for log in log_files:
        # Extract timestamp from filename to group
        match = re.search(r'(\d{8}_\d{6})', log.name)
        if match:
            game_id = match.group(1)
            if game_id not in game_logs:
                game_logs[game_id] = []
            game_logs[game_id].append(log)

    # Parse each game
    results = {
        'results_dir': str(results_dir),
        'total_games': len(game_logs),
        'games': [],
        'aggregated': {
            'bot1_wins': 0,
            'bot2_wins': 0,
            'avg_turns': 0,
            'avg_deploy_failures': 0,
            'avg_force_drains': 0,
            'avg_battles': 0,
            'total_deploy_failures': 0,
        }
    }

    total_turns = 0
    total_drains = 0
    total_battles = 0
    total_failures = 0

    for game_id, logs in game_logs.items():
        game_data = {
            'game_id': game_id,
            'logs': [],
        }

        for log in logs:
            metrics = parse_log_file(log)
            game_data['logs'].append(metrics)

            # Aggregate (use bot1/rando_cal stats for consistency)
            if 'rando_cal' in log.name:
                total_turns += metrics['turn_count']
                total_drains += metrics['force_drains']
                total_battles += metrics['battles_initiated']
                total_failures += metrics['deploy_failures']

                if metrics['won']:
                    results['aggregated']['bot1_wins'] += 1
                else:
                    results['aggregated']['bot2_wins'] += 1

        results['games'].append(game_data)

    # Calculate averages
    if results['total_games'] > 0:
        results['aggregated']['avg_turns'] = total_turns / results['total_games']
        results['aggregated']['avg_force_drains'] = total_drains / results['total_games']
        results['aggregated']['avg_battles'] = total_battles / results['total_games']
        results['aggregated']['avg_deploy_failures'] = total_failures / results['total_games']
        results['aggregated']['total_deploy_failures'] = total_failures

    # Load summary.json if it exists
    summary_path = results_dir / "summary.json"
    if summary_path.exists():
        with open(summary_path) as f:
            results['batch_summary'] = json.load(f)

    return results


def print_results(results: Dict[str, Any]):
    """Print results in a readable format."""
    if not results:
        return

    print("\n" + "=" * 60)
    print("BATCH RESULTS")
    print("=" * 60)

    agg = results.get('aggregated', {})
    print(f"\nTotal games: {results.get('total_games', 0)}")
    print(f"Bot1 (rando_cal) wins: {agg.get('bot1_wins', 0)}")
    print(f"Bot2 (randoblu) wins: {agg.get('bot2_wins', 0)}")

    total = agg.get('bot1_wins', 0) + agg.get('bot2_wins', 0)
    if total > 0:
        win_rate = agg.get('bot1_wins', 0) / total * 100
        print(f"Bot1 win rate: {win_rate:.1f}%")

    print(f"\nAverage turns per game: {agg.get('avg_turns', 0):.1f}")
    print(f"Average force drains: {agg.get('avg_force_drains', 0):.1f}")
    print(f"Average battles: {agg.get('avg_battles', 0):.1f}")
    print(f"Total deploy failures: {agg.get('total_deploy_failures', 0)}")
    print(f"Average deploy failures per game: {agg.get('avg_deploy_failures', 0):.1f}")

    # Show config info if available
    batch = results.get('batch_summary', {})
    if batch:
        print(f"\nConfig 1: {batch.get('config1', 'unknown')}")
        print(f"Config 2: {batch.get('config2', 'unknown')}")

    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description='Parse game results and extract metrics')
    parser.add_argument('results_dir', type=str,
                        help='Path to results directory (e.g., results/20251228_143000/)')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON instead of human-readable')

    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    if not results_dir.exists():
        print(f"ERROR: Results directory not found: {results_dir}")
        sys.exit(1)

    results = parse_results_directory(results_dir)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print_results(results)

    # Save parsed results
    output_path = results_dir / "parsed_results.json"
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nParsed results saved to: {output_path}")


if __name__ == '__main__':
    main()
