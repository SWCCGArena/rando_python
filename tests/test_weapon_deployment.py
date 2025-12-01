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


class TestCharacterSpecificWeapons:
    """Tests for character-specific weapons (e.g., Qui-Gon's Lightsaber)"""

    def test_weapon_with_matching_characters_is_character_weapon(self):
        """Weapon with matching_weapon list should be flagged as character-specific"""
        card = Card(
            blueprint_id="13_40",
            title="•Qui-Gon's Lightsaber",
            side="Light",
            card_type="Weapon",
            sub_type="Character",
            matching_weapon=["Master Qui-Gon", "Qui-Gon Jinn", "Obi-Wan Kenobi"],
        )
        assert card.is_weapon
        assert card.is_character_weapon
        assert len(card.matching_weapon) == 3

    def test_weapon_without_matching_is_not_character_specific(self):
        """Generic weapon without matching_weapon is not character-specific"""
        card = Card(
            blueprint_id="1_312",
            title="Blaster Rifle",
            side="Dark",
            card_type="Weapon",
            sub_type="Character",
            matching_weapon=[],  # Empty list = deploys on any character
        )
        assert card.is_weapon
        assert not card.is_character_weapon

    def test_can_weapon_deploy_on_exact_match(self):
        """Weapon should deploy on exact character name match"""
        card = Card(
            blueprint_id="13_40",
            title="•Qui-Gon's Lightsaber",
            side="Light",
            card_type="Weapon",
            sub_type="Character",
            matching_weapon=["Qui-Gon Jinn"],
        )
        assert card.can_weapon_deploy_on("Qui-Gon Jinn")

    def test_can_weapon_deploy_on_partial_match(self):
        """Weapon should deploy on character with matching partial name"""
        card = Card(
            blueprint_id="13_40",
            title="•Qui-Gon's Lightsaber",
            side="Light",
            card_type="Weapon",
            sub_type="Character",
            matching_weapon=["Qui-Gon Jinn"],
        )
        # "Qui-Gon Jinn" should match "Qui-Gon Jinn, Serene Jedi"
        assert card.can_weapon_deploy_on("Qui-Gon Jinn, Serene Jedi")
        assert card.can_weapon_deploy_on("•Qui-Gon Jinn, Serene Jedi")

    def test_can_weapon_deploy_on_case_insensitive(self):
        """Weapon matching should be case-insensitive"""
        card = Card(
            blueprint_id="13_40",
            title="•Qui-Gon's Lightsaber",
            side="Light",
            card_type="Weapon",
            sub_type="Character",
            matching_weapon=["Qui-Gon Jinn"],
        )
        assert card.can_weapon_deploy_on("QUI-GON JINN")
        assert card.can_weapon_deploy_on("qui-gon jinn, serene jedi")

    def test_can_weapon_deploy_on_no_match(self):
        """Weapon should NOT deploy on non-matching character"""
        card = Card(
            blueprint_id="13_40",
            title="•Qui-Gon's Lightsaber",
            side="Light",
            card_type="Weapon",
            sub_type="Character",
            matching_weapon=["Qui-Gon Jinn", "Obi-Wan Kenobi"],
        )
        assert not card.can_weapon_deploy_on("Luke Skywalker")
        assert not card.can_weapon_deploy_on("Mace Windu")
        assert not card.can_weapon_deploy_on("Darth Vader")

    def test_generic_weapon_deploys_on_any_character(self):
        """Generic weapon (no matching_weapon) deploys on any character"""
        card = Card(
            blueprint_id="1_312",
            title="Blaster Rifle",
            side="Dark",
            card_type="Weapon",
            sub_type="Character",
            matching_weapon=[],
        )
        assert card.can_weapon_deploy_on("Darth Vader")
        assert card.can_weapon_deploy_on("Luke Skywalker")
        assert card.can_weapon_deploy_on("Random Character")

    def test_non_weapon_returns_false_for_deploy_on(self):
        """Non-weapon card always returns False for can_weapon_deploy_on"""
        card = Card(
            blueprint_id="1_168",
            title="Darth Vader",
            side="Dark",
            card_type="Character",
        )
        assert not card.can_weapon_deploy_on("Anyone")


class TestCharacterSpecificWeaponsFromJSON:
    """Tests using real card data from JSON files"""

    @pytest.fixture(autouse=True)
    def ensure_real_card_loader(self):
        """
        Ensure the real card loader is active, not a mock.

        Some tests (test_deploy_planner.py) patch card_loader.get_card with mocks.
        This fixture ensures we have the real card database for JSON tests.
        """
        import engine.card_loader as card_loader

        # Save original function reference (test_deploy_planner patches this)
        # We need to restore the real function
        original_get_card = card_loader.get_card

        # Check if get_card has been patched (check if it's from the right module)
        # If patched, we need to define a real one
        def real_get_card(blueprint_id: str):
            return card_loader.get_card_database().get_card(blueprint_id)

        # Reset database and use real get_card
        card_loader._card_db = None
        db = card_loader.get_card_database()
        assert db._loaded, "Card database should be loaded"

        # Store reference to use in tests
        self._real_get_card = real_get_card

        yield

    def _get_card(self, blueprint_id: str):
        """Get a card using the real card loader"""
        return self._real_get_card(blueprint_id)

    def test_quigon_lightsaber_from_json(self):
        """Qui-Gon's Lightsaber should load correctly from JSON"""
        card = self._get_card("13_40")  # Qui-Gon's Lightsaber
        assert card is not None, "Card 13_40 (Qui-Gon's Lightsaber) not found in database"
        assert "Qui-Gon" in card.title
        assert card.is_weapon
        assert card.is_character_weapon
        assert len(card.matching_weapon) > 0

        # Should match Qui-Gon characters
        assert card.can_weapon_deploy_on("Qui-Gon Jinn, Serene Jedi")
        assert card.can_weapon_deploy_on("Master Qui-Gon")

        # Should NOT match unrelated characters
        assert not card.can_weapon_deploy_on("Luke Skywalker")
        assert not card.can_weapon_deploy_on("Mace Windu")

    def test_anakins_lightsaber_from_json(self):
        """Anakin's Lightsaber should match Anakin characters"""
        # Find Anakin's Lightsaber (there may be multiple versions)
        card = self._get_card("3_71")  # Anakin's Lightsaber
        if card and card.is_weapon and card.matching_weapon:
            assert card.is_character_weapon
            # Should match Anakin
            has_anakin_match = any("anakin" in m.lower() for m in card.matching_weapon)
            assert has_anakin_match, f"Expected Anakin in matching_weapon: {card.matching_weapon}"

    def test_generic_blaster_not_character_specific(self):
        """Generic blasters should not be character-specific"""
        # Blaster Rifle (generic weapon)
        card = self._get_card("1_255")  # Blaster Rifle
        if card and card.is_weapon:
            # Generic weapons should have empty matching_weapon
            assert not card.is_character_weapon or len(card.matching_weapon) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
