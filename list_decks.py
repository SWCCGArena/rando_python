#!/usr/bin/env python3
"""Quick script to list available library decks."""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from config import config
from engine.client import GEMPClient

client = GEMPClient(config.GEMP_SERVER_URL)

username = os.environ.get('GEMP_USERNAME', config.GEMP_USERNAME)
password = os.environ.get('GEMP_PASSWORD', config.GEMP_PASSWORD)

if client.login(username, password):
    print(f"Logged in as {username}\n")

    decks = client.get_library_decks()

    dark_decks = [d for d in decks if getattr(d, 'side', None) == 'dark']
    light_decks = [d for d in decks if getattr(d, 'side', None) == 'light']

    print("=== DARK SIDE DECKS ===")
    for d in sorted(dark_decks, key=lambda x: x.name):
        print(f"  {d.name}")

    print(f"\n=== LIGHT SIDE DECKS ===")
    for d in sorted(light_decks, key=lambda x: x.name):
        print(f"  {d.name}")

    print(f"\nTotal: {len(dark_decks)} Dark, {len(light_decks)} Light")
else:
    print("Login failed!")
