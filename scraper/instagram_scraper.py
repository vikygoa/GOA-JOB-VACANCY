import json
import os
import re
import hashlib
from pathlib import Path
from datetime import datetime, timezone

import requests

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
API_URL = CONFIG["instagram_api_url"]
HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "User-Agent": "okhttp/4.10.0"
}
IMG_DIR = ROOT / "images"
IMG_DIR.mkdir(exist_ok=True)
DEBUG_DIR = ROOT / "debug"
DEBUG_DIR.mkdir(exist_ok=True)


def post_id(post):
    for key in ("pk", "id", "code", "shortcode", "media_id"):
        value = post.get(key)
        if value:
            return str(value)
    raw = json.dumps(post, sort_keys=True, ensure_ascii=False)
    return "hash_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def image_url(post):
    versions = post.get("image_versions") or {}
    items = versions.get("items") or []
    if items:
        return items[0].get("url")
    return post.get("thumbnail_url")


def fetch_account(username):
    payload = {
        "data": {
            "endpoint": "/v1.2/posts",
            "params": {
                "username_or_id_or_url": username
            }
        }
    }
    r = requests.post(API_URL, json=payload, headers=HEADERS, timeout=45)
    r.raise_for_status()
    data = r.json()
    (DEBUG_DIR / f"{username}.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    try:
        items = data["result"]["data"]["items"]
    except (KeyError, TypeError):
        raise RuntimeError(f"Unexpected API response for @{username}")

    return items[: int(CONFIG.get("posts_per_account", 6))]


def download(post, username):
    url = image_url(post)
    if not url:
        return None

    pid = post_id(post)
    safe_user = re.sub(r"[^a-zA-Z0-9_-]", "_", username)
    out = IMG_DIR / f"{safe_user}_{pid}.jpg"

    if out.exists():
        return str(out.relative_to(ROOT))

    r = requests.get(url, timeout=45, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    if not r.content:
        raise RuntimeError("Empty image response")
    out.write_bytes(r.content)
    return str(out.relative_to(ROOT))


def main():
    processed_file = ROOT / "data/processed_posts.json"
    processed = json.loads(processed_file.read_text(encoding="utf-8"))
    seen = set(processed.get("processed_posts", []))
    new_posts = []

    for username in CONFIG["accounts"]:
        print(f"Fetching @{username}...")
        try:
            posts = fetch_account(username)
        except Exception as e:
            print(f"WARNING: @{username}: {e}")
            continue

        for post in posts:
            pid = post_id(post)
            if pid in seen:
                continue
            try:
                image = download(post, username)
            except Exception as e:
                print(f"WARNING: image download failed for {pid}: {e}")
                continue
            if not image:
                print(f"WARNING: no image for {pid}")
                continue

            new_posts.append({
                "post_id": pid,
                "account": username,
                "image": image,
                "raw_post": post
            })
            seen.add(pid)

    processed["processed_posts"] = list(seen)[-1000:]
    processed_file.write_text(
        json.dumps(processed, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    (ROOT / "data/new_posts.json").write_text(
        json.dumps({
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "posts": new_posts
        }, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"New posts ready for AI: {len(new_posts)}")


if __name__ == "__main__":
    main()
