import os
import math
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="SmartCycle AI", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")

# ── Pydantic Models ──────────────────────────────────────────────────────────

class OptimizeRequest(BaseModel):
    location: str                     # city name, e.g. "Mumbai"
    mix_grade: str                    # "M30", "M40", etc.
    target_strength_mpa: float        # MPa at demould
    curing_method: str                # "steam" | "ambient"
    energy_cost_per_kwh: float        # ₹ per kWh
    labor_cost_per_hour: float        # ₹ per hour
    material_cost_per_element: float  # ₹ total material cost
    elements_per_month: int           # yard throughput

class OptimizeResponse(BaseModel):
    location: str
    current_temp_c: float
    humidity_pct: float
    weather_desc: str
    is_live_weather: bool
    baseline_hours: float
    optimized_hours: float
    baseline_kwh: float
    optimized_kwh: float
    energy_saved_kwh: float
    baseline_cost_per_element: float
    optimized_cost_per_element: float
    savings_per_element: float
    monthly_savings: float
    annual_savings: float
    co2_saved_kg_per_month: float
    throughput_increase_pct: float
    strength_at_demould_mpa: float
    maturity_index: float
    recommendation: str


# ── Maturity Model ───────────────────────────────────────────────────────────

def compute_maturity(temp_c: float, hours: float, datum_temp_c: float = -10.0) -> float:
    """
    Nurse–Saul maturity index  M(t) = Σ(T − T₀) × Δt
    Simplified: constant ambient temperature over the curing duration.
    """
    return (temp_c - datum_temp_c) * hours  # °C·hours


def strength_from_maturity(maturity: float, mix_grade: str) -> float:
    """
    Hyperbolic strength–maturity relationship.
    S(M) = Su × (M − M0) / (k + M − M0)
    Parameters tuned per mix grade for a hackathon-level model.
    """
    # Su = ultimate strength (MPa), M0 = maturity at start of strength gain (°C·h),
    # k = rate constant (°C·h). Calibrated for Indian precast conditions.
    grade_params = {
        "M20": {"Su": 22.0, "M0": 50, "k": 280},
        "M25": {"Su": 27.0, "M0": 55, "k": 310},
        "M30": {"Su": 33.0, "M0": 60, "k": 340},
        "M35": {"Su": 38.0, "M0": 65, "k": 370},
        "M40": {"Su": 44.0, "M0": 70, "k": 400},
        "M45": {"Su": 49.0, "M0": 80, "k": 430},
        "M50": {"Su": 55.0, "M0": 90, "k": 460},
    }
    p = grade_params.get(mix_grade.upper(), grade_params["M30"])
    effective = max(maturity - p["M0"], 0)
    if effective == 0:
        return 0.0
    strength = p["Su"] * effective / (p["k"] + effective)
    return round(strength, 2)


def find_optimal_hours(
    temp_c: float,
    mix_grade: str,
    target_strength_mpa: float,
    curing_method: str,
) -> float:
    """
    Binary search: find minimum hours to reach target strength.
    Steam curing gets a temperature boost of +20°C effective (faster hydration).
    """
    effective_temp = temp_c + (20 if curing_method == "steam" else 0)
    # clamp to safe range
    effective_temp = max(-5, min(45, effective_temp))

    low, high = 1.0, 48.0
    for _ in range(50):
        mid = (low + high) / 2
        m = compute_maturity(effective_temp, mid)
        s = strength_from_maturity(m, mix_grade)
        if s >= target_strength_mpa:
            high = mid
        else:
            low = mid

    return round(high, 1)


# ── Energy Model ─────────────────────────────────────────────────────────────

def compute_energy(hours: float, curing_method: str, temp_c: float) -> float:
    """
    Rough energy model:
    - Steam: base 180 kWh + 2.5 kWh/hour; reduced at higher ambient temps
    - Ambient: 10 kWh (fans/sensors only)
    """
    if curing_method == "steam":
        ambient_factor = max(0.7, 1.0 - (temp_c - 20) * 0.01)
        return round((180 + 2.5 * hours) * ambient_factor, 1)
    else:
        return round(10 + 0.5 * hours, 1)


# ── Weather Fetch ─────────────────────────────────────────────────────────────

async def fetch_weather(location: str) -> dict:
    """Fetch current weather from OpenWeatherMap. Falls back to defaults if no API key."""
    if not OPENWEATHER_API_KEY or OPENWEATHER_API_KEY == "your_api_key_here":
        # Demo fallback — deterministic mock based on location hash
        seed = sum(ord(c) for c in location)
        temp = 18 + (seed % 20)   # 18–37°C
        humidity = 50 + (seed % 40)  # 50–90%
        return {
            "temp_c": float(temp),
            "humidity": float(humidity),
            "description": "partly cloudy (demo mode)",
            "demo": True,
        }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params={"q": location, "appid": OPENWEATHER_API_KEY, "units": "metric"},
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "temp_c": data["main"]["temp"],
                "humidity": data["main"]["humidity"],
                "description": data["weather"][0]["description"],
                "demo": False,
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Weather fetch failed: {e}")


# ── Main Endpoint ─────────────────────────────────────────────────────────────

@app.post("/optimize", response_model=OptimizeResponse)
async def optimize(req: OptimizeRequest):
    weather = await fetch_weather(req.location)
    temp_c = weather["temp_c"]
    humidity = weather["humidity"]

    BASELINE_HOURS = 24.0
    baseline_kwh = compute_energy(BASELINE_HOURS, req.curing_method, temp_c)

    opt_hours = find_optimal_hours(
        temp_c, req.mix_grade, req.target_strength_mpa, req.curing_method
    )
    # Never recommend more than baseline
    opt_hours = min(opt_hours, BASELINE_HOURS)
    opt_kwh = compute_energy(opt_hours, req.curing_method, temp_c)

    # Cost model
    saved_kwh = round(baseline_kwh - opt_kwh, 1)
    saved_hours = BASELINE_HOURS - opt_hours
    energy_saving = saved_kwh * req.energy_cost_per_kwh
    labor_saving = saved_hours * req.labor_cost_per_hour
    holding_saving = saved_hours * 50  # ₹50/hour yard holding cost (fixed)
    savings_per_element = round(energy_saving + labor_saving + holding_saving, 0)

    monthly_savings = round(savings_per_element * req.elements_per_month, 0)
    annual_savings = round(monthly_savings * 12, 0)

    baseline_cost = req.material_cost_per_element + (baseline_kwh * req.energy_cost_per_kwh)
    optimized_cost = req.material_cost_per_element + (opt_kwh * req.energy_cost_per_kwh)

    # CO₂: India grid ~0.82 kg CO₂/kWh
    co2_saved = round(saved_kwh * 0.82 * req.elements_per_month, 1)

    # Throughput: fewer hours = more cycles possible
    throughput_increase = round((BASELINE_HOURS / opt_hours - 1) * 100, 1) if opt_hours > 0 else 0

    # Best strength at opt_hours (for display)
    effective_temp = temp_c + (20 if req.curing_method == "steam" else 0)
    mat = compute_maturity(effective_temp, opt_hours)
    strength = strength_from_maturity(mat, req.mix_grade)

    hours_saved_display = round(BASELINE_HOURS - opt_hours, 1)
    recommendation = (
        f"Demould at {opt_hours}h instead of 24h — save {hours_saved_display}h per cycle. "
        f"Reduce {'steam' if req.curing_method == 'steam' else 'energy'} usage by {saved_kwh} kWh. "
        f"Save ₹{int(savings_per_element):,} per element."
    )

    return OptimizeResponse(
        location=req.location,
        current_temp_c=round(temp_c, 1),
        humidity_pct=round(humidity, 1),
        weather_desc=weather["description"],
        is_live_weather=not weather.get("demo", True),
        baseline_hours=BASELINE_HOURS,
        optimized_hours=opt_hours,
        baseline_kwh=baseline_kwh,
        optimized_kwh=opt_kwh,
        energy_saved_kwh=saved_kwh,
        baseline_cost_per_element=round(baseline_cost, 0),
        optimized_cost_per_element=round(optimized_cost, 0),
        savings_per_element=savings_per_element,
        monthly_savings=monthly_savings,
        annual_savings=annual_savings,
        co2_saved_kg_per_month=co2_saved,
        throughput_increase_pct=throughput_increase,
        strength_at_demould_mpa=strength,
        maturity_index=round(mat, 0),
        recommendation=recommendation,
    )


@app.get("/weather/{city}")
async def get_weather(city: str):
    """Get current weather for a city."""
    data = await fetch_weather(city)
    return {
        "city": city,
        "temperature_c": data["temp_c"],
        "humidity_pct": data["humidity"],
        "description": data["description"],
        "is_live": not data.get("demo", True),
        "source": "OpenWeatherMap" if not data.get("demo") else "Demo (add OPENWEATHER_API_KEY to .env)",
    }


@app.get("/weather/{city}/forecast")
async def get_forecast(city: str):
    """Get 48-hour temperature forecast for a city (3-hour intervals)."""
    if not OPENWEATHER_API_KEY or OPENWEATHER_API_KEY == "your_api_key_here":
        # Generate plausible demo forecast
        import random
        seed = sum(ord(c) for c in city)
        base_temp = 18 + (seed % 20)
        forecast = []
        for i in range(16):  # 16 × 3h = 48h
            hour = i * 3
            variation = round((seed * (i + 1) % 7) - 3 + (1 if hour < 12 else -1), 1)
            forecast.append({
                "hour_offset": hour,
                "label": f"+{hour}h",
                "temp_c": base_temp + variation,
                "humidity_pct": 55 + (seed * i % 30),
            })
        return {"city": city, "forecast": forecast, "is_live": False}

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            # Get city coordinates first
            geo = await client.get(
                "https://api.openweathermap.org/geo/1.0/direct",
                params={"q": city, "limit": 1, "appid": OPENWEATHER_API_KEY},
            )
            geo.raise_for_status()
            geo_data = geo.json()
            if not geo_data:
                raise HTTPException(status_code=404, detail=f"City '{city}' not found")
            lat, lon = geo_data[0]["lat"], geo_data[0]["lon"]

            # Fetch 5-day / 3-hour forecast
            resp = await client.get(
                "https://api.openweathermap.org/data/2.5/forecast",
                params={"lat": lat, "lon": lon, "appid": OPENWEATHER_API_KEY, "units": "metric", "cnt": 16},
            )
            resp.raise_for_status()
            raw = resp.json()
            forecast = [
                {
                    "hour_offset": i * 3,
                    "label": f"+{i*3}h",
                    "temp_c": round(item["main"]["temp"], 1),
                    "humidity_pct": item["main"]["humidity"],
                    "description": item["weather"][0]["description"],
                }
                for i, item in enumerate(raw["list"])
            ]
            return {"city": city, "forecast": forecast, "is_live": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Forecast fetch failed: {e}")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "SmartCycle AI", "weather_api": "live" if (OPENWEATHER_API_KEY and OPENWEATHER_API_KEY != "your_api_key_here") else "demo"}


@app.get("/")
async def root():
    return {"message": "SmartCycle AI API — POST /optimize to get curing recommendations"}
