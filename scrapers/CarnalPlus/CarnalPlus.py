import hashlib
import hmac
import json
import os
import re
import sys
import time
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
        sys.exit(f"Recombee returned nothing for video {vid}")

    print(json.dumps(recomms[0].get("values", {}), ensure_ascii=False, indent=2))

## Maybe to utilities?
def clean_text(text: str) -> str:
    text = text.replace("\\", "")
    text = re.sub(r"<\s*/?br\s*/?\s*>", "\n", text)
    return BeautifulSoup(text, "html.parser").get_text("", strip=False)

def to_scraped_scene(api_scene: dict[str, Any], site: str) -> ScrapedScene:
    scene: ScrapedScene = {}
    if clip_id := api_scene.get("clip_id"):
        scene["code"] = str(clip_id)
    if title := api_scene.get("title"):
        scene["title"] = title.strip()
    if description := api_scene.get("description"):
        scene["details"] = clean_text(description)
    if urls := scene_urls(api_scene):
        scene["urls"] = urls
    if release_date := api_scene.get("release_date"):
        scene["date"] = release_date
    if image := largest_scene_image(api_scene):
        scene["image"] = f"{IMAGE_CDN}/movies{image}"
    if studio_name := api_scene.get("studio_name"):
        scene["studio"] = {"name": studio_name}
    if (movie_id := api_scene.get("movie_id")) and movie_exists(movie_id, site):
        scene["movies"] = [movie_from_api_scene(api_scene, site)]

    tags = name_values_as_list(api_scene.get("categories", []))
    tags += list_to_name_values(api_scene.get("content_tags", []))
    if tags:
        scene["tags"] = tags

    if actors := api_scene.get("actors"):
        scene["performers"] = actors_to_performers(actors, site)
    if directors := api_scene.get("directors"):
        scene["director"] = name_values_as_csv(directors)

    return scene


if __name__ == "__main__":
    op, args = scraper_args()
    log.debug(f"args: {args}")

    match op, args:
        case "scene-by-url", {"url": url} if url:
            result = scene_from_url(url)