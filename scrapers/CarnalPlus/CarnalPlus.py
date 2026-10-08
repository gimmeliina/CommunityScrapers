import hashlib
import hmac
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from py_common import log
from py_common.deps import ensure_requirements
from py_common.types import (
    ScrapedGallery,
    ScrapedMovie,
    ScrapedPerformer,
    ScrapedScene,
    ScrapedTag,
)
from py_common.util import (
    dig,
    feet_to_cm,
    guess_nationality,
    is_valid_url,
    lb_to_kg,
    scraper_args,
)

ensure_requirements("requests", "bs4:beautifulsoup4")


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

def performer_from_url(url: str) -> str | None:
    m = re.search(r"performer=(\d+)", url)
    if not m:
        return None
    return m.group(1)


def scene_from_url(url: str) -> ScrapedScene | None:
    page = get(url)
    m = re.search(r"video_id\s*:\s*(\d+)", page)
    if not m:
        log.error("video_id not found on page")
        return None
    vid = m.group(1)

    cfg = find_config(page, url)
    if not cfg:
        log.error("RECOMBEE_CONFIG not found on page or in its JS files")
        return None
    db, token, region = cfg

    path = f"/{db}/recomms/users/test-user-1/items/?frontend_timestamp={int(time.time())}"
    sig = hmac.new(token.encode(), path.encode(), hashlib.sha1).hexdigest()
    r = S.post(
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
        log.error(f"Recombee returned nothing for video {vid}")
        return None

    values = recomms[0].get("values", {})
    log.debug(json.dumps(values, ensure_ascii=False, indent=2))

    scene = to_scraped_scene(values)
    return scene

## Maybe to utilities?
def clean_text(text: str) -> str:
    text = text.replace("\\", "")
    text = re.sub(r"<\s*/?br\s*/?\s*>", "\n", text)
    text = BeautifulSoup(text, "html.parser").get_text("", strip=False)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.strip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()

def full_size(url: str) -> str:
    url = re.sub(r"-1x[^/]*?\.jpg", "-full.jpg", url)
    return re.sub(r"width=\d+", "width=9999", url)

def to_date(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d")
    if isinstance(value, str):
        return value[:10]
    return None


def to_scraped_scene(api_scene: dict[str, Any]) -> ScrapedScene:
    scene: ScrapedScene = {}
    if isinstance(directory := api_scene.get("directory"), str):
        scene["code"] = directory
    if isinstance(title := api_scene.get("title"), str):
        scene["title"] = re.sub(r"\s*\|\s*", " - ", title).strip()
    if isinstance(description := api_scene.get("description"), str):
        scene["details"] = clean_text(description)
    if isinstance(urls := api_scene.get("network_url"), str):
        scene["urls"] = [urls]
    elif isinstance(urls, list):
        scene["urls"] = [u for u in urls if isinstance(u, str)]
    if release_date := to_date(api_scene.get("release_date")):
        scene["date"] = release_date
    if isinstance(image := api_scene.get("poster_url"), str):
        scene["image"] = full_size(image)
    if isinstance(studio := api_scene.get("channel"), str):
        scene["studio"] = {"name": studio}
    if isinstance(tags := api_scene.get("categories"), list):
        scene["tags"] = [{"name": t} for t in tags if isinstance(t, str)]
    if isinstance(actors := api_scene.get("models"), list):
        scene["performers"] = [{"name": a} for a in actors if isinstance(a, str)]

    log.debug(f"scene: {scene}")
    return scene


if __name__ == "__main__":
    op, args = scraper_args()
    log.debug(f"args: {args}")
    match op, args:
        case "scene-by-url", {"url": url} if url:
            result = scene_from_url(url)
        case _:
            log.error(f"Operation: {op}, arguments: {json.dumps(args)} not implemented")
            sys.exit(1)

    if result is None:
        print("null")
        sys.exit(0)
    print(json.dumps(result))
