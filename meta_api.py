import requests

ACCOUNTS = {
    "Verve（愛禮物持有）": "1765049190314272",
    "Verve_New": "1417409513448597",
    "奧旅博 - 廣告帳號": "500489921658676",
    "G2_A180218_TW_唯寵": "1482186610196752",
    "AM_G250333_TW_禮黛": "959760355921279",
    "Triumph TW": "629806431400540",
}

BASE_URL = "https://graph.facebook.com/v20.0"

FIELDS = [
    "spend", "impressions", "clicks", "inline_link_clicks",
    "actions", "action_values", "cpc", "cost_per_inline_link_click",
    "ctr", "purchase_roas", "cost_per_action_type",
    "date_start", "date_stop",
    "campaign_id", "campaign_name",
    "adset_id", "adset_name",
    "ad_id", "ad_name",
    "reach", "frequency",
]


def _action_val(lst, action_type):
    for item in (lst or []):
        if item.get("action_type") == action_type:
            return float(item.get("value", 0))
    return 0.0


def _parse(row, level, account_name, account_id):
    spend = float(row.get("spend", 0) or 0)
    actions = row.get("actions", [])
    action_values = row.get("action_values", [])
    roas_list = row.get("purchase_roas", [])
    cpa_list = row.get("cost_per_action_type", [])

    purchase = _action_val(actions, "purchase")
    engagement = _action_val(actions, "post_engagement")

    cpa = _action_val(cpa_list, "purchase")
    if cpa == 0 and purchase > 0:
        cpa = spend / purchase

    cpe = _action_val(cpa_list, "post_engagement")
    if cpe == 0 and engagement > 0:
        cpe = spend / engagement

    result = {
        "account_id": account_id,
        "account_name": account_name,
        "date_start": row.get("date_start", ""),
        "date_stop": row.get("date_stop", ""),
        "spend": spend,
        "impressions": int(row.get("impressions", 0) or 0),
        "clicks": int(row.get("clicks", 0) or 0),
        "link_clicks": int(row.get("inline_link_clicks", 0) or 0),
        "engagement": engagement,
        "add_to_cart": _action_val(actions, "add_to_cart"),
        "purchase": purchase,
        "purchase_value": _action_val(action_values, "purchase"),
        "cpc": float(row.get("cpc", 0) or 0),
        "cplc": float(row.get("cost_per_inline_link_click", 0) or 0),
        "ctr": float(row.get("ctr", 0) or 0),
        "cpa": cpa,
        "roas": float(roas_list[0]["value"]) if roas_list else 0.0,
        "cpe": cpe,
        "reach": int(row.get("reach", 0) or 0),
        "frequency": float(row.get("frequency", 0) or 0),
    }
    if "age" in row:
        result["age"] = row["age"]
    if "gender" in row:
        result["gender"] = row["gender"]

    if level in ("campaign", "adset", "ad"):
        result["campaign_id"] = row.get("campaign_id", "")
        result["campaign_name"] = row.get("campaign_name", "")
    if level in ("adset", "ad"):
        result["adset_id"] = row.get("adset_id", "")
        result["adset_name"] = row.get("adset_name", "")
    if level == "ad":
        result["ad_id"] = row.get("ad_id", "")
        result["ad_name"] = row.get("ad_name", "")

    return result


def get_insights(account_id, since, until, level="account", time_increment="all_days", token=None, breakdowns=None):
    account_name = next((k for k, v in ACCOUNTS.items() if v == account_id), account_id)
    url = f"{BASE_URL}/act_{account_id}/insights"
    params = {
        "access_token": token,
        "fields": ",".join(FIELDS),
        "level": level,
        "time_range": f'{{"since":"{since}","until":"{until}"}}',
        "limit": 500,
    }
    if time_increment != "all_days":
        params["time_increment"] = time_increment
    if breakdowns:
        params["breakdowns"] = breakdowns

    results = []
    while url:
        r = requests.get(url, params=params)
        data = r.json()
        if "error" in data:
            raise Exception(data["error"]["message"])
        for row in data.get("data", []):
            results.append(_parse(row, level, account_name, account_id))
        url = data.get("paging", {}).get("next")
        params = {}

    return results
