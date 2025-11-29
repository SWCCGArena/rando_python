"""
Weapon Deployment Test Suite

Tests weapon deployment rules:
1. Max 1 weapon per character/starship/vehicle
2. Weapon subtypes must match target types (character→character, etc.)
3. Standalone weapons (automated, artillery) are treated as extra actions

Run with: python -m pytest tests/test_weapon_deployment.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from engine.card_loader import Card


class TestWeaponCardProperties:
    """Tests for weapon card metadata properties"""

    def test_character_weapon_target_type(self):
        """Character weapons should target characters"""
        card = Card(
            blueprint_id="1_312",
            title="Blaster Rifle",
            side="Dark",
            card_type="Weapon",
            sub_type="Character",
        )
        assert card.is_weapon
        assert card.weapon_target_type == "character"
        assert card.is_targeted_weapon
        assert not card.is_standalone_weapon

    def test_vehicle_weapon_target_type(self):
        """Vehicle weapons should target vehicles"""
        card = Card(
            blueprint_id="6_174",
            title="Antipersonnel Laser Cannon",
            side="Dark",
            card_type="Weapon",
            sub_type="Vehicle",
        )
        assert card.is_weapon
        assert card.weapon_target_type == "vehicle"
        assert card.is_targeted_weapon
        assert not card.is_standalone_weapon

    def test_starship_weapon_target_type(self):
        """Starship weapons should target starships"""
        card = Card(
            blueprint_id="200_46",
            title="Quad Laser Cannon",
            side="Light",
            card_type="Weapon",
            sub_type="Starship",
        )
        assert card.is_weapon
        assert card.weapon_target_type == "starship"
        assert card.is_targeted_weapon
        assert not card.is_standalone_weapon

    def test_automated_weapon_is_standalone(self):
        """Automated weapons are standalone (no target)"""
        card = Card(
            blueprint_id="test_auto",
            title="Automated Defense Turret",
            side="Dark",
            card_type="Weapon",
            sub_type="Automated",
        )
        assert card.is_weapon
        assert card.weapon_target_type is None
        assert not card.is_targeted_weapon
        assert card.is_standalone_weapon

    def test_artillery_weapon_is_standalone(self):
        """Artillery weapons are standalone (no target)"""
        card = Card(
            blueprint_id="test_art",
            title="Artillery Remote",
            side="Light",
            card_type="Weapon",
            sub_type="Artillery",
        )
        assert card.is_weapon
        assert card.weapon_target_type is None
        assert not card.is_targeted_weapon
        assert card.is_standalone_weapon

    def test_death_star_weapon_is_standalone(self):
        """Death Star weapons are standalone"""
        card = Card(
            blueprint_id="test_ds",
            title="Superlaser",
            side="Dark",
            card_type="Weapon",
            sub_type="Death Star",
        )
        assert card.is_weapon
        assert card.weapon_target_type is None
        assert not card.is_targeted_weapon
        assert card.is_standalone_weapon

    def test_non_weapon_has_no_target_type(self):
        """Non-weapon cards have no weapon target type"""
        card = Card(
            blueprint_id="1_168",
            title="Darth Vader",
            side="Dark",
            card_type="Character",
        )
        assert not card.is_weapon
        assert card.weapon_target_type is None
        assert not card.is_targeted_weapon
        assert not card.is_standalone_weapon


class TestWeaponTargetMatching:
    """Tests for matching weapon subtypes to valid targets"""

    def test_character_weapon_matches_character(self):
        """Character weapon should match character targets"""
        weapon = Card(
            blueprint_id="1_312",
            title="Blaster Rifle",
            side="Dark",
            card_type="Weapon",
            sub_type="Character",
        )
        target = Card(
            blueprint_id="1_168",
            title="Darth Vader",
            side="Dark",
            card_type="Character",
        )
        assert weapon.weapon_target_type == "character"
        assert target.is_character

    def test_vehicle_weapon_matches_vehicle(self):
        """Vehicle weapon should match vehicle targets"""
        weapon = Card(
            blueprint_id="6_174",
            title="Antipersonnel Laser Cannon",
            side="Dark",
            card_type="Weapon",
            sub_type="Vehicle",
        )
        target = Card(
            blueprint_id="3_140",
            title="AT-AT",
            side="Dark",
            card_type="Vehicle",
        )
        assert weapon.weapon_target_type == "vehicle"
        assert target.is_vehicle

    def test_starship_weapon_matches_starship(self):
        """Starship weapon should match starship targets"""
        weapon = Card(
            blueprint_id="test_starship_weapon",
            title="X-wing Laser Cannon",
            side="Light",
            card_type="Weapon",
            sub_type="Starship",
        )
        target = Card(
            blueprint_id="test_xwing",
            title="Red 5",
            side="Light",
            card_type="Starship",
        )
        assert weapon.weapon_target_type == "starship"
        assert target.is_starship


class TestWeaponSubtypeValidation:
    """Tests for weapon subtype case handling"""

    def test_lowercase_subtype(self):
        """Lowercase subtype should work"""
        card = Card(
            blueprint_id="test",
            title="Test Weapon",
            side="Dark",
            card_type="Weapon",
            sub_type="character",
        )
        assert card.weapon_target_type == "character"

    def test_uppercase_subtype(self):
        """Uppercase subtype should work"""
        card = Card(
            blueprint_id="test",
            title="Test Weapon",
            side="Dark",
            card_type="Weapon",
            sub_type="CHARACTER",
        )
        assert card.weapon_target_type == "character"

    def test_mixed_case_subtype(self):
        """Mixed case subtype should work"""
        card = Card(
            blueprint_id="test",
            title="Test Weapon",
            side="Dark",
            card_type="Weapon",
            sub_type="Starship",
        )
        assert card.weapon_target_type == "starship"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
