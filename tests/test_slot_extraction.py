"""
tests/test_slot_extraction.py

Unit tests for the deterministic date/time extractor that backs activity
drafting.

Why this exists: filling the date and time of a half-finished activity used to
depend entirely on the model, which answered CHAT for a plain "Hari Minggu,
11 Oktober 2026" and stranded the user mid-flow. The clock is interchangeable,
the schedule is not, so these are parsed locally and never need a model.

Run:
    python -m unittest tests.test_slot_extraction -v
"""

import unittest
from datetime import date
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from bot.handlers.message_handler import (  # noqa: E402
    _extract_date,
    _extract_time,
    _fill_slots_locally,
    _is_pure_schedule_answer,
    _merge_draft,
    _merge_generic_draft,
)
from services.intent_service import (  # noqa: E402
    ACTION_ADD_ACTIVITY,
    Intent,
)

# A Sunday, chosen so weekday resolution is unambiguous.
TODAY = date(2026, 10, 11)


class ExtractTimeTests(unittest.TestCase):
    def test_clock_phrases(self) -> None:
        for text, expected in [
            ("Jam 2 sore ginchiee", "14:00"),
            ("jam 3 sore", "15:00"),
            ("jam 10 pagi", "10:00"),
            ("jam 7 malam", "19:00"),
            ("pukul 5 sore", "17:00"),
            ("jam 5 subuh", "05:00"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(_extract_time(text), expected)

    def test_numeric_times(self) -> None:
        for text, expected in [
            ("jam 09:30", "09:30"),
            ("14:00", "14:00"),
            ("jam 23.15", "23:15"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(_extract_time(text), expected)

    def test_no_time_returns_none(self) -> None:
        self.assertIsNone(_extract_time("aku ada rapat"))
        self.assertIsNone(_extract_time("besok ya"))

    def test_pm_words_shift_late_hours(self) -> None:
        # "sore" must push a morning-shaped hour into the afternoon.
        self.assertEqual(_extract_time("jam 11 sore"), "23:00")
        self.assertEqual(_extract_time("jam 11 pagi"), "11:00")


class ExtractDateTests(unittest.TestCase):
    def test_long_date(self) -> None:
        self.assertEqual(
            _extract_date("Hari Minggu, 11 Oktober 2026", TODAY), "2026-10-11"
        )

    def test_day_month_without_year(self) -> None:
        self.assertEqual(_extract_date("11 Oktober", TODAY), "2026-10-11")
        self.assertEqual(_extract_date("3 Desember", TODAY), "2026-12-03")

    def test_numeric_dates(self) -> None:
        self.assertEqual(_extract_date("11/10", TODAY), "2026-10-11")
        self.assertEqual(_extract_date("11-10", TODAY), "2026-10-11")
        self.assertEqual(_extract_date("11-10-2026", TODAY), "2026-10-11")

    def test_relative_days(self) -> None:
        self.assertEqual(_extract_date("hari ini", TODAY), "2026-10-11")
        self.assertEqual(_extract_date("besok", TODAY), "2026-10-12")
        self.assertEqual(_extract_date("lusa", TODAY), "2026-10-13")

    def test_weekday_names_resolve_to_next_occurrence(self) -> None:
        # TODAY is Sunday 11 Oct 2026.
        self.assertEqual(_extract_date("hari jumat", TODAY), "2026-10-16")
        self.assertEqual(_extract_date("hari senin", TODAY), "2026-10-12")

    def test_weekday_never_returns_today_or_past(self) -> None:
        """A named day always means a future one, so a reminder is never lost."""
        result = _extract_date("minggu", TODAY)
        self.assertEqual(result, "2026-10-18")
        self.assertGreater(result, TODAY.isoformat())

    def test_no_date_returns_none(self) -> None:
        self.assertIsNone(_extract_date("jam 2 sore", TODAY))
        self.assertIsNone(_extract_date("mau ngechat pacar", TODAY))


class FillSlotsTests(unittest.TestCase):
    """The exact production sequence that used to break."""

    def test_bare_instruction_then_date_then_time(self) -> None:
        draft = {
            "slots": {"title": "", "date": "", "time": ""},
            "missing": ["title", "date", "time"],
        }

        # 1. "Masukkin ke jadwal kegiatan ginchiee" -> nothing parseable yet.
        draft = _fill_slots_locally(draft, "Masukkin ke jadwal kegiatan ginchiee")
        self.assertEqual(draft["slots"]["date"], "")
        self.assertEqual(draft["slots"]["time"], "")
        self.assertEqual(sorted(draft["missing"]), ["date", "time", "title"])

        # 2. Date-only reply, which the model used to answer CHAT for.
        draft = _fill_slots_locally(draft, "Hari Minggu, 11 Oktober 2026")
        self.assertEqual(draft["slots"]["date"], "2026-10-11")
        self.assertEqual(draft["slots"]["time"], "")
        self.assertNotIn("date", draft["missing"])

        # 3. Time-only reply.
        draft = _fill_slots_locally(draft, "Jam 2 sore ginchiee")
        self.assertEqual(draft["slots"]["time"], "14:00")

        # Only the title is still outstanding, so the bot asks for just that.
        self.assertEqual(draft["missing"], ["title"])

    def test_does_not_overwrite_existing_slots(self) -> None:
        draft = {
            "slots": {"title": "rapat", "date": "2026-10-12", "time": ""},
            "missing": ["time"],
        }
        draft = _fill_slots_locally(draft, "besok jam 9 pagi")
        # Date already set, so "besok" must not replace 2026-10-12.
        self.assertEqual(draft["slots"]["date"], "2026-10-12")
        self.assertEqual(draft["slots"]["time"], "09:00")
        self.assertEqual(draft["missing"], [])

    def test_never_uses_an_old_activity_title(self) -> None:
        """The extractor cannot invent a title, so old titles cannot leak."""
        draft = {
            "slots": {"title": "", "date": "", "time": ""},
            "missing": ["title", "date", "time"],
        }
        draft = _fill_slots_locally(draft, "Hari Minggu, 11 Oktober 2026")
        draft = _fill_slots_locally(draft, "Jam 2 sore ginchiee")
        self.assertEqual(draft["slots"]["title"], "")
        self.assertIn("title", draft["missing"])


class TitleLeakGuardTests(unittest.TestCase):
    """A bare schedule answer must never be able to supply an activity title.

    Regression for a real production bug: the user was mid-draft and answered
    "Jam 9 pagi ginchiee" while the model copied the title of an activity that
    had just been saved, so a brand-new event appeared under the old name.
    """

    def test_pure_schedule_answers(self) -> None:
        for text in [
            "Jam 9 pagi ginchiee",
            "jam 3 sore",
            "pukul 5 sore",
            "Hari Minggu, 11 Oktober 2026",
            "11/10",
            "besok",
            "hari jumat",
            "14:30",
        ]:
            with self.subTest(text=text):
                self.assertTrue(_is_pure_schedule_answer(text), text)

    def test_messages_that_do_carry_a_title(self) -> None:
        for text in [
            "ya betul, exam software engineer",
            "rapat sama tim ya",
            "judulnya ngechat pacar",
            "aku ada meeting sama tim jam 9 pagi",
            "janji sama pacar",
        ]:
            with self.subTest(text=text):
                self.assertFalse(_is_pure_schedule_answer(text), text)

    def test_merge_refuses_a_title_from_a_pure_answer(self) -> None:
        draft = {
            "slots": {"title": "", "date": "2026-10-12", "time": ""},
            "missing": ["title", "time"],
        }
        intent = Intent(
            action=ACTION_ADD_ACTIVITY,
            slots={"title": "Tugas Internship", "time": "09:00"},
            missing=[],
        )
        merged = _merge_draft(draft, intent, "Jam 9 pagi ginchiee")
        self.assertEqual(merged["slots"]["title"], "", "judul lama tidak boleh masuk")
        self.assertEqual(merged["slots"]["time"], "09:00")
        self.assertIn("title", merged["missing"])

    def test_merge_accepts_a_genuine_new_title(self) -> None:
        draft = {
            "slots": {"title": "", "date": "2026-10-12", "time": "09:00"},
            "missing": ["title"],
        }
        intent = Intent(
            action=ACTION_ADD_ACTIVITY,
            slots={"title": "ngechat pacar"},
            missing=[],
        )
        merged = _merge_draft(draft, intent, "judulnya ngechat pacar")
        self.assertEqual(merged["slots"]["title"], "ngechat pacar")
        self.assertEqual(merged["missing"], [])

    def test_merge_still_fills_a_title_when_none_was_answered(self) -> None:
        """A normal add request carries its title in the opening message."""
        draft = {
            "slots": {"title": "", "date": "", "time": ""},
            "missing": ["title", "date", "time"],
        }
        intent = Intent(
            action=ACTION_ADD_ACTIVITY,
            slots={"title": "rapat", "date": "2026-10-13", "time": "10:00"},
            missing=[],
        )
        merged = _merge_draft(draft, intent, "aku ada rapat besok jam 10")
        self.assertEqual(merged["slots"]["title"], "rapat")
        self.assertEqual(merged["missing"], [])


class DraftDoesNotTrapOtherTopicsTests(unittest.TestCase):
    """A food-log request must never be swallowed by an open activity draft.

    Regression for a reported bug: the user had an activity draft open, then
    typed "aku makan sate 10 tusuk dan lontong" and "ginchie", and the bot kept
    asking for the activity's title/date/time instead of logging the food.
    """

    def test_food_log_is_not_a_schedule_answer(self) -> None:
        for text in [
            "aku makan sate 10 tusuk dan lontong",
            "hitungin kalor sate 10 tusuk dan lontong",
            "ginchie",
            "makasih ya ginchiee",
            "agenda aku apa aja",
        ]:
            with self.subTest(text=text):
                self.assertFalse(_is_pure_schedule_answer(text), text)

    def test_schedule_answers_still_count(self) -> None:
        for text in [
            "Hari Minggu, 11 Oktober 2026",
            "Jam 9 pagi ginchiee",
            "besok",
            "11/10",
        ]:
            with self.subTest(text=text):
                self.assertTrue(_is_pure_schedule_answer(text), text)

    def test_merge_generic_draft_fills_a_single_slot(self) -> None:
        from services.intent_service import ACTION_CHANGE_NAME

        draft = {
            "action": ACTION_CHANGE_NAME,
            "slots": {"name": ""},
            "missing": ["name"],
        }
        intent = Intent(action=ACTION_CHANGE_NAME, slots={"name": "Bella"}, missing=[])
        merged = _merge_generic_draft(draft, intent)
        self.assertEqual(merged["slots"]["name"], "Bella")
        self.assertEqual(merged["missing"], [])

    def test_merge_generic_draft_does_not_overwrite(self) -> None:
        from services.intent_service import ACTION_CHANGE_NAME

        draft = {
            "action": ACTION_CHANGE_NAME,
            "slots": {"name": "Ayyi"},
            "missing": [],
        }
        intent = Intent(action=ACTION_CHANGE_NAME, slots={"name": "Bella"}, missing=[])
        merged = _merge_generic_draft(draft, intent)
        self.assertEqual(merged["slots"]["name"], "Ayyi")


if __name__ == "__main__":
    unittest.main()
