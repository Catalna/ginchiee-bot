"""
services/personality.py
Ginchiee's voice: persona definition, few-shot demonstrations, and a
post-processing guard.

Two problems this module solves:

1. Descriptive instructions ("be friendly, don't be robotic") let the model
   invent its own style, which drift toward formal report-writing. Explicit
   constraints plus worked examples lock the voice in.

2. LLMs ignore style constraints some fraction of the time. `sanitize_reply()`
   is the deterministic safety net that strips the specific failure modes we
   actually saw: formal openers, bullet lists, emoji floods, and essay-length
   replies.
"""

import logging
import re

from config.settings import (
    BOT_NAME,
    MAX_REPLY_CHARS,
    MAX_REPLY_EMOJI,
    MAX_REPLY_SENTENCES,
)

logger = logging.getLogger(__name__)


# ── Persona ───────────────────────────────────────────────────────────────────

PERSONA_SYSTEM_INSTRUCTION = f"""Kamu {BOT_NAME}, sahabat dekat (bestie) user yang juga ikut nemenin soal makan dan kesehatan.

Cara kamu ngobrol:
- Gaya chat WhatsApp, bukan gaya asisten formal. Kalimat pendek, santai, kadang terpotong-potong.
- Pakai "aku" dan "kamu". JANGAN pernah pakai "Anda", "saya", atau "penulis".
- Boleh pakai Bahasa gaul: gak, udah, nih, dong, sih, banget, mah, kan. Jangan berlebihan.
- Maksimal {MAX_REPLY_SENTENCES} kalimat. Kalau jawabannya panjang, berarti kamu over-sharing.
- Ekspresif tapi kontrol: maksimal {MAX_REPLY_EMOJI} emoji per balasan. Jangan deretan emoji.

Yang DILARANG (ini yang bikin kamu terdengar seperti bot):
- Jangan mulai dengan "Tentu!", "Baik!", "Tentu saja!", "Halo!", "Hai!", "Oke!".
- Jangan pakai bullet point, daftar bernomor, heading, atau tabel.
- Jangan merangkum ulang apa yang baru user bilang.
- Jangan bilang "sebagai AI", "semoga membantu", "jangan ragu", "berikut", "berikut adalah".
- Jangan ceramah, jangan nasihat kalau nggak diminta.
- Jangan nanya balik kecuali memang butuh info untuk bantu (misal jam/tanggal kegiatan).
- Jangan kasih disclaimer medis di setiap balasan.

Kalau user cerita random, gosip, atau curhat: ikut seru dan empati. JANGAN maksa balik ke topik diet kalau user nggak ngomongin itu.

Soal kesehatan: supportif, nggak judgemental, nggak strict. Nggak diagnosa penyakit, nggak resep obat. Kalau keluhan serius, arahkan ke dokter dengan nada peduli.

Kalau lagi bantu catat makanan atau jadwal, kamu boleh excited dan sedikit cemasan yang lucu.
"""


# ── Few-shot demonstrations ───────────────────────────────────────────────────
# Injected as chat history preamble, so the model imitates the format of real
# turns rather than reading rules. These are the single strongest lever against
# "sounds like AI".

FEW_SHOT: list[dict] = [
    {
        "role": "user",
        "content": "tadi aku makan nasi goreng sama telur mata sapi",
        "model": (
            "Nasi goreng plus telur mata sapi? combo klasik banget sih 😋 "
            "ini udah aku catat ya, jangan lupa minum airnya biar nggak ngantuk!"
        ),
        "good": True,
        "note": (
            "contoh BENAR: reaksinya spontan, sebut makanannya, 1 emoji, "
            "ada satu nudge ringan, tanpa bullet dan tanpa pembuka formal"
        ),
    },
    {
        "role": "user",
        "content": "halo",
        "model": "halo juga! ada apa nih hari ini? 🥰",
        "good": True,
        "note": (
            "contoh BENAR: 5 kata. tidak ada 'Halo! Ada yang bisa saya bantu?'"
        ),
    },
    {
        "role": "user",
        "content": "aku curhat dong, hari ini jelek banget",
        "model": (
            "sini cerita, aku dengerin 🤍 kenapa tuh? "
            "jangan ditahan sendiri ya"
        ),
        "good": True,
        "note": (
            "contoh BENAR: empati dulu, nggak langsung nawarin solusi makanan"
        ),
    },
    {
        "role": "user",
        "content": "aku tadi abis makan 2 potong ayam geprek terus minum es teh manis",
        "model": (
            "wah ayam geprek + es teh manis, ini yang aku suka dengar wkwk 😆 "
            "nyatok ya biar harimu tetap seimbang"
        ),
        "note": "contoh BENAR: antusias tanpa angka, tanpa tabel",
    },
    {
        "role": "user",
        "content": "makasih ya ginchiee",
        "model": "sama-sama, insyaallah aku selalu di sini kalo butuh cerita ✨",
        "note": "contoh BENAR: singkat, hangat, tanpa 'Sama-sama! Senang bisa membantu Anda.'",
    },
    {
        "role": "user",
        "content": "tadi aku makan nasi goreng",
        "good": False,
        "model": (
            "Tentu! Berikut adalah ringkasan nutrisi makanan Anda:\n"
            "- Nasi goreng: 320 kcal\n"
            "- Protein: 8g\n"
            "- Karbohidrat: 45g\n\n"
            "Semoga informasi ini bermanfaat! Jangan ragu untuk bertanya lagi ya 😊"
        ),
        "note": (
            "contoh SALAH — JANGAN pernah seperti ini. Terlalu formal, pakai bullet, "
            "pakai 'Anda', pakai penutup khas asisten, dan mekanis. Bandingkan dengan contoh pertama."
        ),
    },
    {
        "role": "user",
        "content": "malam ya aku mau tidur",
        "good": False,
        "model": (
            "Selamat malam! Berikut adalah tips tidur sehat untuk Anda:\n"
            "1. Hindari kafein\n"
            "2. Jaga suhu kamar\n"
            "3. Matikan gadget 30 menit sebelum tidur"
        ),
        "note": (
            "contoh SALAH — JANGAN seperti ini. User cuma pamit tidur, bukan minta tips. "
            "Cukup jawab 'selamat tidur! mimpi indah ya 🌙'."
        ),
    },
]


# ── Banned phrases ────────────────────────────────────────────────────────────

#: Openers that are formal *salutations*. Only stripped when immediately
#: followed by real punctuation, so an ordinary sentence word survives
#: ("Tentu!" strips, "maaf ya aku bingung" does not).
SALUTATION_OPENERS: tuple[str, ...] = (
    "tentu",
    "tentu saja",
    "tentu banget",
    "baik",
    "baiklah",
    "halo",
    "hai",
    "oke",
    "ok",
    "siap",
    "oh",
    "wah",
    "maaf",
    "terima kasih atas pertanyaan",
    "dengan senang hati",
)

#: Formulaic *prefixes*. Always stripped when the reply starts with them,
#: because they only ever appear as boilerplate ("Berikut adalah ...",
#: "Sebagai AI ...").
PREFIX_OPENERS: tuple[str, ...] = (
    "berikut adalah",
    "berikut",
    "sebagai ai",
    "saya akan",
    "aku akan membantu",
)

# Kept for backwards compatibility.
FORBIDDEN_OPENERS: tuple[str, ...] = SALUTATION_OPENERS + PREFIX_OPENERS

#: Matched anywhere in the reply (case-insensitive).
FORBIDDEN_PHRASES: tuple[str, ...] = (
    "sebagai ai",
    "sebagai model bahasa",
    "sebagai asisten",
    "semoga membantu",
    "semoga informasi ini bermanfaat",
    "jangan ragu",
    "jangan sungkan",
    "ada lagi yang bisa saya bantu",
    "ada yang bisa saya bantu",
    "apakah ada pertanyaan lain",
    "berikut adalah daftar",
    "penulis",
    "anda sebaiknya",
)

#: Leading decorations that betray a list.
_BULLET_PREFIX = re.compile(r"^\s*(?:[-*•·–—]+\s*|\d+[.)]\s+|#{1,6}\s+)")
_EMOJI_PATTERN = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF️❤]"
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?~…])\s+|\n+")


def _strip_accents_for_match(text: str) -> str:
    return text


def _remove_forbidden_openers(text: str) -> tuple[str, bool]:
    """Remove formal openers while the reply still starts with one.

    Formulaic prefixes ("Berikut adalah", "Sebagai AI") are always stripped.
    Salutations ("Tentu!", "Baik,") are only stripped when immediately followed
    by real punctuation, so an ordinary sentence word such as "maaf" in
    "maaf ya aku bingung" survives untouched.
    """
    stripped = text.lstrip()
    changed = False
    while True:
        removed_this_round = False
        working = stripped.lstrip()

        for opener in PREFIX_OPENERS:
            candidate = re.sub(r"^" + re.escape(opener) + r"[\s!,.~:—]*", "", working, count=1, flags=re.IGNORECASE)
            if candidate != working and candidate.strip():
                stripped = candidate.strip()
                changed = True
                removed_this_round = True
                break

        if removed_this_round:
            continue

        for opener in SALUTATION_OPENERS:
            pattern = re.compile(r"^" + re.escape(opener) + r"(?=[!,.~:—\n])[!,.~:—]*", re.IGNORECASE)
            candidate = pattern.sub("", working, count=1)
            if candidate != working and candidate.strip():
                stripped = candidate.strip()
                changed = True
                removed_this_round = True
                break

        if not removed_this_round:
            return stripped, changed


def _remove_forbidden_phrases(text: str) -> tuple[str, list[str]]:
    removed = []
    for phrase in FORBIDDEN_PHRASES:
        # Replace with a space, never "" — otherwise words either side fuse
        # together ("Saya sebagai AI akan" -> "Sayaakan").
        pattern = re.compile(
            r"\s*" + re.escape(phrase) + r"[\s.!?,]*",
            re.IGNORECASE,
        )
        new_text = pattern.sub(" ", text)
        if new_text != text:
            removed.append(phrase)
            text = new_text
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(), removed


def _strip_list_markers(text: str) -> tuple[str, bool]:
    """Drop leading bullet/number markers from each line."""
    lines = [line for line in text.split("\n")]
    cleaned: list[str] = []
    changed = False
    for line in lines:
        new_line = _BULLET_PREFIX.sub("", line)
        if new_line != line:
            changed = True
        cleaned.append(new_line.strip() if new_line.strip() else line)
    result = "\n".join(part for part in cleaned if part.strip())
    return (result.strip() if result.strip() else text), changed


def _limit_emoji(text: str, limit: int) -> tuple[str, int]:
    """Keep only the first `limit` emoji, drop the rest."""
    seen = 0

    def repl(match: re.Match) -> str:
        nonlocal seen
        if seen < limit:
            seen += 1
            return match.group(0)
        return ""

    return _EMOJI_PATTERN.sub(repl, text), seen


def _limit_sentences(text: str, limit: int) -> str:
    """Trim to `limit` sentences, cutting only at a sentence boundary."""
    # Count sentences using word-ending punctuation, not just any newline.
    sentences = _SENTENCE_SPLIT.split(text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if len(sentences) <= limit:
        return text
    kept = sentences[:limit]
    return " ".join(kept).strip()


def _truncate_at_sentence(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars]
    # Prefer the last sentence boundary.
    boundary = max(clipped.rfind("."), clipped.rfind("!"), clipped.rfind("?"), clipped.rfind("~"))
    if boundary > max_chars * 0.5:
        return clipped[: boundary + 1].strip()
    # Fall back to a word boundary.
    space = clipped.rfind(" ")
    if space > 0:
        return clipped[:space].strip() + "..."
    return clipped.strip() + "..."


def _normalize_whitespace(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", text)
    # Collapse 3+ blank lines and any trailing spaces on lines.
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


#: Guard against over-cleaning: never let the sanitizer delete so much that the
#: reply becomes meaningless. Measured on content characters only (emoji not counted)
#: so a short but valid reply like "enak banget sih wkwk" survives.
MIN_CONTENT_CHARS = 5


#: Indonesian pronoun normalisation. "Anda"/"saya" are the single loudest tell
#: of assistant-speak; mapping them to chat pronouns is meaning-preserving.
_PRONOUN_MAP = (
    (re.compile(r"\bAnda\b"), "kamu"),
    (re.compile(r"\banda\b"), "kamu"),
    (re.compile(r"\bSaya\b"), "aku"),
    (re.compile(r"\bsaya\b"), "aku"),
    (re.compile(r"\bAnda pun\b"), "kamu"),
    (re.compile(r"\bAnda harus\b"), "kamu harus"),
    (re.compile(r"\bAnda perlu\b"), "kamu perlu"),
)


def _normalize_pronouns(text: str) -> tuple[str, bool]:
    changed = False
    for pattern, replacement in _PRONOUN_MAP:
        new_text = pattern.sub(replacement, text)
        if new_text != text:
            text = new_text
            changed = True
    return text, changed


def sanitize_reply(
    text: str,
    *,
    max_emoji: int | None = None,
    max_sentences: int | None = None,
    max_chars: int | None = None,
) -> str:
    """Deterministically clean up a model reply.

    Only *removes* mechanical failure modes (formal openers, banned phrases,
    list markers, emoji floods, over-length). It never rewrites the content, so
    personality stays with the model.
    """
    if not text:
        return text

    max_emoji = MAX_REPLY_EMOJI if max_emoji is None else max_emoji
    max_sentences = MAX_REPLY_SENTENCES if max_sentences is None else max_sentences
    max_chars = MAX_REPLY_CHARS if max_chars is None else max_chars

    original = text.strip().strip('"').strip()
    if not original:
        return text

    notes: list[str] = []

    # ── Phase 1: mechanical boilerplate removal ────────────────────────────
    # These steps only delete assistant-speak, so they are always safe. There
    # is no minimum-length guard here: if the entire reply was boilerplate,
    # returning the leftover is better than keeping "Tentu!".
    text, changed = _remove_forbidden_openers(original)
    if changed:
        notes.append("opener")

    text, removed = _remove_forbidden_phrases(text)
    if removed:
        notes.append(f"phrases:{','.join(removed)}")

    # A second pass catches prefixes exposed by the phrase removal above
    # ("Saya sebagai AI akan ..." -> "Saya akan ...").
    text, changed_again = _remove_forbidden_openers(text)
    if changed_again:
        notes.append("opener2")

    text, listy = _strip_list_markers(text)
    if listy:
        notes.append("list")

    text, pronouned = _normalize_pronouns(text)
    if pronouned:
        notes.append("pronouns")

    text = _normalize_whitespace(text)

    # Everything was boilerplate and nothing meaningful survives: fall back to
    # the original rather than returning an empty reply.
    if not text.strip():
        return original

    cleaned = text  # safe-to-return baseline for phase 2

    # ── Phase 2: length shaping (destructive, so guarded) ─────────────────
    text, emoji_seen = _limit_emoji(cleaned, max_emoji)
    if emoji_seen > max_emoji:
        notes.append("emoji")

    if len(text) > max_chars:
        text = _truncate_at_sentence(text, max_chars)
        notes.append("truncated")

    text = _limit_sentences(text, max_sentences)
    text = _normalize_whitespace(text)

    # Guard: never let length shaping erase the reply. Measured on content
    # characters only, so emoji are not counted against the minimum.
    if len(_EMOJI_PATTERN.sub("", text).strip()) < MIN_CONTENT_CHARS:
        logger.debug("Persona sanitizer kept cleaned baseline after shaping")
        return cleaned

    if notes:
        logger.debug("Persona sanitizer applied %s", "|".join(notes))

    return text


def build_persona_guard_prompt() -> str:
    """Short restatement appended to prompts that are not chat (reminders, etc.)."""
    return (
        f"PENTING - gaya bicara: gaya chat {BOT_NAME}, santai, maksimal "
        f"{MAX_REPLY_SENTENCES} kalimat pendek, maksimal {MAX_REPLY_EMOJI} emoji. "
        "DILARANG bullet point, daftar bernomor, tabel, markdown. "
        "DILARANG mulai dengan 'Tentu!', 'Baik!', 'Halo!'. "
        "DILARANG ditutup dengan 'Semoga membantu'. "
        "Pakai 'aku' dan 'kamu', jangan 'Anda'."
    )


def few_shot_as_history() -> list[dict]:
    """The good examples shaped like a Gemini chat history.

    Only good turns go in here. Feeding a bad example back as model output
    risks imitation, so the bad ones live in the system instruction instead
    (see `ANTI_EXAMPLES`), where they are explicitly framed as wrong.
    """
    history: list[dict] = []
    for example in FEW_SHOT:
        if not example.get("model") or not example.get("good", True):
            continue
        history.append({"role": "user", "parts": [example["content"]]})
        history.append({"role": "model", "parts": [example["model"]]})
    return history


# Backwards-compatible alias.
build_few_shot_history = few_shot_as_history


def anti_examples_block() -> str:
    """The 'do NOT do this' block, appended to the system instruction."""
    blocks = ["\nContoh gaya yang SALAH (jangan pernah seperti ini):"]
    for example in FEW_SHOT:
        if example.get("good", True):
            continue
        blocks.append(f"\nUser: {example['content']}")
        blocks.append(f"Balasan yang salah: {example['model']}")
        if example.get("note"):
            blocks.append(f"Alasan: {example['note']}")
    blocks.append("\nContoh-contoh di atas adalah larangan, bukan kebenaran.")
    return "\n".join(blocks)


def good_examples_block() -> str:
    """Compact 'do THIS' block shown to prompts that cannot take history."""
    blocks = ["\nContoh gaya yang benar:"]
    for example in FEW_SHOT:
        if not example.get("model") or not example.get("good", True):
            continue
        blocks.append(f"\nUser: {example['content']}")
        blocks.append(f"{BOT_NAME}: {example['model']}")
    return "\n".join(blocks)


def emoji_count(text: str) -> int:
    return len(_EMOJI_PATTERN.findall(text or ""))


def looks_like_ai_slop(text: str) -> list[str]:
    """Diagnostics helper: list why a reply still reads like AI output.

    Mirrors the sanitizer's rules so it does not report problems the sanitizer
    intentionally leaves alone (e.g. the word "maaf" inside a sentence).
    """
    reasons: list[str] = []
    if not text:
        return reasons
    lower = text.lower().strip()

    for opener in PREFIX_OPENERS:
        if lower.startswith(opener):
            reasons.append(f"formulaic prefix: {opener!r}")
            break
    else:
        for opener in SALUTATION_OPENERS:
            if re.match(r"^" + re.escape(opener) + r"[!,.~:—\n]", lower):
                reasons.append(f"formal opener: {opener!r}")
                break

    for phrase in FORBIDDEN_PHRASES:
        if phrase in lower:
            reasons.append(f"banned phrase: {phrase!r}")
            break

    if re.search(r"(?m)^\s*[-*•]\s+", text):
        reasons.append("bullet list")
    if re.search(r"(?m)^\s*\d+[.)]\s+", text):
        reasons.append("numbered list")
    if "**" in text or re.search(r"(?m)^#{1,6}\s", text):
        reasons.append("markdown formatting")
    if emoji_count(text) > MAX_REPLY_EMOJI:
        reasons.append(f"emoji flood: {emoji_count(text)}")
    if len(text) > MAX_REPLY_CHARS:
        reasons.append(f"too long: {len(text)} chars")
    if re.search(r"\bAnda\b", text):
        reasons.append("formal pronoun: Anda")
    sentence_count = len([s for s in _SENTENCE_SPLIT.split(text) if s.strip()])
    if sentence_count > MAX_REPLY_SENTENCES:
        reasons.append(f"too many sentences: {sentence_count}")

    return reasons


def strip_markdown_for_telegram(text: str) -> str:
    """Escape characters that break legacy Telegram Markdown parsing.

    Bot replies are frequently built by interpolating user input (food names,
    activity titles) into messages sent with parse_mode="Markdown". An
    unescaped '_' or '*' from that input makes Telegram reject the message with
    "Can't parse entities", silently dropping the reply.

    Escaping (not deleting) keeps the user's text intact.
    """
    if not text:
        return text
    return re.sub(r"([`*_\[\]])", r"\\\1", str(text))


