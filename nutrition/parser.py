"""
nutrition/parser.py
Parse natural language food input using LLM → structured list.
"""

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ParsedFoodItem:
    food_name: str
    amount_g: float        # gram, estimated if not given
    unit: str              # "gram", "porsi", "buah", etc.
    amount_raw: str        # original string e.g. "200g", "1 buah"


UNIT_TO_GRAM: dict[str, float] = {
    # Generic units → default gram estimate
    "buah":   100.0,
    "biji":   50.0,
    "butir":  55.0,    # e.g. telur ~55g per butir
    "porsi":  200.0,
    "mangkok": 300.0,
    "piring": 350.0,
    "gelas":  200.0,
    "sendok": 15.0,
    "sdm":    15.0,    # sendok makan
    "sdt":    5.0,     # sendok teh
    "slice":  30.0,
    "lembar": 25.0,
    "batang": 40.0,
    "sachet": 20.0,
    "bungkus": 85.0,
    "loaf":   30.0,
}


async def parse_food_from_llm(text: str, ai_service) -> list[ParsedFoodItem]:
    """
    Use LLM to extract structured food list from natural language.
    Returns list of ParsedFoodItem.
    """
    prompt = f"""Ekstrak semua makanan/minuman dari kalimat berikut dan konversikan ke format JSON.

Kalimat: "{text}"

Kembalikan HANYA array JSON dalam format berikut (tanpa markdown, tanpa penjelasan):
[
  {{"food": "nama makanan dalam bahasa Indonesia", "amount": angka, "unit": "gram"}}
]

Aturan:
- Jika jumlah tidak disebutkan, perkirakan porsi wajar (misal: 1 telur ≈ 55g, 1 piring nasi ≈ 200g)
- Selalu konversi ke unit gram
- Contoh: "1 butir telur" → amount: 55, unit: "gram"
- Contoh: "nasi 200 gram ayam 150g" → [{{"food": "nasi putih", "amount": 200, "unit": "gram"}}, {{"food": "ayam", "amount": 150, "unit": "gram"}}]
- Gunakan nama makanan yang umum dan sederhana"""

    try:
        raw = await ai_service.generate_raw(prompt)
        # Extract JSON from response
        json_match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if not json_match:
            logger.warning("LLM did not return valid JSON array for food parsing")
            return []

        items_data = json.loads(json_match.group())
        result = []
        for item in items_data:
            food_name = item.get("food", "").strip().lower()
            amount = float(item.get("amount", 100))
            unit = item.get("unit", "gram")

            if not food_name:
                continue

            result.append(ParsedFoodItem(
                food_name=food_name,
                amount_g=amount,
                unit=unit,
                amount_raw=f"{amount}{unit}",
            ))

        return result

    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.error(f"Error parsing food items: {e}")
        return []
