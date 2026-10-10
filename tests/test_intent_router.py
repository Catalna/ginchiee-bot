"""
tests/test_intent_router.py

Unit tests for the intent router's parsing and safety rules. No network: these
pin down the contract that keeps a malformed or ambiguous model reply from
becoming a destructive database action.
"""

import unittest
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from services.intent_service import (  # noqa: E402
    ACTION_ADD_ACTIVITY,
    ACTION_CHANGE_NAME,
    ACTION_CHAT,
    ACTION_CORRECT_FOOD,
    ACTION_DELETE_ACTIVITY,
    ACTION_DELETE_FOOD,
    ACTION_LOG_FOOD,
    ACTION_MARK_DONE,
    Intent,
    compute_missing,
    describe_intent,
    parse_intent,
)


class ParseIntentTests(unittest.TestCase):
    def test_valid_chat(self) -> None:
        intent = parse_intent({
            "action": "CHAT", "slots": {}, "missing": [],
            "reply": "halo juga!", "confidence": 0.99,
        })
        self.assertEqual(intent.action, ACTION_CHAT)
        self.assertTrue(intent.is_chat)
        self.assertFalse(intent.needs_confirmation)

    def test_unknown_action_falls_back_to_chat(self) -> None:
        intent = parse_intent({"action": "DESTROY_EVERYTHING", "slots": {}})
        self.assertEqual(intent.action, ACTION_CHAT)

    def test_non_dict_payload_returns_none(self) -> None:
        self.assertIsNone(parse_intent([]))
        self.assertIsNone(parse_intent("CHAT"))
        self.assertIsNone(parse_intent(None))

    def test_delete_without_target_demoted_to_chat(self) -> None:
        """A deletion that cannot name its target must not execute."""
        intent = parse_intent({"action": "DELETE_FOOD", "slots": {"target": ""}})
        self.assertEqual(intent.action, ACTION_CHAT)

        intent = parse_intent({"action": "DELETE_ACTIVITY", "slots": {"target": ""}})
        self.assertEqual(intent.action, ACTION_CHAT)

        intent = parse_intent({"action": "CORRECT_FOOD", "slots": {"target": "nasi goreng"}})
        self.assertEqual(intent.action, ACTION_CHAT)

    def test_log_food_without_foods_demoted_to_chat(self) -> None:
        intent = parse_intent({"action": "LOG_FOOD", "slots": {"foods": []}})
        self.assertEqual(intent.action, ACTION_CHAT)

    def test_food_list_is_validated_and_cleaned(self) -> None:
        intent = parse_intent({
            "action": "LOG_FOOD",
            "slots": {"foods": [
                {"name": "nasi goreng", "amount_g": "200", "calories": "320"},
                {"name": ""},                      # dropped: no name
                {"name": "ayam"},                   # keeps defaults
                "not-a-dict",                       # dropped
            ]},
        })
        foods = intent.slots["foods"]
        self.assertEqual(len(foods), 2)
        self.assertEqual(foods[0]["amount_g"], 200.0)
        self.assertEqual(foods[0]["calories"], 320.0)
        self.assertEqual(foods[1]["amount_g"], 100.0)  # sane default
        self.assertEqual(foods[1]["calories"], 0.0)

    def test_numeric_coercion_is_safe(self) -> None:
        intent = parse_intent({
            "action": "LOG_FOOD",
            "slots": {"foods": [
                {"name": "es teh", "amount_g": None, "calories": "bukan angka"},
            ]},
        })
        food = intent.slots["foods"][0]
        self.assertEqual(food["amount_g"], 100.0)
        self.assertEqual(food["calories"], 0.0)


class MissingSlotsTests(unittest.TestCase):
    def test_add_activity_reports_missing_slots(self) -> None:
        intent = Intent(
            action=ACTION_ADD_ACTIVITY,
            slots={"title": "meeting", "date": "", "time": ""},
        )
        compute_missing(intent)
        self.assertEqual(sorted(intent.missing), ["date", "time"])
        self.assertFalse(intent.is_complete)

    def test_add_activity_complete(self) -> None:
        intent = Intent(
            action=ACTION_ADD_ACTIVITY,
            slots={"title": "meeting", "date": "2026-10-12", "time": "10:00"},
        )
        compute_missing(intent)
        self.assertEqual(intent.missing, [])
        self.assertTrue(intent.is_complete)

    def test_chat_never_has_missing_slots(self) -> None:
        intent = Intent(action=ACTION_CHAT, slots={})
        compute_missing(intent)
        self.assertEqual(intent.missing, [])


class ConfirmationPolicyTests(unittest.TestCase):
    """Logging food must be immediate; everything destructive must confirm."""

    def test_log_food_does_not_need_confirmation(self) -> None:
        intent = Intent(action=ACTION_LOG_FOOD, slots={"foods": [{"name": "nasi"}]})
        self.assertFalse(intent.needs_confirmation)

    def test_destructive_actions_need_confirmation(self) -> None:
        for action in (
            ACTION_DELETE_FOOD,
            ACTION_CORRECT_FOOD,
            ACTION_DELETE_ACTIVITY,
            ACTION_MARK_DONE,
            ACTION_CHANGE_NAME,
        ):
            with self.subTest(action=action):
                self.assertTrue(Intent(action=action, slots={}).needs_confirmation)


class DescribeIntentTests(unittest.TestCase):
    def test_summaries_are_human_readable(self) -> None:
        intent = Intent(
            action=ACTION_LOG_FOOD,
            slots={"foods": [{"name": "nasi goreng", "calories": 320}]},
        )
        self.assertIn("nasi goreng", describe_intent(intent))
        self.assertIn("320", describe_intent(intent))

    def test_change_name_summary(self) -> None:
        intent = Intent(action=ACTION_CHANGE_NAME, slots={"name": "Bella"})
        self.assertIn("Bella", describe_intent(intent))


if __name__ == "__main__":
    unittest.main()
