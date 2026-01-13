#!/usr/bin/env python3
"""
Compare two batch results for statistical significance.

Usage:
    python tools/compare_configs.py results/baseline/ results/experimental/

Performs:
- Win rate comparison with confidence intervals
- Chi-squared test for statistical significance
- Summary recommendation
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Any, Optional

# Try to import scipy for statistical tests
try:
    from scipy import stats
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False


def load_results(results_dir: Path) -> Optional[Dict[str, Any]]:
    """Load parsed results from a directory."""
    # Try parsed_results.json first
    parsed_path = results_dir / "parsed_results.json"
    if parsed_path.exists():
        with open(parsed_path) as f:
            return json.load(f)

    # Try summary.json
    summary_path = results_dir / "summary.json"
    if summary_path.exists():
        with open(summary_path) as f:
            return json.load(f)

    return None


def calculate_confidence_interval(wins: int, total: int, confidence: float = 0.95) -> tuple:
    """
    Calculate confidence interval for win rate using Wilson score interval.

    Returns:
        (lower_bound, upper_bound) as percentages
    """
    if total == 0:
        return (0, 100)

    p = wins / total
    z = 1.96  # 95% confidence

    denominator = 1 + z**2 / total
    center = (p + z**2 / (2 * total)) / denominator
    margin = z * ((p * (1 - p) / total + z**2 / (4 * total**2)) ** 0.5) / denominator

    lower = max(0, center - margin) * 100
    upper = min(1, center + margin) * 100

    return (lower, upper)


def chi_squared_test(wins1: int, total1: int, wins2: int, total2: int) -> tuple:
    """
    Perform chi-squared test to compare two proportions.

    Returns:
        (chi2_stat, p_value)
    """
    if not SCIPY_AVAILABLE:
        return (None, None)

    # Create contingency table
    # [[wins1, losses1], [wins2, losses2]]
    table = [
        [wins1, total1 - wins1],
        [wins2, total2 - wins2]
    ]

    try:
        chi2, p, dof, expected = stats.chi2_contingency(table)
        return (chi2, p)
    except Exception:
        return (None, None)


def compare_results(results1: Dict[str, Any], results2: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compare two sets of results.

    Returns:
        Dict with comparison metrics
    """
    # Get aggregated data
    agg1 = results1.get('aggregated', {})
    agg2 = results2.get('aggregated', {})

    # Get config names
    batch1 = results1.get('batch_summary', {})
    batch2 = results2.get('batch_summary', {})
    config1_name = batch1.get('config1', results1.get('results_dir', 'unknown'))
    config2_name = batch2.get('config1', results2.get('results_dir', 'unknown'))

    # Win counts
    wins1 = agg1.get('bot1_wins', 0)
    total1 = results1.get('total_games', wins1 + agg1.get('bot2_wins', 0))

    wins2 = agg2.get('bot1_wins', 0)
    total2 = results2.get('total_games', wins2 + agg2.get('bot2_wins', 0))

    # Win rates
    rate1 = (wins1 / total1 * 100) if total1 > 0 else 0
    rate2 = (wins2 / total2 * 100) if total2 > 0 else 0

    # Confidence intervals
    ci1 = calculate_confidence_interval(wins1, total1)
    ci2 = calculate_confidence_interval(wins2, total2)

    # Statistical test
    chi2, p_value = chi_squared_test(wins1, total1, wins2, total2)

    comparison = {
        'config1': {
            'name': config1_name,
            'wins': wins1,
            'total': total1,
            'win_rate': rate1,
            'confidence_interval': ci1,
            'avg_turns': agg1.get('avg_turns', 0),
            'avg_deploy_failures': agg1.get('avg_deploy_failures', 0),
        },
        'config2': {
            'name': config2_name,
            'wins': wins2,
            'total': total2,
            'win_rate': rate2,
            'confidence_interval': ci2,
            'avg_turns': agg2.get('avg_turns', 0),
            'avg_deploy_failures': agg2.get('avg_deploy_failures', 0),
        },
        'difference': {
            'win_rate_diff': rate2 - rate1,
            'chi_squared': chi2,
            'p_value': p_value,
            'significant_95': p_value < 0.05 if p_value else None,
            'significant_90': p_value < 0.10 if p_value else None,
        }
    }

    # Recommendation
    if p_value is not None:
        if p_value < 0.05:
            if rate2 > rate1:
                comparison['recommendation'] = f"ADOPT: {config2_name} shows significant improvement (p={p_value:.3f})"
            else:
                comparison['recommendation'] = f"REJECT: {config2_name} shows significant decline (p={p_value:.3f})"
        elif p_value < 0.10:
            comparison['recommendation'] = f"WEAK SIGNAL: Difference marginally significant (p={p_value:.3f}), consider more games"
        else:
            comparison['recommendation'] = f"NO DIFFERENCE: Results not statistically significant (p={p_value:.3f})"
    else:
        comparison['recommendation'] = "Unable to calculate significance (scipy not installed)"

    return comparison


def print_comparison(comparison: Dict[str, Any]):
    """Print comparison in human-readable format."""
    print("\n" + "=" * 70)
    print("CONFIG COMPARISON")
    print("=" * 70)

    c1 = comparison['config1']
    c2 = comparison['config2']
    diff = comparison['difference']

    print(f"\n{'Config':<30} {'Wins':>8} {'Total':>8} {'Rate':>10} {'95% CI':>15}")
    print("-" * 70)

    ci1_str = f"[{c1['confidence_interval'][0]:.1f}%, {c1['confidence_interval'][1]:.1f}%]"
    ci2_str = f"[{c2['confidence_interval'][0]:.1f}%, {c2['confidence_interval'][1]:.1f}%]"

    print(f"{c1['name']:<30} {c1['wins']:>8} {c1['total']:>8} {c1['win_rate']:>9.1f}% {ci1_str:>15}")
    print(f"{c2['name']:<30} {c2['wins']:>8} {c2['total']:>8} {c2['win_rate']:>9.1f}% {ci2_str:>15}")

    print("-" * 70)
    print(f"Difference: {diff['win_rate_diff']:+.1f}%")

    if diff['p_value'] is not None:
        sig = "***" if diff['p_value'] < 0.01 else "**" if diff['p_value'] < 0.05 else "*" if diff['p_value'] < 0.10 else ""
        print(f"Chi-squared: {diff['chi_squared']:.2f}, p-value: {diff['p_value']:.4f} {sig}")
    else:
        print("Statistical test: N/A (install scipy for significance testing)")

    print(f"\nAverage turns: {c1['avg_turns']:.1f} vs {c2['avg_turns']:.1f}")
    print(f"Avg deploy failures: {c1['avg_deploy_failures']:.1f} vs {c2['avg_deploy_failures']:.1f}")

    print("\n" + "=" * 70)
    print(f"RECOMMENDATION: {comparison['recommendation']}")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description='Compare two batch results')
    parser.add_argument('results1', type=str,
                        help='Path to first results directory (baseline)')
    parser.add_argument('results2', type=str,
                        help='Path to second results directory (experimental)')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON instead of human-readable')

    args = parser.parse_args()

    results1_dir = Path(args.results1)
    results2_dir = Path(args.results2)

    if not results1_dir.exists():
        print(f"ERROR: Results directory not found: {results1_dir}")
        sys.exit(1)
    if not results2_dir.exists():
        print(f"ERROR: Results directory not found: {results2_dir}")
        sys.exit(1)

    results1 = load_results(results1_dir)
    results2 = load_results(results2_dir)

    if results1 is None:
        print(f"ERROR: Could not load results from {results1_dir}")
        print("  Run parse_results.py first, or ensure summary.json exists")
        sys.exit(1)
    if results2 is None:
        print(f"ERROR: Could not load results from {results2_dir}")
        print("  Run parse_results.py first, or ensure summary.json exists")
        sys.exit(1)

    comparison = compare_results(results1, results2)

    if args.json:
        print(json.dumps(comparison, indent=2))
    else:
        print_comparison(comparison)

    # Save comparison
    output_path = results2_dir / "comparison.json"
    with open(output_path, 'w') as f:
        json.dump(comparison, f, indent=2)
    print(f"\nComparison saved to: {output_path}")


if __name__ == '__main__':
    main()
