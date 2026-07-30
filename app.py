from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
import os, json, threading, time, hashlib
import requests as http_requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from meta_api import ACCOUNTS, get_insights, BASE_URL
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Metric, Dimension
from google.oauth2.credentials import Credentials
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()
app = Flask(__name__, static_folder='Triumph_dashboard', static_url_path='/Triumph_dashboard')
CORS(app)

META_ACCOUNT = "629806431400540"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)
TRIUMPH_CREATIVES = os.environ.get(
    "TRIUMPH_CREATIVES",
    os.path.join(os.path.dirname(__file__), "Triumph_dashboard", "creatives")
)

TZ_TAIPEI = timezone(timedelta(hours=8))
GA4_PROPERTY = "178359594"
CACHE_TTL = 1800  # 30 分鐘

# ── Cache helpers ─────────────────────────────────────────────

def taipei_now():
    return datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M")

def cache_path(key):
    return os.path.join(CACHE_DIR, f"{key}.json")

def read_cache(key, ttl=None):
    p = cache_path(key)
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            data = json.load(f)
    except Exception:
        return None
    if ttl and data.get("_ts"):
        if time.time() - data["_ts"] > ttl:
            return None
    return data

def write_cache(key, data):
    data["_ts"] = time.time()
    with open(cache_path(key), "w") as f:
        json.dump(data, f, ensure_ascii=False)

def ga4_key(endpoint, since, until):
    return f"ga4_{endpoint}_{since}_{until}".replace("-", "")

def meta_key(account_id, since, until, level, time_increment):
    return f"meta_{account_id}_{since}_{until}_{level}_{time_increment}".replace("-", "")

def with_cache(cache_key, fetch_fn, ttl=CACHE_TTL):
    cached = read_cache(cache_key, ttl=ttl)
    if cached:
        return jsonify({"data": cached["data"], "cached": True, "updated": cached.get("updated", "")})
    result = fetch_fn()
    write_cache(cache_key, {"data": result, "updated": taipei_now()})
    return jsonify({"data": result, "cached": False, "updated": taipei_now()})

# ── GA4 client ────────────────────────────────────────────────

def ga4_client():
    creds = Credentials(
        token=None,
        refresh_token=os.environ.get("GA4_REFRESH_TOKEN"),
        client_id=os.environ.get("GA4_CLIENT_ID"),
        client_secret=os.environ.get("GA4_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token",
    )
    return BetaAnalyticsDataClient(credentials=creds)

# ── GA4 fetch helpers (pure data, no HTTP response) ───────────

def _fetch_ga4_daily(since, until):
    client = ga4_client()
    req = RunReportRequest(
        property=f"properties/{GA4_PROPERTY}",
        date_ranges=[DateRange(start_date=since, end_date=until)],
        dimensions=[Dimension(name="date")],
        metrics=[
            Metric(name="sessions"),
            Metric(name="activeUsers"),
            Metric(name="screenPageViews"),
            Metric(name="keyEvents:add_to_cart"),
            Metric(name="ecommercePurchases"),
            Metric(name="totalRevenue"),
            Metric(name="bounceRate"),
            Metric(name="averageSessionDuration"),
            Metric(name="newUsers"),
        ],
    )
    resp = ga4_client().run_report(req)
    rows = []
    for row in resp.rows:
        d = row.dimension_values[0].value
        m = row.metric_values
        rows.append({
            "date":        d,
            "sessions":    float(m[0].value),
            "users":       float(m[1].value),
            "pageviews":   float(m[2].value),
            "addToCarts":  float(m[3].value),
            "conversions": float(m[4].value),
            "totalRevenue":float(m[5].value),
            "bounceRate":  float(m[6].value),
            "averageSessionDuration": float(m[7].value),
            "new_users":   float(m[8].value),
        })
    rows.sort(key=lambda x: x["date"])
    return rows

def _fetch_ga4_daily_channels(since, until):
    req = RunReportRequest(
        property=f"properties/{GA4_PROPERTY}",
        date_ranges=[DateRange(start_date=since, end_date=until)],
        dimensions=[Dimension(name="date"), Dimension(name="sessionDefaultChannelGrouping")],
        metrics=[
            Metric(name="sessions"),
            Metric(name="keyEvents:add_to_cart"),
            Metric(name="ecommercePurchases"),
            Metric(name="totalRevenue"),
            Metric(name="averageSessionDuration"),
            Metric(name="bounceRate"),
        ],
    )
    resp = ga4_client().run_report(req)
    rows = []
    for row in resp.rows:
        d = row.dimension_values[0].value
        m = row.metric_values
        rows.append({
            "date": d,
            "sessionDefaultChannelGrouping": row.dimension_values[1].value,
            "sessions":    float(m[0].value),
            "addToCarts":  float(m[1].value),
            "conversions": float(m[2].value),
            "totalRevenue":float(m[3].value),
            "averageSessionDuration": float(m[4].value),
            "bounceRate":  float(m[5].value),
        })
    return rows

def _fetch_ga4_daily_sources(since, until):
    req = RunReportRequest(
        property=f"properties/{GA4_PROPERTY}",
        date_ranges=[DateRange(start_date=since, end_date=until)],
        dimensions=[Dimension(name="date"), Dimension(name="sessionSourceMedium")],
        metrics=[
            Metric(name="sessions"),
            Metric(name="keyEvents:add_to_cart"),
            Metric(name="ecommercePurchases"),
            Metric(name="totalRevenue"),
            Metric(name="averageSessionDuration"),
            Metric(name="bounceRate"),
        ],
    )
    resp = ga4_client().run_report(req)
    rows = []
    for row in resp.rows:
        d = row.dimension_values[0].value
        m = row.metric_values
        rows.append({
            "date": d,
            "sessionSourceMedium": row.dimension_values[1].value,
            "sessions":    float(m[0].value),
            "addToCarts":  float(m[1].value),
            "conversions": float(m[2].value),
            "totalRevenue":float(m[3].value),
            "averageSessionDuration": float(m[4].value),
            "bounceRate":  float(m[5].value),
        })
    return rows

def _fetch_ga4_items(since, until):
    req = RunReportRequest(
        property=f"properties/{GA4_PROPERTY}",
        date_ranges=[DateRange(start_date=since, end_date=until)],
        dimensions=[Dimension(name="date"), Dimension(name="itemName")],
        metrics=[
            Metric(name="itemsViewed"),
            Metric(name="keyEvents:add_to_cart"),
            Metric(name="itemsPurchased"),
            Metric(name="itemRevenue"),
        ],
    )
    resp = ga4_client().run_report(req)
    rows = []
    for row in resp.rows:
        d = row.dimension_values[0].value
        m = row.metric_values
        rows.append({
            "date": d,
            "itemName":       row.dimension_values[1].value,
            "itemsViewed":    float(m[0].value),
            "addToCarts":     float(m[1].value),
            "itemsPurchased": float(m[2].value),
            "itemRevenue":    float(m[3].value),
        })
    return rows

def _fetch_ga4_search(since, until):
    req = RunReportRequest(
        property=f"properties/{GA4_PROPERTY}",
        date_ranges=[DateRange(start_date=since, end_date=until)],
        dimensions=[Dimension(name="date"), Dimension(name="searchTerm")],
        metrics=[Metric(name="eventCount"), Metric(name="sessions")],
    )
    resp = ga4_client().run_report(req)
    rows = []
    for row in resp.rows:
        d = row.dimension_values[0].value
        term = row.dimension_values[1].value
        if not term or term in ('(not set)', ''):
            continue
        m = row.metric_values
        rows.append({"date": d, "searchTerm": term, "eventCount": float(m[0].value), "sessions": float(m[1].value)})
    return rows

def _fetch_ga4_channels(since, until):
    req = RunReportRequest(
        property=f"properties/{GA4_PROPERTY}",
        date_ranges=[DateRange(start_date=since, end_date=until)],
        dimensions=[Dimension(name="sessionDefaultChannelGrouping")],
        metrics=[
            Metric(name="sessions"),
            Metric(name="activeUsers"),
            Metric(name="ecommercePurchases"),
            Metric(name="totalRevenue"),
        ],
    )
    resp = ga4_client().run_report(req)
    rows = []
    for row in resp.rows:
        m = row.metric_values
        rows.append({
            "channel":   row.dimension_values[0].value,
            "sessions":  int(m[0].value),
            "users":     int(m[1].value),
            "purchases": int(float(m[2].value)),
            "revenue":   float(m[3].value),
        })
    rows.sort(key=lambda x: x["sessions"], reverse=True)
    return rows

SOURCE_MAP = {
    ("facebookwm", "soc"): "Meta",
    ("google",     "cpc"): "Google",
}

def _fetch_ga4_sources(since, until):
    req = RunReportRequest(
        property=f"properties/{GA4_PROPERTY}",
        date_ranges=[DateRange(start_date=since, end_date=until)],
        dimensions=[
            Dimension(name="date"),
            Dimension(name="sessionSource"),
            Dimension(name="sessionMedium"),
        ],
        metrics=[
            Metric(name="sessions"),
            Metric(name="activeUsers"),
            Metric(name="ecommercePurchases"),
            Metric(name="totalRevenue"),
        ],
    )
    resp = ga4_client().run_report(req)
    by_channel = {}
    for row in resp.rows:
        d      = row.dimension_values[0].value
        source = row.dimension_values[1].value.lower()
        medium = row.dimension_values[2].value.lower()
        channel = SOURCE_MAP.get((source, medium))
        if not channel:
            continue
        m = row.metric_values
        date_str = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        key = (date_str, channel)
        if key not in by_channel:
            by_channel[key] = {"date": date_str, "channel": channel, "sessions": 0, "users": 0, "purchases": 0, "revenue": 0.0}
        by_channel[key]["sessions"]  += int(m[0].value)
        by_channel[key]["users"]     += int(m[1].value)
        by_channel[key]["purchases"] += int(float(m[2].value))
        by_channel[key]["revenue"]   += float(m[3].value)
    return sorted(by_channel.values(), key=lambda x: (x["date"], x["channel"]))

# ── GA4 routes ────────────────────────────────────────────────

def _ga4_dates(req):
    since = req.args.get("since")
    until = req.args.get("until")
    if not since or not until:
        today = datetime.now(TZ_TAIPEI)
        until = today.strftime("%Y-%m-%d")
        since = (today - timedelta(days=6)).strftime("%Y-%m-%d")
    return since, until

@app.route("/api/ga4")
def api_ga4():
    since, until = _ga4_dates(request)
    try:
        return with_cache(ga4_key("daily", since, until), lambda: _fetch_ga4_daily(since, until))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ga4/daily-channels")
def api_ga4_daily_channels():
    since, until = _ga4_dates(request)
    try:
        return with_cache(ga4_key("daily_channels", since, until), lambda: _fetch_ga4_daily_channels(since, until))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ga4/daily-sources")
def api_ga4_daily_sources():
    since, until = _ga4_dates(request)
    try:
        return with_cache(ga4_key("daily_sources", since, until), lambda: _fetch_ga4_daily_sources(since, until))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ga4/items")
def api_ga4_items():
    since, until = _ga4_dates(request)
    try:
        return with_cache(ga4_key("items", since, until), lambda: _fetch_ga4_items(since, until))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ga4/search")
def api_ga4_search():
    since, until = _ga4_dates(request)
    try:
        return with_cache(ga4_key("search", since, until), lambda: _fetch_ga4_search(since, until))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ga4/channels")
def api_ga4_channels():
    since, until = _ga4_dates(request)
    try:
        return with_cache(ga4_key("channels", since, until), lambda: _fetch_ga4_channels(since, until))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ga4/sources")
def api_ga4_sources():
    since, until = _ga4_dates(request)
    try:
        return with_cache(ga4_key("sources", since, until), lambda: _fetch_ga4_sources(since, until))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Meta insights ─────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("Triumph_dashboard", "ga4_meta.html")

@app.route("/api/insights")
def api_insights():
    account_ids = request.args.getlist("accounts")
    since = request.args.get("since", "2025-01-01")
    until = request.args.get("until")
    level = request.args.get("level", "account")
    time_increment = request.args.get("time_increment", "all_days")
    breakdowns = request.args.get("breakdowns")
    force = request.args.get("force", "false").lower() == "true"
    token = os.environ.get("META_ACCESS_TOKEN")

    if not token:
        return jsonify({"error": "META_ACCESS_TOKEN not set"}), 500

    # 只快取 level=account/campaign 且無 breakdowns 的結果
    if not force and not breakdowns and level in ("account", "campaign"):
        ck = meta_key(",".join(account_ids), since, until, level, time_increment)
        cached = read_cache(ck, ttl=CACHE_TTL)
        if cached:
            return jsonify({"data": cached["data"], "errors": [], "cached": True, "updated": cached.get("updated","")})

    all_results, errors = [], []
    for account_id in account_ids:
        try:
            rows = get_insights(account_id, since, until, level, time_increment, token, breakdowns)
            all_results.extend(rows)
        except Exception as e:
            errors.append({"account_id": account_id, "error": str(e)})

    if not errors and not breakdowns and level in ("account", "campaign"):
        ck = meta_key(",".join(account_ids), since, until, level, time_increment)
        write_cache(ck, {"data": all_results, "updated": taipei_now()})

    return jsonify({"data": all_results, "errors": errors, "cached": False, "updated": taipei_now()})

@app.route("/api/cache_status")
def cache_status():
    result = {}
    for key in ["ad_urls_" + META_ACCOUNT]:
        cached = read_cache(key)
        result[key] = cached.get("updated") if cached else None
    return jsonify(result)

@app.route("/api/ad_urls")
def api_ad_urls():
    account_id = request.args.get("account")
    force = request.args.get("force", "false").lower() == "true"
    token = os.environ.get("META_ACCESS_TOKEN")
    if not token:
        return jsonify({"error": "META_ACCESS_TOKEN not set"}), 500

    cache_key = f"ad_urls_{account_id}"
    if not force:
        cached = read_cache(cache_key)
        if cached:
            return jsonify({"data": cached["data"], "cached": True})

    name_to_cid = {}
    url = f"{BASE_URL}/act_{account_id}/ads"
    params = {
        "access_token": token,
        "fields": "name,creative{id,thumbnail_url}",
        "limit": 100,
    }
    while url:
        r = http_requests.get(url, params=params)
        data = r.json()
        if "error" in data:
            return jsonify({"error": data["error"]["message"]}), 400
        for ad in data.get("data", []):
            name = ad.get("name", "")
            creative = ad.get("creative") or {}
            cid = creative.get("id")
            thumb = creative.get("thumbnail_url")
            if name not in name_to_cid:
                name_to_cid[name] = {"cid": cid, "thumb": thumb}
        url = data.get("paging", {}).get("next")
        params = {}

    unique_cids = list({v["cid"] for v in name_to_cid.values() if v["cid"]})
    cid_to_img = {}
    for i in range(0, len(unique_cids), 50):
        batch = [{"method": "GET", "relative_url": f"{cid}?fields=image_url"} for cid in unique_cids[i:i+50]]
        r = http_requests.post(BASE_URL, data={"access_token": token, "batch": json.dumps(batch)})
        for item in (r.json() or []):
            if item and item.get("code") == 200:
                body = json.loads(item["body"])
                if body.get("image_url"):
                    cid_to_img[body["id"]] = body["image_url"]

    result = {}
    for name, info in name_to_cid.items():
        cid = info["cid"]
        thumb = cid_to_img.get(cid) or info["thumb"]
        result[name] = {"url": None, "thumb": thumb}

    write_cache(cache_key, {"data": result, "updated": taipei_now()})
    return jsonify({"data": result, "count": len(result), "cached": False})


def _embedded_full_url(thumbnail_url):
    from urllib.parse import urlparse, parse_qs
    try:
        return parse_qs(urlparse(thumbnail_url).query).get("url", [None])[0]
    except Exception:
        return None


def _fetch_fresh_creative(name, token, big=False):
    from meta_api import BASE_URL
    r = http_requests.get(
        f"{BASE_URL}/act_{META_ACCOUNT}/ads",
        params={"access_token": token, "fields": "name,creative{thumbnail_url,image_url,object_story_spec}", "filtering": json.dumps([{"field": "name", "operator": "EQUAL", "value": name}]), "limit": 1},
        timeout=15
    )
    data = r.json()
    for ad in data.get("data", []):
        cr = ad.get("creative") or {}
        oss = cr.get("object_story_spec") or {}
        oss_pic = ((oss.get("link_data") or {}).get("picture")
                   or (oss.get("video_data") or {}).get("image_url")
                   or (oss.get("template_data") or {}).get("picture"))
        thumb = cr.get("thumbnail_url")
        return {
            "thumb": thumb or oss_pic or cr.get("image_url"),
            "full": cr.get("image_url") or oss_pic or _embedded_full_url(thumb) or thumb,
        }
    return None


def _resize_jpeg(img_bytes, box):
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(img_bytes))
        im.thumbnail((box, box))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        return img_bytes


@app.route("/api/thumb")
def api_thumb():
    name = request.args.get("name", "")
    size = request.args.get("size", "thumb")
    if size not in ("thumb", "full"):
        size = "thumb"
    if not name:
        return jsonify({"error": "missing name"}), 400
    big = (size == "full")

    thumb_dir = os.path.join(CACHE_DIR, "thumbs")
    os.makedirs(thumb_dir, exist_ok=True)
    safe = hashlib.md5((("full320|" if big else "thumb64|") + name).encode()).hexdigest()
    cache_file = os.path.join(thumb_dir, safe + ".jpg")

    if os.path.exists(cache_file):
        with open(cache_file, "rb") as f:
            return Response(f.read(), mimetype="image/jpeg")

    token = os.environ.get("META_ACCESS_TOKEN")

    def _serve(u):
        try:
            r = http_requests.get(u, timeout=15)
            if r.status_code == 200:
                img = r.content
                if big:
                    img = _resize_jpeg(img, 320)
                with open(cache_file, "wb") as f:
                    f.write(img)
                return Response(img, mimetype="image/jpeg" if big else r.headers.get("Content-Type", "image/jpeg"))
        except Exception:
            pass
        return None

    cached = read_cache(f"ad_urls_{META_ACCOUNT}")
    entry = (cached.get("data", {}).get(name) or {}) if cached else {}
    cached_url = entry.get("thumb") or entry.get("full")
    if cached_url:
        u = _embedded_full_url(cached_url) if big else cached_url
        if u:
            resp = _serve(u)
            if resp:
                return resp

    if token:
        fresh = _fetch_fresh_creative(name, token, big=big)
        if fresh and fresh.get(size):
            resp = _serve(fresh[size])
            if resp:
                return resp

    return jsonify({"error": "not found"}), 404


FLIGHT_ALIASES = {
    "0608-0621": "0601-0621",
}

@app.route("/api/creative_img")
def creative_img():
    flights = request.args.get("flights", request.args.get("flight", ""))
    column = request.args.get("column", "")
    candidates = []
    for flight in [f.strip() for f in flights.split(",") if f.strip()]:
        candidates.append(flight)
        if flight in FLIGHT_ALIASES:
            candidates.append(FLIGHT_ALIASES[flight])
    for flight in candidates:
        folder = os.path.join(TRIUMPH_CREATIVES, flight)
        if os.path.isdir(folder):
            for f in os.listdir(folder):
                if f.startswith(column) and not f.startswith('.'):
                    return send_from_directory(os.path.abspath(folder), f)
    return jsonify({"error": "not found"}), 404

# ── Keep-alive + 每日預熱 ─────────────────────────────────────

SELF_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:5002")

def _prewarm():
    today = datetime.now(TZ_TAIPEI)
    ranges = [
        ((today - timedelta(days=6)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")),
        ((today - timedelta(days=29)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")),
    ]
    token = os.environ.get("META_ACCESS_TOKEN")
    for since, until in ranges:
        try:
            # GA4
            for ep, fn in [
                ("daily",         _fetch_ga4_daily),
                ("daily_channels",_fetch_ga4_daily_channels),
                ("daily_sources", _fetch_ga4_daily_sources),
                ("items",         _fetch_ga4_items),
                ("search",        _fetch_ga4_search),
                ("channels",      _fetch_ga4_channels),
                ("sources",       _fetch_ga4_sources),
            ]:
                ck = ga4_key(ep, since, until)
                if not read_cache(ck, ttl=CACHE_TTL):
                    write_cache(ck, {"data": fn(since, until), "updated": taipei_now()})
            # Meta (近7天 daily)
            if token:
                ck = meta_key(META_ACCOUNT, since, until, "account", "1")
                if not read_cache(ck, ttl=CACHE_TTL):
                    rows = get_insights(META_ACCOUNT, since, until, "account", "1", token, None)
                    write_cache(ck, {"data": rows, "updated": taipei_now()})
        except Exception as e:
            print(f"[prewarm] {since}~{until} error: {e}")

def _keep_alive():
    try:
        http_requests.get(f"{SELF_URL}/api/cache_status", timeout=10)
    except Exception:
        pass

scheduler = BackgroundScheduler(timezone="Asia/Taipei")
scheduler.add_job(_keep_alive, "interval", minutes=14, id="keep_alive")
scheduler.add_job(_prewarm, "cron", hour=7, minute=0, id="daily_prewarm")
scheduler.start()

if __name__ == "__main__":
    app.run(debug=True, port=5002)
