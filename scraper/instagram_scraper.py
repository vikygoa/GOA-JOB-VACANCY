import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

API_URL = CONFIG["instagram_api_url"]
HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "User-Agent": "okhttp/4.10.0",
}

IMAGE_DIR = ROOT / "images"
IMAGE_DIR.mkdir(exist_ok=True)

DEBUG_DIR = ROOT / "debug"
DEBUG_DIR.mkdir(exist_ok=True)


def load_processed_ids():
    path = ROOT / "data" / "processed_posts.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(str(x) for x in data.get("processed_posts", []))
    except Exception:
        return set()


def stable_post_id(post):
    for key in ("pk", "id", "code", "shortcode", "media_id"):
        if post.get(key):
            return str(post[key])

    raw = json.dumps(post, sort_keys=True, ensure_ascii=False)
    return "hash_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def media_url(media):
    versions = media.get("image_versions") or {}
    items = versions.get("items") or []

    if items:
        items = sorted(
            items,
            key=lambda x: (x.get("width", 0) or 0) * (x.get("height", 0) or 0),
            reverse=True,
        )
        for item in items:
            if item.get("url"):
                return item["url"]

    return media.get("thumbnail_url") or media.get("url")


def get_all_media(post):
    """
    Return every image in an Instagram post.

    Supports:
    - normal single-image posts
    - carousel_media
    - carousel_media.items
    - carousel_media.media
    - image_versions fallback
    """
    carousel = post.get("carousel_media")

    if isinstance(carousel, list) and carousel:
        return carousel

    if isinstance(carousel, dict):
        for key in ("items", "media", "carousel_media"):
            value = carousel.get(key)
            if isinstance(value, list) and value:
                return value

    # Some API responses use a different carousel field.
    for key in ("carousel_items", "children", "media_items"):
        value = post.get(key)
        if isinstance(value, list) and value:
            return value

    # Normal single-image post.
    return [post]


def download_media(media, username, post_id, index):
    url = media_url(media)
    if not url:
        return None

    safe_user = re.sub(r"[^A-Za-z0-9_-]", "_", username)
    output = IMAGE_DIR / f"{safe_user}_{post_id}_{index:02d}.jpg"

    if output.exists() and output.stat().st_size > 0:
        return str(output.relative_to(ROOT))

    response = requests.get(
        url,
        timeout=45,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()

    if not response.content:
        raise RuntimeError("Empty image response")

    output.write_bytes(response.content)
    return str(output.relative_to(ROOT))


def fetch_posts(username):
    payload = {
        "data": {
            "endpoint": "/v1.2/posts",
            "params": {
                "username_or_id_or_url": username
            },
        }
    }

    response = requests.post(
        API_URL,
        json=payload,
        headers=HEADERS,
        timeout=45,
    )
    response.raise_for_status()

    data = response.json()

    (DEBUG_DIR / f"{username}.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    try:
        items = data["result"]["data"]["items"]
    except (KeyError, TypeError):
        raise RuntimeError(f"Unexpected API response for @{username}")

    return items[: int(CONFIG.get("posts_per_account", 6))]


def main():
    processed_ids = load_processed_ids()
    new_posts = []

    for username in CONFIG["accounts"]:
        print(f"Fetching @{username}...")

        try:
            posts = fetch_posts(username)
        except Exception as exc:
            print(f"WARNING: @{username} fetch failed: {exc}")
            continue

        for post in posts:
            post_id = stable_post_id(post)

            if post_id in processed_ids:
                continue

            media_items = get_all_media(post)

            if not media_items:
                print(f"WARNING: no media for {post_id}")
                continue

            downloaded = []

            try:
                for index, media in enumerate(media_items, start=1):
                    path = download_media(
                        media,
                        username,
                        post_id,
                        index,
                    )
                    if path:
                        downloaded.append(path)
            except Exception as exc:
                print(f"WARNING: carousel download failed for {post_id}: {exc}")
                continue

            if not downloaded:
                print(f"WARNING: no downloadable images for {post_id}")
                continue

            new_posts.append({
                "post_id": post_id,
                "account": username,
                "images": downloaded,
                "slide_count": len(downloaded),
                "raw_post": post,
            })

            print(
                f"New post @{username}: {post_id} "
                f"({len(downloaded)} slide(s))"
            )

    output = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "posts": new_posts,
    }

    (ROOT / "data" / "new_posts.json").write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"New Instagram posts ready for Gemini: {len(new_posts)}")


if __name__ == "__main__":
    main()
