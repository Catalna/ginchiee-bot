"""
nutrition/parser.py
Parse natural language food input using LLM → structured list.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ParsedFoodItem:
    food_name: str
    amount_g: float        # gram, estimated if not given
    unit: str              # "gram", "porsi", "potong", "sendok", etc.
    amount_raw: str        # original string e.g. "200g", "1 potong"
    calories: Optional[float] = None
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fat_g: Optional[float] = None


UNIT_TO_GRAM: dict[str, float] = {
    # Generic units → default gram estimate
    "buah":   100.0,
    "biji":   50.0,
    "butir":  55.0,    # e.g. telur ~55g per butir
    "potong": 120.0,   # e.g. ayam potong ~120g
    "porsi":  200.0,
    "mangkuk": 300.0,
    "mangkok": 300.0,
    "piring": 350.0,
    "gelas":  250.0,
    "cangkir": 180.0,
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
    Use LLM to extract structured food items and estimate their nutrition
    from natural Indonesian language.
    """
    prompt = f"""Kamu adalah ahli gizi. Analisa teks makanan/minuman berikut dan ekstrak setiap item beserta estimasi berat dan nutrisinya.

Input pengguna: "{text}"

Tugas:
1. Pisahkan setiap item makanan / minuman (misal: "Nasi 100g", "Ayam penyet", "sambel bawang").
2. Jika pengguna tidak menyebutkan gram atau ukuran (misal hanya bilang "dada ayam goreng", "ayam bakar", "es teh manis"):
   - Perkirakan porsi wajar standar dalam gram (misal: 1 potong dada ayam goreng ≈ 130g, 1 porsi ayam bakar ≈ 150g, 1 sendok sambal ≈ 15-20g, 1 piring nasi ≈ 150-200g, 1 butir telur ≈ 55g).
3. Hitung estimasi nutrisi untuk porsi tersebut:
   - calories (total kkal)
   - protein_g (gram protein)
   - carbs_g (gram karbohidrat)
   - fat_g (gram lemak)

Kembalikan HANYA array JSON murni tanpa format markdown codeblock, tanpa teks pembuka/penutup, dalam struktur persis seperti ini:
[
  {{
    "food": "nama makanan jelas",
    "amount_g": 130,
    "unit": "potong",
    "calories": 240,
    "protein_g": 31.0,
    "carbs_g": 2.0,
    "fat_g": 12.0
  }}
]"""

    try:
        raw = await ai_service.generate_raw(prompt)
        # Clean markdown codeblocks if LLM included them
        clean_raw = re.sub(r"^```[a-zA-Z]*\n", "", raw.strip(), flags=re.MULTILINE)
        clean_raw = re.sub(r"```$", "", clean_raw.strip(), flags=re.MULTILINE)

        # Extract JSON array
        json_match = re.search(r'\[.*\]', clean_raw, re.DOTALL)
        if not json_match:
            logger.warning(f"LLM did not return valid JSON array for food parsing. Raw response: {raw}")
            return []

        items_data = json.loads(json_match.group())
        result = []
        for item in items_data:
            if not isinstance(item, dict):
                continue

            food_name = str(item.get("food", "")).strip().lower()
            if not food_name:
                continue

            # Safely parse numeric amounts
            try:
                amount_g = float(item.get("amount_g") or item.get("amount") or 100)
            except (ValueError, TypeError):
                amount_g = 100.0

            unit = str(item.get("unit", "gram")).strip()
            amount_raw = f"{amount_g:.0f}g"

            # Parse macros if provided
            def _parse_num(val):
                try:
                    return round(float(val), 1) if val is not None else None
                except (ValueError, TypeError):
                    return None

            calories = _parse_num(item.get("calories"))
            protein_g = _parse_num(item.get("protein_g") or item.get("protein"))
            carbs_g = _parse_num(item.get("carbs_g") or item.get("carbs"))
            fat_g = _parse_num(item.get("fat_g") or item.get("fat"))

            result.append(ParsedFoodItem(
                food_name=food_name,
                amount_g=amount_g,
                unit=unit,
                amount_raw=amount_raw,
                calories=calories,
                protein_g=protein_g,
                carbs_g=carbs_g,
                fat_g=fat_g,
            ))

        return result

    except Exception as e:
        logger.error(f"Error parsing food items from LLM: {e}")
        return []
