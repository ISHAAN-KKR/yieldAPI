from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional
import math
import requests

app = FastAPI()


# =========================================================
#   FETCH GROUND ELEVATION (SAME AS YOUR ORIGINAL FUNCTION)
# =========================================================

def get_ground_elevation(lat, lon):
    try:
        url = f"https://api.open-elevation.com/api/v1/lookup?locations={lat},{lon}"
        r = requests.get(url, timeout=5).json()
        return r["results"][0]["elevation"]
    except:
        return 0.0


# =========================================================
#                  GEOMETRY HELPERS
# =========================================================

def polygon_area_from_latlon(points):
    if len(points) < 3:
        return 0.0

    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    lat_ref = sum(lats) / len(lats)
    lon_ref = lons[0]

    lat_to_m = 111320.0
    lon_to_m = 111320.0 * math.cos(math.radians(lat_ref))

    xy = []
    for lat, lon in points:
        x = (lon - lon_ref) * lon_to_m
        y = (lat - lat_ref) * lat_to_m
        xy.append((x, y))

    area = 0
    n = len(xy)
    for i in range(n):
        x1, y1 = xy[i]
        x2, y2 = xy[(i + 1) % n]
        area += x1 * y2 - x2 * y1

    return abs(area) * 0.5


def meters_to_latlon_offsets(east_m, north_m, lat_ref):
    lat_to_m = 111320.0
    lon_to_m = 111320.0 * math.cos(math.radians(lat_ref))
    return north_m / lat_to_m, east_m / lon_to_m


# =========================================================
#            DRONE FOOTPRINT CALCULATION
# =========================================================

def camera_footprint_from_drone(
    drone_lat, drone_lon,
    drone_alt_msl,
    compass_heading_deg,
    mount_tilt_deg,
    vertical_fov_deg,
    horizontal_fov_deg
):
    ground_elev = get_ground_elevation(drone_lat, drone_lon)
    h = drone_alt_msl - ground_elev
    if h <= 0:
        h = 5

    half_v = vertical_fov_deg / 2
    near_angle = max(0.0001, mount_tilt_deg - half_v)
    far_angle = mount_tilt_deg + half_v

    near_d = h * math.tan(math.radians(near_angle))
    far_d = h * math.tan(math.radians(far_angle))

    half_w_near = near_d * math.tan(math.radians(horizontal_fov_deg / 2))
    half_w_far = far_d * math.tan(math.radians(horizontal_fov_deg / 2))

    theta = math.radians(compass_heading_deg)

    def en_from_forward_right(d_forward, right_offset):
        ef = math.sin(theta)
        nf = math.cos(theta)
        er = math.cos(theta)
        nr = -math.sin(theta)
        east = d_forward * ef + right_offset * er
        north = d_forward * nf + right_offset * nr
        return east, north

    corners_en = [
        en_from_forward_right(near_d, -half_w_near),
        en_from_forward_right(near_d, +half_w_near),
        en_from_forward_right(far_d, +half_w_far),
        en_from_forward_right(far_d, -half_w_far),
    ]

    footprint = []
    for east_m, north_m in corners_en:
        dlat, dlon = meters_to_latlon_offsets(east_m, north_m, drone_lat)
        footprint.append((drone_lat + dlat, drone_lon + dlon))

    return footprint


# =========================================================
#                REQUEST BODY MODELS
# =========================================================

class ManualPolygon(BaseModel):
    type: str = "manual"
    points: List[List[float]]  # [[lat, lon], [lat, lon] ...]


class DronePolygon(BaseModel):
    type: str = "drone"
    drone_lat: float
    drone_lon: float
    drone_alt_msl: float
    compass_heading_deg: float
    mount_tilt_deg: float
    vertical_fov_deg: float
    horizontal_fov_deg: float


class DamageRequest(BaseModel):
    farm_area_m2: float
    row_spacing: float
    plant_spacing: float
    damage_polygons: List[ManualPolygon | DronePolygon]


# =========================================================
#                     FASTAPI ENDPOINT
# =========================================================

@app.post("/analyze-damage")
def analyze_damage(data: DamageRequest):

    plant_density = 1 / (data.row_spacing * data.plant_spacing)
    total_plants = plant_density * data.farm_area_m2

    total_damage_area = 0
    total_lost_plants = 0

    for d in data.damage_polygons:

        if d.type == "manual":
            poly = d.points

        else:
            poly = camera_footprint_from_drone(
                d.drone_lat, d.drone_lon,
                d.drone_alt_msl,
                d.compass_heading_deg,
                d.mount_tilt_deg,
                d.vertical_fov_deg,
                d.horizontal_fov_deg
            )

        area = polygon_area_from_latlon(poly)
        lost = area * plant_density

        total_damage_area += area
        total_lost_plants += lost

    surviving = total_plants - total_lost_plants
    yield_percentage = (surviving / total_plants) * 100
    damage_percentage = 100 - yield_percentage

    return {
        "farm_area_m2": data.farm_area_m2,
        "total_plants": total_plants,
        "total_damage_area": total_damage_area,
        "total_lost_plants": total_lost_plants,
        "remaining_plants": surviving,
        "yield_remaining_percent": yield_percentage,
        "yield_lost_percent": damage_percentage,
        "total_rice_kg": surviving * 0.014
    }
