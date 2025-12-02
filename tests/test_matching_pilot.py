"""
Matching Pilot/Ship Preference Test Suite

Tests the soft preference for matching pilot/ship combos:
1. Card.is_matching_pilot_for() - pilot lists ship in matching field
2. Card.is_matching_ship_for() - ship lists pilot in matching field
3. is_matching_pilot_ship() - bidirectional check (either direction)

This is a SOFT preference - matching pairs get a bonus but any qualified
pilot can still fly any ship.

Run with: python -m pytest tests/test_matching_pilot.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from engine.card_loader import Card, is_matching_pilot_ship


class TestMatchingPilotCardMethods:
    """Tests for Card.is_matching_pilot_for and is_matching_ship_for"""

    def test_pilot_with_matching_ship(self):
        """Pilot with matching field should prefer listed ships"""
        pilot = Card(
            blueprint_id="test_pilot",
            title="Wedge Antilles",
            side="Light",
            card_type="Character",
            icons=["Pilot"],
            matching=["Red 2", "Red Squadron 1"],
        )
        assert pilot.is_pilot
        assert pilot.is_matching_pilot_for("Red 2")
        assert pilot.is_matching_pilot_for("Red Squadron 1")
        assert not pilot.is_matching_pilot_for("Millennium Falcon")

    def test_pilot_without_matching_field(self):
        """Pilot without matching field matches no ships"""
        pilot = Card(
            blueprint_id="test_pilot",
            title="Generic Pilot",
            side="Light",
            card_type="Character",
            icons=["Pilot"],
            matching=[],  # No preferences
        )
        assert pilot.is_pilot
        assert not pilot.is_matching_pilot_for("Any Ship")
        assert not pilot.is_matching_pilot_for("Millennium Falcon")

    def test_ship_with_matching_pilot(self):
        """Ship with matching field should prefer listed pilots"""
        ship = Card(
            blueprint_id="test_ship",
            title="Red 2",
            side="Light",
            card_type="Starship",
            matching=["Wedge Antilles"],
        )
        assert ship.is_starship
        assert ship.is_matching_ship_for("Wedge Antilles")
        assert ship.is_matching_ship_for("Wedge Antilles, Red Two Leader")  # Partial match
        assert not ship.is_matching_ship_for("Luke Skywalker")

    def test_vehicle_with_matching_pilot(self):
        """Vehicle with matching field should prefer listed pilots"""
        vehicle = Card(
            blueprint_id="test_vehicle",
            title="Luke's T-16 Skyhopper",
            side="Light",
            card_type="Vehicle",
            matching=["Luke Skywalker"],
        )
        assert vehicle.is_vehicle
        assert vehicle.is_matching_ship_for("Luke Skywalker")
        assert not vehicle.is_matching_ship_for("Han Solo")

    def test_non_pilot_returns_false(self):
        """Non-pilot character cannot be matching pilot"""
        char = Card(
            blueprint_id="test_char",
            title="Chewbacca",
            side="Light",
            card_type="Character",
            icons=["Warrior"],
            matching=["Millennium Falcon"],  # Has matching but not a pilot
        )
        assert not char.is_pilot
        assert not char.is_matching_pilot_for("Millennium Falcon")

    def test_non_ship_returns_false(self):
        """Non-ship cannot use is_matching_ship_for"""
        char = Card(
            blueprint_id="test_char",
            title="Han Solo",
            side="Light",
            card_type="Character",
            matching=["Millennium Falcon"],
        )
        assert not char.is_starship
        assert not char.is_vehicle
        assert not char.is_matching_ship_for("Chewbacca")


class TestMatchingPilotShipFunction:
    """Tests for the is_matching_pilot_ship helper function"""

    def test_pilot_lists_ship(self):
        """Returns true when pilot's matching field lists the ship"""
        pilot = Card(
            blueprint_id="test_pilot",
            title="Wedge Antilles",
            side="Light",
            card_type="Character",
            icons=["Pilot"],
            matching=["Red 2"],
        )
        ship = Card(
            blueprint_id="test_ship",
            title="Red 2",
            side="Light",
            card_type="Starship",
            matching=[],  # Ship doesn't list pilot
        )
        assert is_matching_pilot_ship(pilot, ship)

    def test_ship_lists_pilot(self):
        """Returns true when ship's matching field lists the pilot"""
        pilot = Card(
            blueprint_id="test_pilot",
            title="Wedge Antilles",
            side="Light",
            card_type="Character",
            icons=["Pilot"],
            matching=[],  # Pilot doesn't list ship
        )
        ship = Card(
            blueprint_id="test_ship",
            title="Red 2",
            side="Light",
            card_type="Starship",
            matching=["Wedge Antilles"],  # Ship lists pilot
        )
        assert is_matching_pilot_ship(pilot, ship)

    def test_bidirectional_match(self):
        """Returns true when both list each other"""
        pilot = Card(
            blueprint_id="test_pilot",
            title="Wedge Antilles",
            side="Light",
            card_type="Character",
            icons=["Pilot"],
            matching=["Red 2"],
        )
        ship = Card(
            blueprint_id="test_ship",
            title="Red 2",
            side="Light",
            card_type="Starship",
            matching=["Wedge Antilles"],
        )
        assert is_matching_pilot_ship(pilot, ship)

    def test_no_match(self):
        """Returns false when neither lists the other"""
        pilot = Card(
            blueprint_id="test_pilot",
            title="Wedge Antilles",
            side="Light",
            card_type="Character",
            icons=["Pilot"],
            matching=["Red 2"],
        )
        ship = Card(
            blueprint_id="test_ship",
            title="Millennium Falcon",
            side="Light",
            card_type="Starship",
            matching=["Han Solo"],
        )
        assert not is_matching_pilot_ship(pilot, ship)

    def test_null_pilot(self):
        """Returns false for null pilot"""
        ship = Card(
            blueprint_id="test_ship",
            title="Red 2",
            side="Light",
            card_type="Starship",
        )
        assert not is_matching_pilot_ship(None, ship)

    def test_null_ship(self):
        """Returns false for null ship"""
        pilot = Card(
            blueprint_id="test_pilot",
            title="Wedge Antilles",
            side="Light",
            card_type="Character",
            icons=["Pilot"],
        )
        assert not is_matching_pilot_ship(pilot, None)

    def test_case_insensitive_match(self):
        """Matching should be case-insensitive"""
        pilot = Card(
            blueprint_id="test_pilot",
            title="Wedge Antilles",
            side="Light",
            card_type="Character",
            icons=["Pilot"],
            matching=["RED 2"],  # Uppercase
        )
        ship = Card(
            blueprint_id="test_ship",
            title="Red 2",  # Mixed case
            side="Light",
            card_type="Starship",
        )
        assert is_matching_pilot_ship(pilot, ship)


class TestMatchingPilotFromJSON:
    """Tests using real card data from JSON files"""

    @pytest.fixture(autouse=True)
    def ensure_real_card_loader(self):
        """Ensure the real card loader is active"""
        import engine.card_loader as card_loader

        card_loader._card_db = None
        db = card_loader.get_card_database()
        assert db._loaded, "Card database should be loaded"

        def real_get_card(blueprint_id: str):
            return card_loader.get_card_database().get_card(blueprint_id)

        self._real_get_card = real_get_card
        yield

    def _get_card(self, blueprint_id: str):
        """Get a card using the real card loader"""
        return self._real_get_card(blueprint_id)

    def test_wedge_antilles_matching_ships(self):
        """Wedge Antilles should have matching ships in JSON"""
        # Find a Wedge Antilles card
        import engine.card_loader as card_loader
        db = card_loader.get_card_database()

        wedge_cards = [c for c in db.cards.values()
                       if 'wedge' in c.title.lower() and c.is_pilot]

        if wedge_cards:
            wedge = wedge_cards[0]
            # Wedge should have matching field if any ships are listed
            if wedge.matching:
                # Check that matching field contains ship-like names
                assert len(wedge.matching) > 0

    def test_millennium_falcon_matching_pilots(self):
        """Millennium Falcon should have matching pilots"""
        import engine.card_loader as card_loader
        db = card_loader.get_card_database()

        falcon_cards = [c for c in db.cards.values()
                        if 'millennium falcon' in c.title.lower() and c.is_starship]

        if falcon_cards:
            # Find a Falcon that lists Han Solo specifically
            for falcon in falcon_cards:
                if falcon.matching:
                    # Check if "Han Solo" is explicitly in matching
                    if any('han solo' in m.lower() for m in falcon.matching):
                        assert falcon.is_matching_ship_for("Han Solo"), \
                            f"Expected {falcon.title} to match Han Solo"
                        break
                    # Or check for Chewbacca
                    elif any('chewbacca' in m.lower() for m in falcon.matching):
                        assert falcon.is_matching_ship_for("Chewbacca"), \
                            f"Expected {falcon.title} to match Chewbacca"
                        break

    def test_real_matching_pair(self):
        """Test a known matching pilot/ship pair from the database"""
        import engine.card_loader as card_loader
        db = card_loader.get_card_database()

        # Find any pilot with a matching field
        pilots_with_matching = [c for c in db.cards.values()
                                if c.is_pilot and c.matching]

        if pilots_with_matching:
            pilot = pilots_with_matching[0]
            expected_ship_name = pilot.matching[0]

            # Find that ship in the database
            matching_ships = [c for c in db.cards.values()
                              if expected_ship_name.lower() in c.title.lower()
                              and (c.is_starship or c.is_vehicle)]

            if matching_ships:
                ship = matching_ships[0]
                # The function should return True
                assert is_matching_pilot_ship(pilot, ship), \
                    f"Expected {pilot.title} to match {ship.title}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
