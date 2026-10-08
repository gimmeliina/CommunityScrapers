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

from Altwolia.scrape import performer_from_url
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


def scene_from_url(url: str):
    page = get(url)
    m = re.search(r"video_id\s*:\s*(\d+)", page)
    if not m:
        sys.exit("video_id not found on page")
    vid = m.group(1)

    cfg = find_config(page, url)
    if not cfg:
        sys.exit("RECOMBEE_CONFIG not found on page or in its JS files")
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
        log.error(f"Recombee returned nothing for video {vid}")
        return None

    values = recomms[0].get("values", {})
    log.debug(json.dumps(values, ensure_ascii=False, indent=2))

    return to_scraped_scene(values)

## Maybe to utilities?
def clean_text(text: str) -> str:
    text = text.replace("\\", "")
    text = re.sub(r"<\s*/?br\s*/?\s*>", "\n", text)
    return BeautifulSoup(text, "html.parser").get_text("", strip=False)

def full_size(url: str) -> str:
    url = re.sub(r"-1x.*?\.jpg", "-full.jpg", url)
    return re.sub(r"\d{4}", "9999", url)

def to_date(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d")
    if isinstance(value, str):
        return value[:10]
    return None

def to_scraped_scene(api_scene: dict[str]) -> ScrapedScene:
    scene: ScrapedScene = {}
    if directory := api_scene.get("directory"):
        scene["code"] = directory
    if title := api_scene.get("title"):
        scene["title"] = title
    if description := api_scene.get("description"):
        scene["details"] = clean_text(description)
    if urls := api_scene.get("network_url"):
        scene["urls"] = urls
    if release_date := to_date(api_scene.get("release_date")):
        scene["date"] = release_date
    if image := api_scene.get("poster_url"):
        scene["image"] = full_size(image)
    if studio := api_scene.get("channel"):
        scene["studio"] = studio

    tags = api_scene.get("categories", [])

    if tags:
        scene["tags"] = tags

    actors = api_scene.get("models", [])
    if actors:
        scene["performers"] = actors

    log.debug(f"scene: {scene}")
    return scene


if __name__ == "__main__":
    ## op, args = scraper_args()
    op, args = "scene-by-url", {"url": "https://jockpack.com/videos/brotherly-bonds-pledge-grayson-vol-1.html"}
    log.debug(f"args: {args}")
    match op, args:
        case "scene-by-url", {"url": url} if url:
            result = scene_from_url(url)
