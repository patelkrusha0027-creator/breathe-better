# 🍃 Breathe Better — Real-Time Air Quality Intelligence Platform

Built for the **Lets Code Diwali Challenge 2026**. Live air quality tracking, 3-day forecasts,
multi-city mapping, personal health-risk scoring, email alerts and PDF/CSV reports — all from
authentic public data. **No simulated or random values anywhere.**

## Features
| Tab | What it does | Data |
|---|---|---|
| Dashboard | Live AQI, PM2.5/PM10/NO₂/CO, 7-day hourly trend | WAQI station feed (+ Open-Meteo history) |
| AQI Map | Multi-city bubble map coloured by AQI | WAQI / Open-Meteo |
| Heatmap | Day × hour-of-day AQI; shows the daily worst hour | Open-Meteo |
| Forecast | 3-day hourly forecast + official WAQI daily forecast | CAMS model via Open-Meteo, WAQI |
| Health Risk | Transparent rule-based exposure score (age, conditions, hours outside, mask) | Live AQI |
| Alerts | Threshold email alerts over SMTP | Live AQI |
| Leaderboard | Cities ranked cleanest → most polluted | WAQI / Open-Meteo |
| Reports | PDF summary + CSV export | Live + historical |

## Data sources
* **[WAQI / aqicn.org](https://aqicn.org/api/)** — real-time station readings (free token)
* **[Open-Meteo Air Quality API](https://open-meteo.com/en/docs/air-quality-api)** — hourly history and forecast, no key
* **[Open-Meteo Geocoding](https://open-meteo.com/en/docs/geocoding-api)** — city → coordinates

Works without a WAQI token (falls back to Open-Meteo); adding a token enables station-level readings and the WAQI forecast.
AQI follows the US EPA scale.

## Run locally
```bash
git clone <your-repo-url> && cd breathe-better
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then fill in values
streamlit run app.py
```

## Deploy (shareable link)
1. Push this folder to a GitHub repo (`secrets.toml` is git-ignored).
2. Go to [share.streamlit.io](https://share.streamlit.io) → *New app* → pick the repo, main file `app.py`.
3. Under *Advanced settings → Secrets*, paste the contents of `secrets.toml.example` with real values.
4. Share the generated URL.

## Security notes
* API tokens and SMTP passwords live in Streamlit secrets / env vars — never in source.
* Use a Gmail **App Password**, not your account password.

## Limitations (honest notes)
* Health risk is a heuristic for awareness, not medical advice.
* Streamlit only executes on page interaction; for always-on alerting, run the threshold check from cron / GitHub Actions.
* Open-Meteo values are model-based (CAMS), not ground sensors; WAQI values come from ground stations.
