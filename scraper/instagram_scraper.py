import json
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


def load_processed():
    """Read already successfully processed Instagram post IDs."""
    path = ROOT / "data" / "processed_posts.json"

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return {"processed_posts": []}

        return data

    except Exception:
        return {"processed_posts": []}


def get_post_id(post):
    """Get a stable ID for an Instagram post."""
    for key in ("pk", "id", "code", "shortcode", "media_id"):
        value = post.get(key)

        if value:
            return str(value)

    # Fallback when the API does not provide a normal ID.
    raw = json.dumps(
        post,
        sort_keys=True,
        ensure_ascii=False
    )

    return "hash_" + hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:24]


def get_image_url(post):
    """Get the best available image URL."""
    image_versions = post.get("image_versions") or {}
    items = image_versions.get("items") or []

    if items:
        # Prefer the largest image when dimensions are available.
        items = sorted(
            items,
            key=lambda item:
                (item.get("width", 0) or 0) *
                (item.get("height", 0) or 0),
            reverse=True
        )

        for item in items:
            url = item.get("url")

            if url:
                return url

    return post.get("thumbnail_url")


def fetch_account(username):
    """Fetch recent posts from one Instagram account."""
    payload = {
        "data": {
            "endpoint": "/v1.2/posts",
            "params": {
                "username_or_id_or_url": username
            }
        }
    }

    response = requests.post(
        API_URL,
        json=payload,
        headers=HEADERS,
        timeout=45
    )

    response.raise_for_status()

    data = response.json()

    # Save API response for debugging.
    debug_file = DEBUG_DIR / f"{username}.json"

    with open(debug_file, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False
        )

    try:
        items = data["result"]["data"]["items"]

    except (KeyError, TypeError):
        raise RuntimeError(
            f"Unexpected Instagram API response for @{username}"
        )

    if not isinstance(items, list):
        raise RuntimeError(
            f"Instagram API returned invalid posts data for @{username}"
        )

    return items[:int(CONFIG.get("posts_per_account", 6))]


def download_image(post, username):
    """Download a post image and return repository-relative path."""
    url = get_image_url(post)

    if not url:
        return None

    post_id = get_post_id(post)

    safe_username = re.sub(
        r"[^a-zA-Z0-9_-]",
        "_",
        username
    )

    output_file = IMG_DIR / f"{safe_username}_{post_id}.jpg"

    # Avoid downloading the same image again.
    if output_file.exists() and output_file.stat().st_size > 0:
        return str(output_file.relative_to(ROOT))

    response = requests.get(
        url,
        timeout=45,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    response.raise_for_status()

    if not response.content:
        raise RuntimeError(
            "Instagram returned an empty image"
        )

    output_file.write_bytes(response.content)

    return str(output_file.relative_to(ROOT))


def main():
    processed_data = load_processed()

    processed_ids = set(
        processed_data.get("processed_posts", [])
    )

    new_posts = []

    for username in CONFIG["accounts"]:

        print(
            f"Fetching @{username}..."
        )

        try:
            posts = fetch_account(username)

        except Exception as error:
            print(
                f"WARNING: Failed to fetch "
                f"@{username}: {error}"
            )
            continue

        print(
            f"Found {len(posts)} recent posts "
            f"from @{username}"
        )

        for post in posts:

            post_id = get_post_id(post)

            # Only skip posts that were successfully
            # processed by Gemini in a previous run.
            if post_id in processed_ids:
                continue

            try:
                image = download_image(
                    post,
                    username
                )

            except Exception as error:
                print(
                    f"WARNING: Image download failed "
                    f"for {post_id}: {error}"
                )
                continue

            if not image:
                print(
                    f"WARNING: No image found "
                    f"for {post_id}"
                )
                continue

            new_posts.append({
                "post_id": post_id,
                "account": username,
                "image": image,
                "raw_post": post
            })

    # IMPORTANT:
    # DO NOT update processed_posts.json here.
    #
    # A post becomes processed ONLY after Gemini
    # successfully creates a job. ai_parser.py handles that.
    #
    # This prevents a Gemini/API failure from permanently
    # losing a job post.

    new_posts_file = ROOT / "data" / "new_posts.json"

    output = {
        "fetched_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "posts": new_posts
    }

    with open(
        new_posts_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            indent=2,
            ensure_ascii=False
        )

    print(
        f"New posts ready for AI: "
        f"{len(new_posts)}"
    )


if __name__ == "__main__":
    main()
