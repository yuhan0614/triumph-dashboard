"""
執行這支腳本可以重新授權 GA4，拿到新的 refresh_token。
用法：python refresh_ga4_token.py
"""
import os, json, webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
import requests

CLIENT_ID     = os.environ.get("GA4_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GA4_CLIENT_SECRET", "")
REDIRECT_URI  = "http://localhost:9999/callback"
SCOPE         = "https://www.googleapis.com/auth/analytics.readonly"

if not CLIENT_ID or not CLIENT_SECRET:
    print("請先設定環境變數 GA4_CLIENT_ID 和 GA4_CLIENT_SECRET，或直接改這支腳本填入值。")
    CLIENT_ID     = input("GA4_CLIENT_ID: ").strip()
    CLIENT_SECRET = input("GA4_CLIENT_SECRET: ").strip()

auth_url = (
    "https://accounts.google.com/o/oauth2/v2/auth?"
    + urlencode({
        "client_id":     CLIENT_ID,
        "redirect_uri":  REDIRECT_URI,
        "response_type": "code",
        "scope":         SCOPE,
        "access_type":   "offline",
        "prompt":        "consent",
    })
)

print(f"\n開啟瀏覽器授權：\n{auth_url}\n")
webbrowser.open(auth_url)

code_holder = {}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        code = qs.get("code", [None])[0]
        if code:
            code_holder["code"] = code
            self.send_response(200)
            self.end_headers()
            self.wfile.write("<h2>OK</h2>".encode())
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"no code")

    def log_message(self, *args):
        pass

print("等待 Google 授權回調（http://localhost:9999）…")
HTTPServer(("localhost", 9999), Handler).handle_request()

code = code_holder.get("code")
if not code:
    print("沒拿到 code，請重試。")
    exit(1)

resp = requests.post("https://oauth2.googleapis.com/token", data={
    "code":          code,
    "client_id":     CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "redirect_uri":  REDIRECT_URI,
    "grant_type":    "authorization_code",
})
tokens = resp.json()

if "refresh_token" not in tokens:
    print("拿 token 失敗：", json.dumps(tokens, indent=2))
    exit(1)

print("\n========== 複製以下值到 Render 環境變數 ==========")
print(f"GA4_REFRESH_TOKEN={tokens['refresh_token']}")
print("===================================================\n")
print("(access_token 不用存，後端每次自動 refresh)")
