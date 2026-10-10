"""
tests/test_personality.py

Unit tests for the persona guard. These are deterministic (no LLM, no network)
so they run in milliseconds and pin down the exact failure modes that make
Ginchiee sound like a generated assistant:

  * formal openers
  * "Anda"/"saya" pronouns
  * bullet / numbered lists
  * emoji floods
  * essay-length replies
  * unescaped Markdown characters that make Telegram reject the message
"""

import unittest
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from services.personality import (  # noqa: E402
    anti_examples_block,
    few_shot_as_history,
    sanitize_reply,
    strip_markdown_for_telegram,
)


class PersonaStructureTests(unittest.TestCase):
    def test_few_shot_history_is_alternating(self) -> None:
        history = few_shot_as_history()
        self.assertTrue(history, "few-shot history is empty")
        self.assertEqual(len(history) % 2, 0, "history must be user/model pairs")
        for index, turn in enumerate(history):
            expected = "user" if index % 2 == 0 else "model"
            self.assertEqual(turn["role"], expected)
            self.assertTrue(turn["parts"][0], "empty few-shot turn")

    def test_anti_examples_are_labelled_as_wrong(self) -> None:
        block = anti_examples_block()
        self.assertIn("SALAH", block)
        self.assertIn("larangan", block.lower())
        self.assertIn("Semoga informasi ini bermanfaat", block)

    def test_persona_instruction_forbids_bot_speak(self) -> None:
        from services.personality import PERSONA_SYSTEM_INSTRUCTION

        for token in ("DILARANG", "bullet", "Anda", "Sebagai AI"):
            self.assertIn(token.lower(), PERSONA_SYSTEM_INSTRUCTION.lower())


class SanitizerTests(unittest.TestCase):
    def test_strips_formal_openers(self) -> None:
        for text in (
            "Tentu! aku catat ya",
            "Baik, aku bantu",
            "Halo! aku di sini",
            "Tentu saja! oke deh",
            "Berikut adalah daftarmu",
            "Sebagai AI aku bantu",
        ):
            out = sanitize_reply(text)
            self.assertNotIn("Tentu!", out, text)
            self.assertNotIn("Baik,", out, text)
            self.assertNotIn("Halo!", out, text)
            self.assertNotIn("Berikut adalah", out, text)
            self.assertNotIn("Sebagai AI", out, text)
            self.assertTrue(out.strip(), text)

    def test_removes_banned_closers(self) -> None:
        out = sanitize_reply("makanannya enak ya. Semoga membantu! jangan ragu bertanya")
        self.assertNotIn("semoga membantu", out.lower())
        self.assertNotIn("jangan ragu", out.lower())

    def test_normalises_pronouns(self) -> None:
        self.assertEqual(sanitize_reply("Anda perlu makan siang"), "kamu perlu makan siang")
        self.assertEqual(sanitize_reply("Saya sudah catat"), "aku sudah catat")

    def test_keeps_ordinary_word_maaf(self) -> None:
        # "maaf" must only be stripped as a salutation, never as a normal word.
        self.assertEqual(sanitize_reply("maaf ya aku bingung"), "maaf ya aku bingung")

    def test_strips_bullet_markers(self) -> None:
        out = sanitize_reply("- nasi goreng\n- ayam geprek")
        self.assertNotIn("- nasi", out)
        self.assertNotIn("- ayam", out)
        self.assertIn("nasi goreng", out)

    def test_limits_emoji(self) -> None:
        out = sanitize_reply("asik banget 😊✨💖🥰😆🎉")
        self.assertLessEqual(len(out) + 1, 60)
        self.assertNotIn("🎉", out)

    def test_truncates_long_reply_at_sentence(self) -> None:
        long_text = (
            "wah kamu udah makan nasi goreng sama ayam geprek? "
            "kamu harus banyak minum air putih ya. "
            "tidur juga jangan lupa biar badan segar. "
            "jangan makan gorengan terus nanti kurang baik. "
            "aku doain kamu sehat selalu ya."
        )
        out = sanitize_reply(long_text)
        self.assertLessEqual(len(out), 450)
        # Cuts only at sentence boundaries, so no mid-word break.
        self.assertIn("minum air putih", out)

    def test_natural_reply_is_unchanged(self) -> None:
        natural = "nasi gorengnya enak banget sih wkwk"
        self.assertEqual(sanitize_reply(natural), natural)

    def test_empty_and_whitespace(self) -> None:
        self.assertEqual(sanitize_reply(""), "")
        self.assertEqual(sanitize_reply("   "), "   ")

    def test_does_not_return_empty_from_over_cleaning(self) -> None:
        out = sanitize_reply("Halo! Ada yang bisa saya bantu?")
        self.assertTrue(out.strip())


class MarkdownEscapeTests(unittest.TestCase):
    def test_escapes_instead_of_deleting(self) -> None:
        # Deleting the '_' would corrupt the food name; escaping keeps it intact
        # while keeping Telegram's legacy Markdown parser happy.
        self.assertEqual(
            strip_markdown_for_telegram("nasi_ayam_geprek"),
            "nasi\\_ayam\\_geprek",
        )

    def test_escapes_all_markdown_specials(self) -> None:
        out = strip_markdown_for_telegram("*bold* _under_ `code` [link]")
        self.assertIn("\\*bold\\*", out)
        self.assertIn("\\_under\\_", out)
        self.assertIn("\\`code\\`", out)
        self.assertIn("\\[link\\]", out)

    def test_plain_text_untouched(self) -> None:
        self.assertEqual(strip_markdown_for_telegram("nasi goreng biasa"), "nasi goreng biasa")


if __name__ == "__main__":
    unittest.main()

