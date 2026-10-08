import hashlib
import hmac
import json
import os
import re
import sys
import time
from urllib.parse import urljoin, urlparse

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
if os.environ.get("COOKIE"):
    HEADERS["Cookie"] = os.environ["COOKIE"]

S = requests.Session()
S.headers.update(HEADERS)


def get(url):
    r = S.get(url, timeout=15)
    r.raise_for_status()
    return r.text


def parse_config(text):
    def field(name):
        m = re.search(rf"{name}\s*:\s*['\"]([^'\"]*)['\"]", text)
        return m.group(1) if m else None

    db, token = field("databaseId"), field("publicToken")
    if db and token:
        return db, token, field("region") or ""
    return None


def find_config(page, page_url):
    cfg = parse_config(page)
    if cfg:
        return cfg
    host = urlparse(page_url).netloc
    for src in re.findall(r"<script[^>]+src=['\"]([^'\"]+)['\"]", page):
        js_url = urljoin(page_url, src)
        if urlparse(js_url).netloc != host:
            continue
        try:
            cfg = parse_config(get(js_url))
        except requests.RequestException:
            continue
        if cfg:
            return cfg
    return None


def api_host(region):
    return f"client-rapi-{region.lower()}.recombee.com" if region else "client-rapi.recombee.com"


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else json.load(sys.stdin)["url"]
    page = get(url)

    m = re.search(r"video_id\s*:\s*(\d+)", page)
    if not m:
        sys.exit("video_id not found")
    vid = m.group(1)

    cfg = find_config(page, url)
    if not cfg:
        sys.exit("RECOMBEE_CONFIG not found")
    db, token, region = cfg

    path = f"/{db}/recomms/users/test-user-1/items/?frontend_timestamp={int(time.time())}"
    sig = hmac.new(token.encode(), path.encode(), hashlib.sha1).hexdigest()
    r = requests.post(
        f"https://{api_host(region)}{path}&frontend_sign={sig}",
        json={
            "count": 1,
            "returnProperties": True,
            "cascadeCreate": True,
            "filter": f"'itemId' == \"{vid}\"",
        },
        timeout=15,
    )
    r.raise_for_status()

    recomms = r.json().get("recomms") or []
    if not recomms:
        sys.exit(f"Recombee ei palauttanut videota {vid}")

    print(json.dumps(recomms[0].get("values", {}), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
