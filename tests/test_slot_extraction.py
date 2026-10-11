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


if __name__ == "__main__":
    unittest.main()
