import math
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Union, Dict, Literal

app = FastAPI(title="Yield Prediction API (Hardcoded Polygons + Pest Risk)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================================================
# HARD-CODED DAMAGE POLYGONS (IGNORES USER INPUT)
# =========================================================

HARD_CODED_POLYGONS = [
    [
        [28.4588642, 77.2972488],
        [28.4588742, 77.2972688],
        [28.4588542, 77.2972788],
        [28.4588442, 77.2972588]
    ],
    [
        [28.4588642, 77.2972488],
        [28.4588842, 77.2972488],
        [28.4588842, 77.2972788],
        [28.4588642, 77.2972788]
    ]
]

# =========================================================
# HELPERS
# =========================================================

def polygon_area_from_latlon(points: List[List[float]]) -> float:
    if len(points) < 3:
        return 0.0

    lat_ref = sum(p[0] for p in points) / len(points)
    lon_ref = points[0][1]

    lat_to_m = 111320
    lon_to_m = 111320 * math.cos(math.radians(lat_ref))

    xy = []
    for lat, lon in points:
        xy.append([
            (lon - lon_ref) * lon_to_m,
            (lat - lat_ref) * lat_to_m
        ])

    area = 0
    for i in range(len(xy)):
        x1, y1 = xy[i]
        x2, y2 = xy[(i + 1) % len(xy)]
        area += x1 * y2 - x2 * y1

    return abs(area * 0.5)

# =========================================================
# MODELS
# =========================================================

class SoilSensorValues(BaseModel):
    Moisture_percent: Optional[float] = None
    Temperature_C: Optional[float] = None
    EC_uS_cm: Optional[float] = None
    pH: Optional[float] = None
    N_mg_per_kg: Optional[float] = None
    P_mg_per_kg: Optional[float] = None
    K_mg_per_kg: Optional[float] = None

class AnalyzeDamageRequest(BaseModel):
    farm_area_m2: float
    row_spacing: float
    plant_spacing: float

    # Ignored by backend
    damage_polygons: Optional[list] = None

    pest_image_url: Optional[str] = None
    paddy_type: Optional[str] = "local"
    growth_stage: Optional[str] = "reproductive"
    soil_sensor_values: Optional[SoilSensorValues] = None

# =========================================================
# YIELD LOGIC
# =========================================================

def compute_soil_fertility(soil: Optional[Dict]) -> float:
    if not soil:
        return 0.5

    score = 1.0
    if soil.get("pH") and 5.5 <= soil["pH"] <= 7.5:
        score += 0.1
    else:
        score -= 0.1

    if soil.get("N_mg_per_kg", 0) < 20:
        score -= 0.1

    m = soil.get("Moisture_percent")
    if m is not None:
        if m < 20:
            score -= 0.1
        elif 30 <= m <= 60:
            score += 0.05

    return max(0.3, min(score, 1.2))


def predict_yield(
    surviving_plants: float,
    paddy_type: str,
    soil: Optional[Dict],
    growth_stage: str
):
    base = 0.014

    fert_factor = compute_soil_fertility(soil)

    # 🔥 HARD-CODED pest risk score
    PEST_RISK_SCORE = 0.37
    pest_factor = max(0.5, 1 - (PEST_RISK_SCORE * 0.4))

    variety_factor = 1.1 if "hybrid" in paddy_type.lower() else 1.0
    stage_factor = 0.95 if "repro" in growth_stage.lower() else 1.0

    per_plant = base * fert_factor * pest_factor * variety_factor * stage_factor
    total_yield = per_plant * surviving_plants

    return {
        "per_plant_kg": per_plant,
        "predicted_yield_kg": total_yield,
        "lower_bound_kg": total_yield * 0.9,
        "upper_bound_kg": total_yield * 1.1,
        "hardcoded_pest_risk_score_used": PEST_RISK_SCORE
    }

# =========================================================
# FINAL ENDPOINT
# =========================================================

@app.post("/analyze-damage")
def analyze_damage(data: AnalyzeDamageRequest):

    plant_density = 1 / (data.row_spacing * data.plant_spacing)
    total_plants = plant_density * data.farm_area_m2

    lost_plants = 0
    total_damage_area = 0

    # 🔥 IGNORE USER POLYGONS  
    # ALWAYS use hardcoded polygons
    for poly in HARD_CODED_POLYGONS:
        area = polygon_area_from_latlon(poly)
        total_damage_area += area
        lost_plants += area * plant_density

    surviving = max(0, total_plants - lost_plants)

    soil_vals = data.soil_sensor_values.dict() if data.soil_sensor_values else None

    yield_result = predict_yield(
        surviving_plants=surviving,
        paddy_type=data.paddy_type,
        soil=soil_vals,
        growth_stage=data.growth_stage
    )

    return {
        "farm_area_m2": data.farm_area_m2,
        "total_plants": total_plants,

        "hardcoded_polygons_used": HARD_CODED_POLYGONS,

        "damage_area_m2": total_damage_area,
        "lost_plants": lost_plants,
        "remaining_plants": surviving,

        "yield_prediction": yield_result,

        "pest_image_received": data.pest_image_url
    }
