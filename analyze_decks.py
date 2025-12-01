#!/usr/bin/env python3
"""Analyze deck files to find most common cards, prioritizing interrupts and effects."""

import json
import os
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def load_card_database():
    """Load card data from JSON files and build lookup by gempId."""
    card_db = {}
    json_dir = Path("/opt/gemp/rando_cal_working/swccg-card-json")

    for json_file in ["Light.json", "Dark.json"]:
        filepath = json_dir / json_file
        if filepath.exists():
            with open(filepath, 'r') as f:
                data = json.load(f)
                for card in data.get('cards', []):
                    gemp_id = card.get('gempId')
                    if gemp_id:
                        front = card.get('front', {})
                        card_db[gemp_id] = {
                            'title': front.get('title', 'Unknown'),
                            'type': front.get('type', 'Unknown'),
                            'subType': front.get('subType'),
                            'side': card.get('side', 'Unknown'),
                            'gametext': front.get('gametext', ''),
                            'deploy': front.get('deploy'),
                            'destiny': front.get('destiny'),
                        }
    return card_db


def parse_deck_file(filepath):
    """Parse a deck XML file and return list of blueprint IDs."""
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
        cards = []
        for card_elem in root.findall('.//card'):
            blueprint_id = card_elem.get('blueprintId')
            if blueprint_id:
                cards.append(blueprint_id)
        return cards
    except Exception as e:
        print(f"Error parsing {filepath}: {e}")
        return []


def analyze_decks():
    """Analyze all decks and count card occurrences."""
    decks_dir = Path("/opt/gemp/rando_cal_working/decks")
    card_db = load_card_database()

    # Count cards across all decks
    card_counts = defaultdict(int)  # blueprint_id -> count
    deck_counts = defaultdict(int)  # blueprint_id -> number of decks containing it

    deck_files = list(decks_dir.glob("*.txt"))
    print(f"Found {len(deck_files)} deck files")

    for deck_file in deck_files:
        cards = parse_deck_file(deck_file)
        seen_in_deck = set()
        for card_id in cards:
            card_counts[card_id] += 1
            seen_in_deck.add(card_id)
        for card_id in seen_in_deck:
            deck_counts[card_id] += 1

    # Build results with card info
    results = []
    for blueprint_id, total_count in card_counts.items():
        card_info = card_db.get(blueprint_id, {})
        results.append({
            'blueprint_id': blueprint_id,
            'title': card_info.get('title', f'Unknown ({blueprint_id})'),
            'type': card_info.get('type', 'Unknown'),
            'subType': card_info.get('subType'),
            'side': card_info.get('side', 'Unknown'),
            'total_copies': total_count,
            'deck_count': deck_counts[blueprint_id],
            'gametext': card_info.get('gametext', ''),
        })

    return results, len(deck_files)


def print_results(results, total_decks):
    """Print results organized by card type."""
    # Define type priority (interrupts and effects first)
    type_priority = {
        'Interrupt': 0,
        'Effect': 1,
        'Character': 2,
        'Starship': 3,
        'Vehicle': 4,
        'Weapon': 5,
        'Device': 6,
        'Location': 7,
        'Objective': 8,
        'Unknown': 99,
    }

    # Group by type
    by_type = defaultdict(list)
    for r in results:
        by_type[r['type']].append(r)

    # Sort each type by deck_count (how many decks have it)
    for card_type in by_type:
        by_type[card_type].sort(key=lambda x: (-x['deck_count'], -x['total_copies']))

    # Print organized output
    print(f"\n{'='*80}")
    print(f"DECK ANALYSIS REPORT - {total_decks} decks analyzed")
    print(f"{'='*80}")

    # Sort types by priority
    sorted_types = sorted(by_type.keys(), key=lambda t: type_priority.get(t, 50))

    for card_type in sorted_types:
        cards = by_type[card_type]
        if not cards:
            continue

        print(f"\n{'='*80}")
        print(f" {card_type.upper()}S ({len(cards)} unique)")
        print(f"{'='*80}")

        # Show top cards of this type
        top_n = 30 if card_type in ['Interrupt', 'Effect'] else 20
        for card in cards[:top_n]:
            pct = (card['deck_count'] / total_decks) * 100
            side_marker = "LS" if card['side'] == 'Light' else "DS"
            subtype_str = f" [{card['subType']}]" if card['subType'] else ""
            print(f"  {card['deck_count']:3d} decks ({pct:5.1f}%) | {card['total_copies']:4d} copies | [{side_marker}] {card['blueprint_id']:10s} | {card['title']}{subtype_str}")

        if len(cards) > top_n:
            print(f"  ... and {len(cards) - top_n} more")

    # Print summary stats
    print(f"\n{'='*80}")
    print(" SUMMARY BY TYPE")
    print(f"{'='*80}")
    for card_type in sorted_types:
        cards = by_type[card_type]
        total_copies = sum(c['total_copies'] for c in cards)
        avg_per_deck = total_copies / total_decks if total_decks > 0 else 0
        print(f"  {card_type:15s}: {len(cards):4d} unique cards, {total_copies:5d} total copies ({avg_per_deck:.1f} avg/deck)")


def export_interrupt_effect_list(results, total_decks):
    """Export a focused list of interrupts and effects for custom handling."""
    interrupts_effects = [r for r in results if r['type'] in ('Interrupt', 'Effect')]
    interrupts_effects.sort(key=lambda x: (-x['deck_count'], -x['total_copies']))

    print(f"\n{'='*80}")
    print(" TOP INTERRUPTS & EFFECTS FOR CUSTOM HANDLING")
    print(f"{'='*80}")
    print("\nThese cards appear in many decks and would benefit from custom AI logic:\n")

    for i, card in enumerate(interrupts_effects[:50], 1):
        pct = (card['deck_count'] / total_decks) * 100
        side_marker = "LS" if card['side'] == 'Light' else "DS"
        subtype = card['subType'] or card['type']
        print(f"{i:2d}. [{side_marker}] {card['title']}")
        print(f"    Blueprint: {card['blueprint_id']} | Type: {subtype} | In {card['deck_count']} decks ({pct:.0f}%)")
        if card['gametext']:
            # Truncate gametext for readability
            gt = card['gametext'][:200] + "..." if len(card['gametext']) > 200 else card['gametext']
            print(f"    Gametext: {gt}")
        print()


if __name__ == '__main__':
    results, total_decks = analyze_decks()
    print_results(results, total_decks)
    export_interrupt_effect_list(results, total_decks)
