#!/usr/bin/env python3
"""
Analyze bot-vs-bot game results.

Usage:
    python tools/analyze_results.py results/20251229_100656
    python tools/analyze_results.py results/20251229_100656 --verbose
    python tools/analyze_results.py results/20251229_100656 --compare results/20251228_194517
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional


def load_summary(results_dir: Path) -> dict:
    """Load summary.json from results directory."""
    summary_path = results_dir / "summary.json"
    if not summary_path.exists():
        print(f"ERROR: {summary_path} not found")
        sys.exit(1)
    with open(summary_path) as f:
        return json.load(f)


def analyze_log_file(log_path: Path) -> dict:
    """Extract key metrics from a game log file."""
    if not log_path.exists():
        return {"error": "file not found"}

    content = log_path.read_text()
    lines = content.split('\n')

    metrics = {
        "errors": [],
        "warnings": [],
        "decisions": Counter(),
        "turns": 0,
        "deploys": 0,
        "battles": 0,
        "passes": 0,
        "plan_completions": 0,
        "early_game_blocks": 0,
        "force_activations": [],
    }

    for line in lines:
        # Count errors and warnings
        if " - ERROR - " in line or "ERROR:" in line:
            # Extract just the message part
            if " - ERROR - " in line:
                msg = line.split(" - ERROR - ")[-1][:100]
            else:
                msg = line[:100]
            metrics["errors"].append(msg)

        if " - WARNING - " in line:
            msg = line.split(" - WARNING - ")[-1][:80]
            # Skip noisy warnings
            if "zones didn't match" not in msg:
                metrics["warnings"].append(msg)

        # Count decision types
        if "decisionType=" in line:
            match = re.search(r'decisionType="(\w+)"', line)
            if match:
                metrics["decisions"][match.group(1)] += 1

        # Track turn count
        if "Turn:" in line or "turn #" in line:
            match = re.search(r'turn[# ]+(\d+)', line, re.IGNORECASE)
            if match:
                turn = int(match.group(1))
                metrics["turns"] = max(metrics["turns"], turn)

        # Track deploy actions
        if "Deploy " in line and ("score=" in line or "Deploying" in line):
            metrics["deploys"] += 1

        # Track battles
        if "initiate battle" in line.lower() or "Battle at" in line:
            metrics["battles"] += 1

        # Track passes
        if "Choosing to pass" in line or "selected: Pass" in line.lower():
            metrics["passes"] += 1

        # Track plan completions
        if "Plan complete" in line:
            metrics["plan_completions"] += 1

        # Track early game blocks
        if "early_game" in line.lower() and ("block" in line.lower() or "filter" in line.lower() or "threshold" in line.lower()):
            metrics["early_game_blocks"] += 1

        # Track force activation amounts
        if "Activate" in line and "force" in line.lower():
            match = re.search(r'Activate (\d+)', line)
            if match:
                metrics["force_activations"].append(int(match.group(1)))

    # Summarize force activations
    if metrics["force_activations"]:
        metrics["avg_force_activation"] = sum(metrics["force_activations"]) / len(metrics["force_activations"])
    else:
        metrics["avg_force_activation"] = 0

    return metrics


def print_summary(summary: dict, verbose: bool = False):
    """Print analysis of a run."""
    print(f"\n{'='*60}")
    print(f"Run: {summary['timestamp']}")
    if summary.get('ab_test'):
        print(f"Mode: A/B Test")
        print(f"Config1 (MC): {summary['config1']}")
        print(f"Config2 (control): {summary['config2']}")
    else:
        print(f"Config: {summary['config1']}")
    print(f"Games: {summary['num_games']} (parallel={summary.get('parallel', 1)})")
    print(f"{'='*60}")

    # Win/loss stats
    creator_wins = summary["creator_wins"]
    joiner_wins = summary["joiner_wins"]
    errors = summary.get("errors", 0)
    total = creator_wins + joiner_wins

    print(f"\n## Results")
    print(f"Dark Side (creator) wins: {creator_wins} ({creator_wins/total*100:.1f}%)")
    print(f"Light Side (joiner) wins: {joiner_wins} ({joiner_wins/total*100:.1f}%)")
    print(f"Errors: {errors}")

    # Game sequence
    games = summary.get("games", [])
    if games:
        # Sort by game_num
        sorted_games = sorted(games, key=lambda g: g.get("game_num", 0))
        sequence = "".join(["D" if g.get("creator_won") else "L" for g in sorted_games])
        print(f"\nSequence (D=Dark win, L=Light win): {sequence}")

        # Streaks
        max_dark_streak = max_light_streak = 0
        current_dark = current_light = 0
        for g in sorted_games:
            if g.get("creator_won"):
                current_dark += 1
                current_light = 0
                max_dark_streak = max(max_dark_streak, current_dark)
            else:
                current_light += 1
                current_dark = 0
                max_light_streak = max(max_light_streak, current_light)

        print(f"Max Dark streak: {max_dark_streak}")
        print(f"Max Light streak: {max_light_streak}")

        # Duration stats
        durations = [g.get("duration", 0) for g in sorted_games if g.get("completed")]
        if durations:
            print(f"\n## Duration")
            print(f"Average: {sum(durations)/len(durations):.1f}s")
            print(f"Min: {min(durations):.1f}s")
            print(f"Max: {max(durations):.1f}s")

        # By pair
        pair_stats = defaultdict(lambda: {"wins": 0, "losses": 0})
        for g in sorted_games:
            pair = g.get("pair_id", "A")
            if g.get("creator_won"):
                pair_stats[pair]["wins"] += 1
            else:
                pair_stats[pair]["losses"] += 1

        if len(pair_stats) > 1:
            print(f"\n## By Bot Pair")
            for pair, stats in sorted(pair_stats.items()):
                total_pair = stats["wins"] + stats["losses"]
                pct = stats["wins"] / total_pair * 100 if total_pair > 0 else 0
                print(f"Pair {pair}: {stats['wins']}-{stats['losses']} ({pct:.0f}% Dark)")

    return summary


def analyze_game_logs(results_dir: Path, summary: dict, verbose: bool = False):
    """Analyze individual game logs for patterns."""
    games_dir = results_dir / "games"
    if not games_dir.exists():
        print(f"\nNo games directory found")
        return

    all_metrics = []
    win_metrics = []
    loss_metrics = []
    all_errors = []
    all_warnings = []

    for game in summary.get("games", []):
        creator_log = game.get("creator_log", "")
        if creator_log:
            log_path = Path(creator_log)
            if not log_path.exists():
                # Try relative to games_dir
                log_path = games_dir / Path(creator_log).name

            metrics = analyze_log_file(log_path)
            metrics["game_num"] = game.get("game_num")
            metrics["won"] = game.get("creator_won", False)
            all_metrics.append(metrics)

            if game.get("creator_won"):
                win_metrics.append(metrics)
            else:
                loss_metrics.append(metrics)

            all_errors.extend(metrics.get("errors", []))
            all_warnings.extend(metrics.get("warnings", []))

    # Print error summary
    if all_errors:
        print(f"\n## Errors Found ({len(all_errors)} total)")
        error_counts = Counter(all_errors)
        for error, count in error_counts.most_common(5):
            print(f"  [{count}x] {error[:70]}...")
    else:
        print(f"\n## No Errors Found")

    # Print warning summary (excluding noisy ones)
    unique_warnings = set(all_warnings)
    if unique_warnings and verbose:
        print(f"\n## Unique Warnings ({len(unique_warnings)})")
        for warn in list(unique_warnings)[:5]:
            print(f"  - {warn[:70]}...")

    # Compare wins vs losses
    if win_metrics and loss_metrics:
        print(f"\n## Win vs Loss Comparison")

        def avg_metric(metrics_list, key):
            values = [m.get(key, 0) for m in metrics_list]
            return sum(values) / len(values) if values else 0

        print(f"{'Metric':<25} {'Wins':>10} {'Losses':>10}")
        print(f"{'-'*25} {'-'*10} {'-'*10}")

        for metric in ["turns", "deploys", "battles", "passes", "plan_completions", "avg_force_activation"]:
            win_avg = avg_metric(win_metrics, metric)
            loss_avg = avg_metric(loss_metrics, metric)
            print(f"{metric:<25} {win_avg:>10.1f} {loss_avg:>10.1f}")


def analyze_mc_comparison(summary: dict):
    """
    Analyze Monte Carlo A/B test results.

    In A/B test mode:
    - Pair A: creator uses config1 (MC), joiner uses config2 (no MC)
    - Pair B: creator uses config2 (no MC), joiner uses config1 (MC)

    So:
    - MC wins = Pair A creator wins + Pair B joiner wins
    - Non-MC wins = Pair A joiner wins + Pair B creator wins
    """
    if not summary.get('ab_test'):
        return

    games = summary.get('games', [])
    if not games:
        return

    mc_wins = 0
    mc_games = 0
    non_mc_wins = 0
    non_mc_games = 0

    # Track by side for more detailed analysis
    mc_dark_wins = 0
    mc_dark_games = 0
    mc_light_wins = 0
    mc_light_games = 0

    for game in games:
        if not game.get('completed'):
            continue

        pair_id = game.get('pair_id', 'A')
        creator_won = game.get('creator_won', False)

        # Pair A: creator = MC (Dark), joiner = no-MC (Light)
        if pair_id == 'A':
            mc_dark_games += 1
            non_mc_games += 1
            if creator_won:
                mc_wins += 1
                mc_dark_wins += 1
            else:
                non_mc_wins += 1

        # Pair B: creator = no-MC (Dark), joiner = MC (Light)
        elif pair_id == 'B':
            mc_light_games += 1
            non_mc_games += 1
            if creator_won:
                non_mc_wins += 1
            else:
                mc_wins += 1
                mc_light_wins += 1

    mc_games = mc_dark_games + mc_light_games
    total_games = mc_wins + non_mc_wins

    if total_games == 0:
        return

    print(f"\n## Monte Carlo A/B Test Results")
    print(f"{'='*50}")

    mc_pct = mc_wins / total_games * 100 if total_games > 0 else 0
    non_mc_pct = non_mc_wins / total_games * 100 if total_games > 0 else 0

    print(f"MC enabled:   {mc_wins:>3}/{total_games} wins ({mc_pct:.1f}%)")
    print(f"MC disabled:  {non_mc_wins:>3}/{total_games} wins ({non_mc_pct:.1f}%)")

    # Calculate statistical significance (simple chi-squared approximation)
    expected = total_games / 2
    if expected > 0:
        chi_sq = ((mc_wins - expected) ** 2 / expected) + ((non_mc_wins - expected) ** 2 / expected)
        # Chi-squared critical value for p=0.05, df=1 is ~3.84
        significant = chi_sq > 3.84
        print(f"\nChi-squared: {chi_sq:.2f} {'(significant at p<0.05)' if significant else '(not significant)'}")

    # Breakdown by side
    print(f"\n## MC Performance by Side")
    if mc_dark_games > 0:
        dark_pct = mc_dark_wins / mc_dark_games * 100
        print(f"MC as Dark:  {mc_dark_wins:>3}/{mc_dark_games} wins ({dark_pct:.1f}%)")
    if mc_light_games > 0:
        light_pct = mc_light_wins / mc_light_games * 100
        print(f"MC as Light: {mc_light_wins:>3}/{mc_light_games} wins ({light_pct:.1f}%)")

    # Comparison insight
    delta = mc_pct - 50
    if delta > 5:
        print(f"\n✅ Monte Carlo shows improvement (+{delta:.1f}% over baseline)")
    elif delta < -5:
        print(f"\n❌ Monte Carlo shows regression ({delta:.1f}% vs baseline)")
    else:
        print(f"\n⚖️ Results inconclusive (within ±5% of baseline)")


def get_dark_wins(run: dict) -> int:
    """Get dark side wins, handling both old and new field names."""
    return run.get("creator_wins", run.get("bot1_wins", 0))


def get_light_wins(run: dict) -> int:
    """Get light side wins, handling both old and new field names."""
    return run.get("joiner_wins", run.get("bot2_wins", 0))


def compare_runs(run1: dict, run2: dict):
    """Compare two runs."""
    print(f"\n{'='*60}")
    print(f"Comparison: {run1['timestamp']} vs {run2['timestamp']}")
    print(f"{'='*60}")

    r1_dark = get_dark_wins(run1)
    r1_light = get_light_wins(run1)
    r2_dark = get_dark_wins(run2)
    r2_light = get_light_wins(run2)

    r1_total = r1_dark + r1_light
    r2_total = r2_dark + r2_light

    r1_dark_pct = r1_dark / r1_total * 100 if r1_total > 0 else 0
    r2_dark_pct = r2_dark / r2_total * 100 if r2_total > 0 else 0

    print(f"\n{'Metric':<20} {run1['timestamp']:>15} {run2['timestamp']:>15} {'Delta':>10}")
    print(f"{'-'*20} {'-'*15} {'-'*15} {'-'*10}")
    print(f"{'Config':<20} {run1.get('config1','baseline'):>15} {run2.get('config1','baseline'):>15}")
    print(f"{'Games':<20} {r1_total:>15} {r2_total:>15}")
    print(f"{'Dark Win %':<20} {r1_dark_pct:>14.1f}% {r2_dark_pct:>14.1f}% {r2_dark_pct-r1_dark_pct:>+9.1f}%")
    print(f"{'Errors':<20} {run1.get('errors', 0):>15} {run2.get('errors', 0):>15}")


def main():
    parser = argparse.ArgumentParser(description='Analyze bot-vs-bot game results')
    parser.add_argument('results_dir', type=str, help='Path to results directory')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show detailed analysis')
    parser.add_argument('--compare', type=str, help='Compare with another results directory')

    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    if not results_dir.exists():
        print(f"ERROR: {results_dir} not found")
        sys.exit(1)

    summary = load_summary(results_dir)
    print_summary(summary, args.verbose)
    analyze_game_logs(results_dir, summary, args.verbose)

    # A/B test analysis for Monte Carlo comparison
    if summary.get('ab_test'):
        analyze_mc_comparison(summary)

    if args.compare:
        compare_dir = Path(args.compare)
        if compare_dir.exists():
            compare_summary = load_summary(compare_dir)
            compare_runs(compare_summary, summary)
        else:
            print(f"WARNING: Compare directory not found: {compare_dir}")


if __name__ == '__main__':
    main()
