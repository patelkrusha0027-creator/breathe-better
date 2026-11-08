"""
Breathe Better - Real-Time Air Quality Intelligence Platform

Data sources (all real, no simulated values):
  * WAQI / aqicn.org   - live station readings + official daily forecast (token required)
  * Open-Meteo Air Quality API - hourly history (past 7 days) + 3-day hourly forecast (no key)
  * Open-Meteo Geocoding API   - city -> coordinates (no key)

Without a WAQI token the app still works end-to-end using Open-Meteo only.
"""

import io
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# ==================================================
# PAGE CONFIG
# ==================================================
st.set_page_config(page_title="Breathe Better", page_icon="🍃", layout="wide")

WAQI_BASE = "https://api.waqi.info"
OM_AQ = "https://air-quality-api.open-meteo.com/v1/air-quality"
OM_GEO = "https://geocoding-api.open-meteo.com/v1/search"

DEFAULT_CITIES = [
    "Delhi", "Mumbai", "Kolkata", "Chennai", "Bengaluru", "Hyderabad",
    "Ahmedabad", "Surat", "Pune", "Lucknow", "Jaipur", "Patna",
]

# US EPA AQI bands: (upper bound, label, colour, advice)
BANDS = [
    (50, "Good", "#2e9e4f", "Air quality is satisfactory. Enjoy outdoor activities."),
    (100, "Moderate", "#d4b106", "Unusually sensitive people should reduce prolonged outdoor exertion."),
    (150, "Unhealthy for Sensitive Groups", "#e67e22",
     "Children, older adults and people with asthma/heart disease should limit outdoor exertion."),
    (200, "Unhealthy", "#d93025",
     "Everyone should reduce prolonged outdoor exertion. Consider an N95 mask outdoors."),
    (300, "Very Unhealthy", "#8e44ad", "Avoid outdoor activity. Keep windows closed; use an air purifier."),
    (10_000, "Hazardous", "#7b1c1c", "Health emergency. Stay indoors with filtered air."),
]


# ==================================================
# SECRETS / CONFIG
# ==================================================
def _secret(key, default=""):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return os.environ.get(key, default)


def _smtp_cfg():
    try:
        cfg = dict(st.secrets.get("smtp", {}))
    except Exception:
        cfg = {}
    return {
        "host": cfg.get("host", os.environ.get("SMTP_HOST", "smtp.gmail.com")),
        "port": int(cfg.get("port", os.environ.get("SMTP_PORT", 587))),
        "user": cfg.get("user", os.environ.get("SMTP_USER", "")),
        "password": cfg.get("password", os.environ.get("SMTP_PASSWORD", "")),
    }


# ==================================================
# HELPERS
# ==================================================
def band(aqi):
    for upper, label, colour, advice in BANDS:
        if aqi <= upper:
            return label, colour, advice
    return BANDS[-1][1:]


def to_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def geocode(city):
    """Open-Meteo geocoding -> dict(name, lat, lon, country) or None."""
    try:
        r = requests.get(OM_GEO, params={"name": city, "count": 1, "language": "en"}, timeout=10)
        r.raise_for_status()
        res = (r.json().get("results") or [None])[0]
        if not res:
            return None
        return {"name": res["name"], "lat": res["latitude"], "lon": res["longitude"],
                "country": res.get("country", "")}
    except requests.RequestException:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def waqi_feed(city, token):
    """Live WAQI reading. Returns data dict or None."""
    if not token:
        return None
    try:
        r = requests.get(f"{WAQI_BASE}/feed/{requests.utils.quote(city)}/",
                         params={"token": token}, timeout=10)
        j = r.json()
        if j.get("status") == "ok" and to_num(j["data"].get("aqi")) is not None:
            return j["data"]
    except (requests.RequestException, ValueError, KeyError):
        pass
    return None


@st.cache_data(ttl=900, show_spinner=False)
def open_meteo(lat, lon):
    """Hourly pollutants: last 7 days + next 3 days, plus current block."""
    try:
        r = requests.get(OM_AQ, params={
            "latitude": lat, "longitude": lon, "timezone": "auto",
            "hourly": "us_aqi,pm2_5,pm10,nitrogen_dioxide,ozone,carbon_monoxide,sulphur_dioxide",
            "current": "us_aqi,pm2_5,pm10,nitrogen_dioxide,ozone,carbon_monoxide",
            "past_days": 7, "forecast_days": 3,
        }, timeout=15)
        r.raise_for_status()
        j = r.json()
        df = pd.DataFrame(j["hourly"])
        df["time"] = pd.to_datetime(df["time"])
        return df, j.get("current", {})
    except (requests.RequestException, ValueError, KeyError):
        return None, {}


def snapshot(city, token):
    """Unified current-conditions record for one city (WAQI first, Open-Meteo fallback)."""
    d = waqi_feed(city, token)
    if d:
        geo = d.get("city", {}).get("geo") or [None, None]
        iaqi = d.get("iaqi", {})
        return {
            "city": city, "station": d["city"]["name"], "lat": geo[0], "lon": geo[1],
            "aqi": to_num(d["aqi"]), "source": "WAQI",
            "pm25": iaqi.get("pm25", {}).get("v"), "pm10": iaqi.get("pm10", {}).get("v"),
            "no2": iaqi.get("no2", {}).get("v"), "co": iaqi.get("co", {}).get("v"),
            "o3": iaqi.get("o3", {}).get("v"), "updated": d.get("time", {}).get("s", ""),
            "forecast": d.get("forecast", {}).get("daily", {}),
        }
    g = geocode(city)
    if not g:
        return None
    _, cur = open_meteo(g["lat"], g["lon"])
    if not cur or cur.get("us_aqi") is None:
        return None
    return {
        "city": city, "station": f'{g["name"]}, {g["country"]}', "lat": g["lat"], "lon": g["lon"],
        "aqi": float(cur["us_aqi"]), "source": "Open-Meteo (CAMS model)",
        "pm25": cur.get("pm2_5"), "pm10": cur.get("pm10"), "no2": cur.get("nitrogen_dioxide"),
        "co": cur.get("carbon_monoxide"), "o3": cur.get("ozone"),
        "updated": cur.get("time", ""), "forecast": {},
    }


@st.cache_data(ttl=300, show_spinner="Fetching cities…")
def multi_snapshot(cities, token):
    rows = [snapshot(c, token) for c in cities]
    return pd.DataFrame([r for r in rows if r and r["lat"] is not None]).drop(columns=["forecast"], errors="ignore")


def health_risk(age, conditions, outdoor_hours, aqi, uses_mask):
    """Transparent rule-based score (not a medical device)."""
    score = aqi / 10
    if age >= 60 or age <= 12:
        score += 20
    score += 8 * len(conditions)
    score += outdoor_hours * 2.5
    if uses_mask:
        score *= 0.8
    if score < 30:
        return "LOW", "🟢", score
    if score < 60:
        return "MEDIUM", "🟡", score
    if score < 90:
        return "HIGH", "🟠", score
    return "CRITICAL", "🔴", score


def send_email(receiver, city, aqi, advice):
    cfg = _smtp_cfg()
    if not cfg["user"] or not cfg["password"]:
        return False, "SMTP credentials not configured (see README)."
    try:
        msg = MIMEText(
            f"Air Quality Alert\n\nCity: {city}\nAQI: {aqi:.0f}\n\nRecommendation: {advice}\n\n"
            f"Sent by Breathe Better at {datetime.now():%Y-%m-%d %H:%M}."
        )
        msg["Subject"] = f"AQI Alert - {city} ({aqi:.0f})"
        msg["From"], msg["To"] = cfg["user"], receiver
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as s:
            s.starttls()
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
        return True, "Alert sent."
    except Exception as e:  # noqa: BLE001
        return False, f"Email error: {e}"


def build_pdf(snap, label, advice, weekly_avg):
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    pdf.setTitle("Breathe Better AQI Report")
    y = 800
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(50, y, "Breathe Better - AQI Report")
    pdf.setFont("Helvetica", 11)
    lines = [
        f"Location: {snap['station']}",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M}",
        f"Data source: {snap['source']}",
        "",
        f"Current AQI: {snap['aqi']:.0f}  ({label})",
        f"PM2.5: {snap['pm25']}   PM10: {snap['pm10']}   NO2: {snap['no2']}   O3: {snap['o3']}",
    ]
    if weekly_avg is not None:
        lines.append(f"7-day average AQI: {weekly_avg:.0f}")
    lines += ["", "Recommendation:", advice, "", "AQI scale: US EPA. Data: WAQI, Open-Meteo."]
    y -= 40
    for ln in lines:
        pdf.drawString(50, y, ln[:100])
        y -= 20
    pdf.save()
    buf.seek(0)
    return buf


# ==================================================
# SIDEBAR
# ==================================================
st.title("🍃 Breathe Better")
st.caption("Real-time air quality intelligence · WAQI + Open-Meteo · no simulated data")

st.sidebar.header("Configuration")
token = st.sidebar.text_input(
    "WAQI API token (optional)", type="password", value=_secret("WAQI_TOKEN"),
    help="Free at aqicn.org/data-platform/token. Without it, Open-Meteo is used.",
)
city = st.sidebar.text_input("City", value="Delhi").strip()
st.sidebar.markdown(
    "**Sources**\n- [WAQI](https://aqicn.org/api/)\n- [Open-Meteo Air Quality](https://open-meteo.com/en/docs/air-quality-api)"
)
if st.sidebar.button("🔄 Refresh data"):
    st.cache_data.clear()
    st.rerun()

snap = snapshot(city, token) if city else None
if not snap:
    st.error("Could not find data for that city. Check the spelling or your connection.")
    st.stop()

hist, cur_om = open_meteo(snap["lat"], snap["lon"])
# Use the API's own local clock so 'past' vs 'forecast' is right regardless of server timezone
now_loc = pd.Timestamp(cur_om["time"]) if cur_om.get("time") else pd.Timestamp.now()
aqi = snap["aqi"]
label, colour, advice = band(aqi)

tabs = st.tabs(["📊 Dashboard", "🗺️ AQI Map", "🔥 Heatmap", "📈 Forecast",
                "❤️ Health Risk", "📧 Alerts", "🏆 Leaderboard", "📄 Reports"])

# ==================================================
# DASHBOARD
# ==================================================
with tabs[0]:
    st.subheader(snap["station"])
    st.caption(f"Source: {snap['source']} · Updated: {snap['updated']}")
    c = st.columns(5)
    c[0].metric("AQI (US)", f"{aqi:.0f}")
    c[1].metric("PM2.5", snap["pm25"] if snap["pm25"] is not None else "N/A")
    c[2].metric("PM10", snap["pm10"] if snap["pm10"] is not None else "N/A")
    c[3].metric("NO₂", snap["no2"] if snap["no2"] is not None else "N/A")
    c[4].metric("CO", snap["co"] if snap["co"] is not None else "N/A")
    st.markdown(
        f"<div style='padding:12px 16px;border-radius:8px;background:{colour};color:white'>"
        f"<b>{label}</b> — {advice}</div>", unsafe_allow_html=True)

    if hist is not None:
        past = hist[hist["time"] <= now_loc]
        st.markdown("#### Last 7 days (hourly, observed/analysed)")
        fig = px.line(past, x="time", y="us_aqi", labels={"us_aqi": "US AQI", "time": ""})
        fig.update_traces(line_color=colour)
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("#### Pollutants (µg/m³)")
        fig2 = px.line(past, x="time", y=["pm2_5", "pm10", "nitrogen_dioxide", "ozone"],
                       labels={"value": "µg/m³", "time": "", "variable": ""})
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.warning("Historical data temporarily unavailable.")

# ==================================================
# MAP
# ==================================================
with tabs[1]:
    sel = st.multiselect("Cities to compare", DEFAULT_CITIES + ([city] if city not in DEFAULT_CITIES else []),
                         default=DEFAULT_CITIES[:6])
    if sel:
        df = multi_snapshot(tuple(sel), token)
        if df.empty:
            st.warning("No data returned.")
        else:
            df["Category"] = df["aqi"].apply(lambda v: band(v)[0])
            fig = px.scatter_map(
                df, lat="lat", lon="lon", size="aqi", color="aqi", hover_name="station",
                hover_data={"aqi": ":.0f", "Category": True, "lat": False, "lon": False},
                color_continuous_scale=["#2e9e4f", "#d4b106", "#e67e22", "#d93025", "#8e44ad", "#7b1c1c"],
                range_color=[0, 300], size_max=40, zoom=4, height=560, map_style="open-street-map",
            )
            st.plotly_chart(fig, use_container_width=True)

# ==================================================
# HEATMAP
# ==================================================
with tabs[2]:
    st.subheader(f"AQI by hour of day · {snap['city']}")
    if hist is not None:
        past = hist[hist["time"] <= now_loc].copy()
        past["Date"] = past["time"].dt.strftime("%b %d")
        past["Hour"] = past["time"].dt.hour
        pivot = past.pivot_table(index="Date", columns="Hour", values="us_aqi", sort=False)
        fig = px.imshow(pivot, aspect="auto", color_continuous_scale="YlOrRd",
                        labels={"color": "US AQI", "x": "Hour", "y": ""})
        st.plotly_chart(fig, use_container_width=True)
        worst = past.groupby("Hour")["us_aqi"].mean().idxmax()
        st.info(f"Highest average AQI this week occurs around **{worst:02d}:00** local time.")
    else:
        st.warning("Data unavailable.")

# ==================================================
# FORECAST
# ==================================================
with tabs[3]:
    st.subheader("3-day hourly forecast (CAMS atmospheric model via Open-Meteo)")
    if hist is not None:
        fut = hist[hist["time"] >= now_loc.floor("h")]
        fig = px.line(fut, x="time", y="us_aqi", labels={"us_aqi": "Forecast US AQI", "time": ""})
        fig.add_hline(y=100, line_dash="dash", annotation_text="Moderate limit")
        st.plotly_chart(fig, use_container_width=True)
        daily = fut.assign(Day=fut["time"].dt.date).groupby("Day")["us_aqi"].agg(["mean", "max"]).round(0)
        daily.columns = ["Avg AQI", "Peak AQI"]
        st.dataframe(daily, use_container_width=True)
    if snap["forecast"]:
        st.subheader("Official WAQI daily forecast")
        rows = []
        for pol, items in snap["forecast"].items():
            for it in items:
                rows.append({"Pollutant": pol, "Day": it["day"], "Avg": it["avg"],
                             "Min": it["min"], "Max": it["max"]})
        fdf = pd.DataFrame(rows)
        if not fdf.empty:
            st.plotly_chart(px.line(fdf[fdf["Pollutant"].isin(["pm25", "pm10", "o3"])],
                                    x="Day", y="Avg", color="Pollutant", markers=True),
                            use_container_width=True)

# ==================================================
# HEALTH RISK
# ==================================================
with tabs[4]:
    st.subheader("Personal exposure risk")
    a, b = st.columns(2)
    age = a.slider("Age", 1, 100, 30)
    hours = a.slider("Hours outdoors today", 0, 12, 2)
    conds = b.multiselect("Conditions", ["Asthma", "COPD", "Heart disease", "Pregnancy"])
    mask = b.checkbox("I wear an N95 mask outdoors")
    level, icon, score = health_risk(age, conds, hours, aqi, mask)
    st.metric("Risk level", f"{icon} {level}", help=f"Score {score:.0f}")
    st.write(advice)
    st.caption("Heuristic estimate for awareness only — not medical advice.")

# ==================================================
# ALERTS
# ==================================================
with tabs[5]:
    st.subheader("Email alerts")
    threshold = st.slider("Alert threshold (AQI)", 50, 300, 100, 10)
    email = st.text_input("Recipient email")
    if aqi > threshold:
        st.warning(f"{snap['city']} AQI {aqi:.0f} exceeds your threshold of {threshold}.")
    else:
        st.success(f"{snap['city']} AQI {aqi:.0f} is within your threshold of {threshold}.")
    auto = st.checkbox("Auto-send when this page refreshes above threshold")
    if st.button("Send alert now") and email:
        ok, msg = send_email(email, snap["station"], aqi, advice)
        (st.success if ok else st.error)(msg)
    elif auto and email and aqi > threshold:
        key = f"sent-{snap['city']}-{datetime.now():%Y%m%d%H}"
        if key not in st.session_state:
            ok, msg = send_email(email, snap["station"], aqi, advice)
            st.session_state[key] = ok
            (st.success if ok else st.error)(msg)
    st.caption("Streamlit runs on page load; for 24/7 alerts run this check from a scheduler (cron/GitHub Actions).")

# ==================================================
# LEADERBOARD
# ==================================================
with tabs[6]:
    st.subheader("Cleanest → most polluted")
    df = multi_snapshot(tuple(DEFAULT_CITIES), token)
    if not df.empty:
        df = df.sort_values("aqi").reset_index(drop=True)
        df.index += 1
        df["Category"] = df["aqi"].apply(lambda v: band(v)[0])
        st.plotly_chart(px.bar(df, x="aqi", y="city", orientation="h", color="aqi",
                               color_continuous_scale="YlOrRd", labels={"aqi": "US AQI", "city": ""}),
                        use_container_width=True)
        st.dataframe(df[["city", "aqi", "Category", "pm25", "pm10", "source"]], use_container_width=True)

# ==================================================
# REPORTS
# ==================================================
with tabs[7]:
    st.subheader("Downloadable reports")
    weekly = None
    if hist is not None:
        weekly = hist[hist["time"] <= now_loc]["us_aqi"].mean()
    pdf = build_pdf(snap, label, advice, weekly)
    st.download_button("📄 Download PDF report", pdf, file_name=f"aqi_{snap['city']}.pdf",
                       mime="application/pdf")
    if hist is not None:
        st.download_button("📊 Download hourly data (CSV)", hist.to_csv(index=False),
                           file_name=f"aqi_{snap['city']}.csv", mime="text/csv")
