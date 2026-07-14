from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os, json, threading
import requests as http_requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from meta_api import ACCOUNTS, get_insights, BASE_URL

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

def taipei_now():
    return datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M")

def cache_path(key):
    return os.path.join(CACHE_DIR, f"{key}.json")

def read_cache(key):
    p = cache_path(key)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return None

def write_cache(key, data):
    with open(cache_path(key), "w") as f:
        json.dump(data, f, ensure_ascii=False)


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

    all_results, errors = [], []
    for account_id in account_ids:
        try:
            rows = get_insights(account_id, since, until, level, time_increment, token, breakdowns)
            all_results.extend(rows)
        except Exception as e:
            errors.append({"account_id": account_id, "error": str(e)})

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
    """Meta's 64px thumbnail_url embeds the original full-res image as a
    url= query param (facebook.com/ads/image/...). Pull it out."""
    from urllib.parse import urlparse, parse_qs
    try:
        return parse_qs(urlparse(thumbnail_url).query).get("url", [None])[0]
    except Exception:
        return None


def _fetch_fresh_creative(name, token, big=False):
    """Re-fetch a creative image url for a single ad. When big=True, return the
    embedded full-resolution image instead of the 64px thumbnail."""
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
    """Downscale to fit box×box, return JPEG bytes. No-op if Pillow missing."""
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
    import hashlib
    from flask import Response
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

    # fast path: reuse cached ad_urls url. For big, the outer thumbnail url is
    # expired but its embedded full-res image url still works — extract it, no API call.
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


if __name__ == "__main__":
    app.run(debug=True, port=5002)
