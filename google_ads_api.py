import os
from collections import defaultdict
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

BASE_GAQL = """
    SELECT segments.date, campaign.name, campaign.advertising_channel_type,
           metrics.cost_micros, metrics.impressions, metrics.clicks
    FROM campaign
    WHERE segments.date BETWEEN '{since}' AND '{until}'
"""

CONV_GAQL = """
    SELECT segments.date, campaign.name, segments.conversion_action_category,
           metrics.conversions, metrics.conversions_value, metrics.all_conversions
    FROM campaign
    WHERE segments.date BETWEEN '{since}' AND '{until}'
"""

PMAX_BASE_GAQL = """
    SELECT segments.date, campaign.name, segments.ad_network_type,
           metrics.cost_micros, metrics.impressions, metrics.clicks
    FROM campaign
    WHERE campaign.advertising_channel_type = 'PERFORMANCE_MAX'
      AND segments.date BETWEEN '{since}' AND '{until}'
"""

PMAX_CONV_GAQL = """
    SELECT segments.date, campaign.name, segments.ad_network_type,
           segments.conversion_action_category,
           metrics.conversions, metrics.conversions_value, metrics.all_conversions
    FROM campaign
    WHERE campaign.advertising_channel_type = 'PERFORMANCE_MAX'
      AND segments.date BETWEEN '{since}' AND '{until}'
"""

CHANNEL_LABELS = {
    "SEARCH": "搜尋", "SEARCH_PARTNERS": "搜尋夥伴", "CONTENT": "多媒體",
    "YOUTUBE": "YouTube", "YOUTUBE_SEARCH": "YouTube 搜尋",
    "YOUTUBE_WATCH": "YouTube 觀看", "GMAIL": "Gmail",
    "DISCOVER": "Discover", "MAPS": "地圖", "MIXED": "混合",
}


def _client():
    return GoogleAdsClient.load_from_dict({
        "developer_token": os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"],
        "client_id": os.environ["GOOGLE_ADS_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_ADS_CLIENT_SECRET"],
        "refresh_token": os.environ["GOOGLE_ADS_REFRESH_TOKEN"],
        "login_customer_id": os.environ["GOOGLE_ADS_LOGIN_CUSTOMER_ID"],
        "use_proto_plus": True,
    })


def _run(query):
    svc = _client().get_service("GoogleAdsService")
    cid = os.environ["GOOGLE_ADS_CUSTOMER_ID"]
    try:
        return list(svc.search_stream(customer_id=cid, query=query))
    except GoogleAdsException as ex:
        raise Exception("Google Ads API error: " + "; ".join(e.message for e in ex.failure.errors))


def _blank():
    return {"cost": 0.0, "imp": 0, "clicks": 0, "atc": 0.0, "purchase": 0.0, "revenue": 0.0}


def _finish(agg, key_fields):
    rows = []
    for key, v in agg.items():
        r = dict(zip(key_fields, key))
        r.update(v)
        r["ctr"] = v["clicks"] / v["imp"] * 100 if v["imp"] else 0.0
        r["cpc"] = v["cost"] / v["clicks"] if v["clicks"] else 0.0
        r["cpa"] = v["cost"] / v["purchase"] if v["purchase"] else 0.0
        r["roas"] = v["revenue"] / v["cost"] if v["cost"] else 0.0
        rows.append(r)
    rows.sort(key=lambda x: tuple(str(x[f]) for f in key_fields))
    return rows


def _collect(base_gaql, conv_gaql, since, until, extra_dim=None):
    """extra_dim: None 或 'ad_network_type'。轉換指標要另外查，否則基礎指標會被重複計算。"""
    agg = defaultdict(_blank)

    for b in _run(base_gaql.format(since=since, until=until)):
        for row in b.results:
            k = [row.segments.date, row.campaign.name]
            if extra_dim:
                k.append(row.segments.ad_network_type.name)
            a = agg[tuple(k)]
            a["cost"] += row.metrics.cost_micros / 1_000_000
            a["imp"] += row.metrics.impressions
            a["clicks"] += row.metrics.clicks

    for b in _run(conv_gaql.format(since=since, until=until)):
        for row in b.results:
            k = [row.segments.date, row.campaign.name]
            if extra_dim:
                k.append(row.segments.ad_network_type.name)
            a = agg[tuple(k)]
            cat = row.segments.conversion_action_category.name
            if cat == "PURCHASE":
                a["purchase"] += row.metrics.conversions
                a["revenue"] += row.metrics.conversions_value
            elif cat == "ADD_TO_CART":
                a["atc"] += row.metrics.all_conversions

    fields = ["date", "campaign"] + (["channel"] if extra_dim else [])
    return agg, fields


def get_campaign_daily(since, until):
    agg, fields = _collect(BASE_GAQL, CONV_GAQL, since, until)
    return _finish(agg, fields)


def get_pmax_channels(since, until):
    agg, fields = _collect(PMAX_BASE_GAQL, PMAX_CONV_GAQL, since, until, extra_dim="ad_network_type")
    rows = _finish(agg, fields)
    for r in rows:
        r["channel_label"] = CHANNEL_LABELS.get(r["channel"], r["channel"])
    return rows
