# ⚡ SmartCycle AI — Hackathon MVP

> AI-powered curing cycle optimization for precast concrete yards.

## 🚀 Quick Start (2 terminals)

### Terminal 1 — Backend

```bash
cd backend
pip install -r requirements.txt

# Optional: add your OpenWeatherMap API key for live weather
cp .env.example .env
# edit .env → OPENWEATHER_API_KEY=your_key_here

uvicorn main:app --reload
```

Backend runs at → **http://localhost:8000**
API docs at → **http://localhost:8000/docs**

### Terminal 2 — Frontend

```bash
cd frontend
# Just open index.html in your browser, OR serve it:
npx serve .
```

Frontend runs at → **http://localhost:3000** (or open `index.html` directly)

---

## 🧠 What the AI Does

1. **Fetches live weather** for the yard location (temp + humidity)
2. **Runs Nurse–Saul maturity model**: `M(t) = Σ(T − T₀) × Δt`
3. **Predicts concrete strength** using hyperbolic strength-maturity curve
4. **Finds minimum curing hours** via binary search to hit target demould strength
5. **Calculates cost savings**: energy + labor + yard holding cost
6. **Returns**: recommended hours, kWh saved, ₹ saved/element, monthly savings, CO₂ impact

---

## 📊 Demo Numbers (500 elements/month yard)

| Metric | Baseline | Optimized |
|--------|----------|-----------|
| Cycle Time | 24h | ~19h |
| Energy/Batch | 220 kWh | ~185 kWh |
| Cost/Element | ₹20,000 | ~₹17,600 |
| Monthly Savings | — | **₹32L** |

---

## 📁 Project Structure

```
smartcycle-ai/
├── backend/
│   ├── main.py           ← FastAPI app (maturity model + optimizer)
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    └── index.html        ← Beautiful single-page dashboard
```

---

## 🔑 Get Free Weather API Key

1. Sign up at [openweathermap.org](https://openweathermap.org/api)
2. Copy your API key → paste in `backend/.env`
3. Restart backend

Without a key, the app runs in **demo mode** with realistic mock weather.
