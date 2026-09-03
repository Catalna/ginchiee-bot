"""
nutrition/food_database.py
Built-in food nutrition database.
Values are per 100g unless noted otherwise.
Keys are lowercase Indonesian/English names.

Format: { "food_name": { "calories": kcal, "protein": g, "carbs": g, "fat": g } }
"""

FOOD_DB: dict[str, dict[str, float]] = {
    # ── Nasi & Karbohidrat ────────────────────────────────────────────────────
    "nasi putih":          {"calories": 130, "protein": 2.7, "carbs": 28.2, "fat": 0.3},
    "nasi merah":          {"calories": 110, "protein": 2.6, "carbs": 23.0, "fat": 0.9},
    "nasi goreng":         {"calories": 180, "protein": 4.0, "carbs": 28.0, "fat": 6.0},
    "rice":                {"calories": 130, "protein": 2.7, "carbs": 28.2, "fat": 0.3},
    "white rice":          {"calories": 130, "protein": 2.7, "carbs": 28.2, "fat": 0.3},
    "brown rice":          {"calories": 110, "protein": 2.6, "carbs": 23.0, "fat": 0.9},
    "mie":                 {"calories": 138, "protein": 4.5, "carbs": 26.0, "fat": 1.5},
    "mie goreng":          {"calories": 190, "protein": 5.0, "carbs": 27.0, "fat": 7.0},
    "mie rebus":           {"calories": 150, "protein": 4.5, "carbs": 26.5, "fat": 2.0},
    "pasta":               {"calories": 131, "protein": 5.0, "carbs": 25.0, "fat": 1.1},
    "roti tawar":          {"calories": 265, "protein": 9.0, "carbs": 49.0, "fat": 3.2},
    "roti":                {"calories": 265, "protein": 9.0, "carbs": 49.0, "fat": 3.2},
    "bread":               {"calories": 265, "protein": 9.0, "carbs": 49.0, "fat": 3.2},
    "kentang":             {"calories": 77,  "protein": 2.0, "carbs": 17.0, "fat": 0.1},
    "potato":              {"calories": 77,  "protein": 2.0, "carbs": 17.0, "fat": 0.1},
    "kentang goreng":      {"calories": 312, "protein": 3.4, "carbs": 41.0, "fat": 15.0},
    "oatmeal":             {"calories": 389, "protein": 17.0, "carbs": 66.0, "fat": 7.0},
    "oat":                 {"calories": 389, "protein": 17.0, "carbs": 66.0, "fat": 7.0},
    "singkong":            {"calories": 160, "protein": 1.4, "carbs": 38.0, "fat": 0.3},

    # ── Protein Hewani ────────────────────────────────────────────────────────
    "ayam":                {"calories": 165, "protein": 31.0, "carbs": 0.0,  "fat": 3.6},
    "chicken":             {"calories": 165, "protein": 31.0, "carbs": 0.0,  "fat": 3.6},
    "ayam goreng":         {"calories": 245, "protein": 27.0, "carbs": 2.0,  "fat": 14.0},
    "ayam bakar":          {"calories": 200, "protein": 29.0, "carbs": 2.0,  "fat": 8.0},
    "dada ayam":           {"calories": 165, "protein": 31.0, "carbs": 0.0,  "fat": 3.6},
    "chicken breast":      {"calories": 165, "protein": 31.0, "carbs": 0.0,  "fat": 3.6},
    "telur":               {"calories": 155, "protein": 13.0, "carbs": 1.1,  "fat": 11.0},
    "egg":                 {"calories": 155, "protein": 13.0, "carbs": 1.1,  "fat": 11.0},
    "telur rebus":         {"calories": 155, "protein": 13.0, "carbs": 1.1,  "fat": 11.0},
    "telur goreng":        {"calories": 196, "protein": 13.6, "carbs": 0.4,  "fat": 15.4},
    "telur dadar":         {"calories": 185, "protein": 12.0, "carbs": 1.0,  "fat": 14.5},
    "daging sapi":         {"calories": 250, "protein": 26.0, "carbs": 0.0,  "fat": 15.0},
    "beef":                {"calories": 250, "protein": 26.0, "carbs": 0.0,  "fat": 15.0},
    "ikan":                {"calories": 108, "protein": 22.0, "carbs": 0.0,  "fat": 2.5},
    "fish":                {"calories": 108, "protein": 22.0, "carbs": 0.0,  "fat": 2.5},
    "ikan salmon":         {"calories": 208, "protein": 20.0, "carbs": 0.0,  "fat": 13.0},
    "salmon":              {"calories": 208, "protein": 20.0, "carbs": 0.0,  "fat": 13.0},
    "ikan tuna":           {"calories": 132, "protein": 28.0, "carbs": 0.0,  "fat": 1.0},
    "tuna":                {"calories": 132, "protein": 28.0, "carbs": 0.0,  "fat": 1.0},
    "udang":               {"calories": 99,  "protein": 24.0, "carbs": 0.2,  "fat": 0.3},
    "shrimp":              {"calories": 99,  "protein": 24.0, "carbs": 0.2,  "fat": 0.3},
    "tempe":               {"calories": 193, "protein": 19.0, "carbs": 9.0,  "fat": 11.0},
    "tahu":                {"calories": 76,  "protein": 8.0,  "carbs": 1.9,  "fat": 4.8},
    "tofu":                {"calories": 76,  "protein": 8.0,  "carbs": 1.9,  "fat": 4.8},
    "daging kambing":      {"calories": 258, "protein": 27.0, "carbs": 0.0,  "fat": 16.0},

    # ── Sayuran ───────────────────────────────────────────────────────────────
    "bayam":               {"calories": 23,  "protein": 2.9,  "carbs": 3.6,  "fat": 0.4},
    "spinach":             {"calories": 23,  "protein": 2.9,  "carbs": 3.6,  "fat": 0.4},
    "brokoli":             {"calories": 34,  "protein": 2.8,  "carbs": 7.0,  "fat": 0.4},
    "broccoli":            {"calories": 34,  "protein": 2.8,  "carbs": 7.0,  "fat": 0.4},
    "wortel":              {"calories": 41,  "protein": 0.9,  "carbs": 10.0, "fat": 0.2},
    "carrot":              {"calories": 41,  "protein": 0.9,  "carbs": 10.0, "fat": 0.2},
    "kangkung":            {"calories": 19,  "protein": 2.6,  "carbs": 2.1,  "fat": 0.3},
    "tomat":               {"calories": 18,  "protein": 0.9,  "carbs": 3.9,  "fat": 0.2},
    "tomato":              {"calories": 18,  "protein": 0.9,  "carbs": 3.9,  "fat": 0.2},
    "timun":               {"calories": 15,  "protein": 0.7,  "carbs": 3.6,  "fat": 0.1},
    "cucumber":            {"calories": 15,  "protein": 0.7,  "carbs": 3.6,  "fat": 0.1},
    "kol":                 {"calories": 25,  "protein": 1.3,  "carbs": 5.8,  "fat": 0.1},
    "cabbage":             {"calories": 25,  "protein": 1.3,  "carbs": 5.8,  "fat": 0.1},

    # ── Buah ─────────────────────────────────────────────────────────────────
    "pisang":              {"calories": 89,  "protein": 1.1,  "carbs": 23.0, "fat": 0.3},
    "banana":              {"calories": 89,  "protein": 1.1,  "carbs": 23.0, "fat": 0.3},
    "apel":                {"calories": 52,  "protein": 0.3,  "carbs": 14.0, "fat": 0.2},
    "apple":               {"calories": 52,  "protein": 0.3,  "carbs": 14.0, "fat": 0.2},
    "jeruk":               {"calories": 47,  "protein": 0.9,  "carbs": 12.0, "fat": 0.1},
    "orange":              {"calories": 47,  "protein": 0.9,  "carbs": 12.0, "fat": 0.1},
    "mangga":              {"calories": 60,  "protein": 0.8,  "carbs": 15.0, "fat": 0.4},
    "mango":               {"calories": 60,  "protein": 0.8,  "carbs": 15.0, "fat": 0.4},
    "semangka":            {"calories": 30,  "protein": 0.6,  "carbs": 7.6,  "fat": 0.2},
    "watermelon":          {"calories": 30,  "protein": 0.6,  "carbs": 7.6,  "fat": 0.2},
    "alpukat":             {"calories": 160, "protein": 2.0,  "carbs": 9.0,  "fat": 15.0},
    "avocado":             {"calories": 160, "protein": 2.0,  "carbs": 9.0,  "fat": 15.0},
    "pepaya":              {"calories": 43,  "protein": 0.5,  "carbs": 11.0, "fat": 0.3},

    # ── Dairy & Susu ─────────────────────────────────────────────────────────
    "susu":                {"calories": 61,  "protein": 3.2,  "carbs": 4.8,  "fat": 3.3},
    "milk":                {"calories": 61,  "protein": 3.2,  "carbs": 4.8,  "fat": 3.3},
    "susu skim":           {"calories": 34,  "protein": 3.4,  "carbs": 5.0,  "fat": 0.1},
    "yogurt":              {"calories": 59,  "protein": 3.5,  "carbs": 5.0,  "fat": 3.3},
    "yogurt greek":        {"calories": 59,  "protein": 10.0, "carbs": 3.6,  "fat": 0.4},
    "keju":                {"calories": 402, "protein": 25.0, "carbs": 1.3,  "fat": 33.0},
    "cheese":              {"calories": 402, "protein": 25.0, "carbs": 1.3,  "fat": 33.0},

    # ── Snack & Makanan Cepat ─────────────────────────────────────────────────
    "kerupuk":             {"calories": 490, "protein": 3.0,  "carbs": 75.0, "fat": 19.0},
    "kacang":              {"calories": 567, "protein": 26.0, "carbs": 16.0, "fat": 49.0},
    "peanut":              {"calories": 567, "protein": 26.0, "carbs": 16.0, "fat": 49.0},
    "mie instant":         {"calories": 350, "protein": 8.0,  "carbs": 50.0, "fat": 13.0},
    "indomie":             {"calories": 350, "protein": 8.0,  "carbs": 50.0, "fat": 13.0},

    # ── Minuman ───────────────────────────────────────────────────────────────
    "air putih":           {"calories": 0,   "protein": 0.0,  "carbs": 0.0,  "fat": 0.0},
    "water":               {"calories": 0,   "protein": 0.0,  "carbs": 0.0,  "fat": 0.0},
    "jus jeruk":           {"calories": 45,  "protein": 0.7,  "carbs": 10.0, "fat": 0.2},
    "kopi":                {"calories": 2,   "protein": 0.3,  "carbs": 0.0,  "fat": 0.0},
    "coffee":              {"calories": 2,   "protein": 0.3,  "carbs": 0.0,  "fat": 0.0},
    "teh":                 {"calories": 1,   "protein": 0.0,  "carbs": 0.3,  "fat": 0.0},
    "tea":                 {"calories": 1,   "protein": 0.0,  "carbs": 0.3,  "fat": 0.0},

    # ── Makanan Indonesia Umum ────────────────────────────────────────────────
    "gado-gado":           {"calories": 120, "protein": 6.0,  "carbs": 10.0, "fat": 7.0},
    "soto":                {"calories": 80,  "protein": 7.0,  "carbs": 5.0,  "fat": 3.5},
    "rendang":             {"calories": 285, "protein": 25.0, "carbs": 6.0,  "fat": 18.0},
    "opor ayam":           {"calories": 210, "protein": 20.0, "carbs": 4.0,  "fat": 13.0},
    "sate ayam":           {"calories": 175, "protein": 22.0, "carbs": 5.0,  "fat": 8.0},
    "bakso":               {"calories": 133, "protein": 10.0, "carbs": 9.0,  "fat": 6.0},
    "bubur ayam":          {"calories": 100, "protein": 6.0,  "carbs": 15.0, "fat": 2.5},
    "pecel lele":          {"calories": 200, "protein": 18.0, "carbs": 5.0,  "fat": 12.0},
    "nasi padang":         {"calories": 400, "protein": 20.0, "carbs": 55.0, "fat": 12.0},

    # ── Minyak & Lemak ────────────────────────────────────────────────────────
    "minyak goreng":       {"calories": 884, "protein": 0.0,  "carbs": 0.0,  "fat": 100.0},
    "olive oil":           {"calories": 884, "protein": 0.0,  "carbs": 0.0,  "fat": 100.0},
    "butter":              {"calories": 717, "protein": 0.9,  "carbs": 0.1,  "fat": 81.0},
    "margarin":            {"calories": 717, "protein": 0.9,  "carbs": 0.1,  "fat": 81.0},
}

# Aliases / common typos
FOOD_DB["nasi"] = FOOD_DB["nasi putih"]
FOOD_DB["beras"] = FOOD_DB["nasi putih"]
FOOD_DB["chicken fillet"] = FOOD_DB["dada ayam"]
FOOD_DB["ikan lele"] = {"calories": 90, "protein": 18.0, "carbs": 0.0, "fat": 2.5}
FOOD_DB["lele"] = FOOD_DB["ikan lele"]
FOOD_DB["telur ayam"] = FOOD_DB["telur"]
