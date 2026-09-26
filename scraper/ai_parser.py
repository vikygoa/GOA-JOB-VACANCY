import json
import os
import re
import time
import hashlib
from pathlib import Path

from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

MODEL = CONFIG.get("gemini_model", "gemini-3.8-flash")
MAX_POSTS_PER_RUN = int(CONFIG.get("max_ai_posts_per_run", 5))
MAX_REQUESTS_PER_RUN = int(CONFIG.get("max_gemini_requests_per_run", 5))
REQUEST_INTERVAL_SECONDS = float(CONFIG.get("gemini_request_interval_seconds", 16))
RETRY_503_SECONDS = int(CONFIG.get("retry_503_seconds", 30))

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY is not set")

client = genai.Client(api_key=API_KEY)

new_posts_file = ROOT / "data" / "new_posts.json"
pending_file = ROOT / "data" / "pending_jobs.json"
processed_file = ROOT / "data" / "processed_posts.json"

new_posts = json.loads(new_posts_file.read_text(encoding="utf-8")) if new_posts_file.exists() else []
pending_data = json.loads(pending_file.read_text(encoding="utf-8")) if pending_file.exists() else {"last_updated": None, "jobs": []}
processed_data = json.loads(processed_file.read_text(encoding="utf-8")) if processed_file.exists() else {"processed_posts": []}

if isinstance(new_posts, dict):
    new_posts = new_posts.get("posts", [])

pending_jobs = pending_data.get("jobs", [])
processed_ids = set(processed_data.get("processed_posts", []))

# Every Gemini attempt counts toward the free-tier request budget.
request_count = 0
last_request_time = 0.0


def wait_for_request_slot():
    global last_request_time
    now = time.monotonic()
    wait = REQUEST_INTERVAL_SECONDS - (now - last_request_time)
    if wait > 0:
        print(f"Waiting {wait:.1f}s before next Gemini request...")
        time.sleep(wait)


def make_job_id(post_id, index, job):
    raw = "|".join([
        str(post_id),
        str(index),
        str(job.get("title", "")).strip().lower(),
        str(job.get("company", "")).strip().lower(),
        str(job.get("location", "")).strip().lower(),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def extract_retry_seconds(error_text):
    m = re.search(r"retry in\s+([\d.]+)s", error_text, re.I)
    if m:
        return min(max(float(m.group(1)), 1), 90)
    return None


def generate_with_safe_retry(contents):
    """
    Maximum total Gemini HTTP attempts in this workflow run is MAX_REQUESTS_PER_RUN.
    429: stop immediately; the post remains unprocessed.
    503: wait once and retry if a request slot remains.
    """
    global request_count, last_request_time

    attempts_for_call = 0

    while True:
        if request_count >= MAX_REQUESTS_PER_RUN:
            raise RuntimeError("REQUEST_BUDGET_EXHAUSTED")

        wait_for_request_slot()
        request_count += 1
        last_request_time = time.monotonic()
        attempts_for_call += 1

        try:
            return client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema={
                        "type": "OBJECT",
                        "properties": {
                            "jobs": {
                                "type": "ARRAY",
                                "items": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "title": {"type": "STRING"},
                                        "company": {"type": "STRING"},
                                        "location": {"type": "STRING"},
                                        "employment_type": {"type": "STRING"},
                                        "vacancies": {"type": "STRING"},
                                        "qualification": {"type": "STRING"},
                                        "salary": {"type": "STRING"},
                                        "last_date": {"type": "STRING"},
                                        "apply": {"type": "STRING"},
                                        "source": {"type": "STRING"},
                                        "description": {"type": "STRING"}
                                    },
                                    "required": ["title", "company", "location"]
                                }
                            }
                        },
                        "required": ["jobs"]
                    }
                )
            )

        except Exception as e:
            error_text = str(e)
            is_429 = "429" in error_text or "RESOURCE_EXHAUSTED" in error_text
            is_503 = "503" in error_text or "UNAVAILABLE" in error_text

            if is_429:
                raise RuntimeError("GEMINI_429") from e

            if is_503 and attempts_for_call < 2 and request_count < MAX_REQUESTS_PER_RUN:
                print(f"Gemini 503. Waiting {RETRY_503_SECONDS}s before one retry...")
                time.sleep(RETRY_503_SECONDS)
                continue

            if is_503:
                raise RuntimeError("GEMINI_503") from e

            raise


def get_downloaded_media_paths(post):
    """
    The Instagram scraper stores downloaded images in `images`.
    Older versions used `media_paths`. Accept both so the parser
    remains compatible with either format.
    """
    raw_paths = (
        post.get("media_paths")
        or post.get("images")
        or post.get("image_paths")
        or post.get("files")
        or []
    )

    if isinstance(raw_paths, str):
        raw_paths = [raw_paths]

    resolved = []
    for raw in raw_paths:
        if not raw:
            continue

        path = Path(str(raw))

        # Paths written by instagram_scraper.py are relative to ROOT.
        if not path.is_absolute():
            path = ROOT / path

        if path.exists() and path.is_file() and path.stat().st_size > 0:
            resolved.append(path)

    return resolved


def parse_carousel(post):
    media_paths = get_downloaded_media_paths(post)
    if not media_paths:
        raise RuntimeError(
            f"No downloaded media for post {post.get('post_id', '')}"
        )

    prompt = """
You are extracting job vacancies from an Instagram job post/carousel.

IMPORTANT:
- ALL images supplied belong to ONE Instagram post.
- Read ALL slides together before answering.
- A carousel may contain several different vacancies.
- Return ONE job object for EACH DISTINCT VACANCY.
- Do NOT treat continuation/detail slides of the same vacancy as separate jobs.
- Combine information from multiple slides when they describe the same vacancy.
- Do not invent missing information.
- If a field is not visible, use an empty string.
- Keep vacancy counts, qualification, salary/pay level, location, dates and application information accurate.
- Return JSON only in the requested schema.
"""

    contents = [prompt]
    for path in media_paths:
        contents.append(
            types.Part.from_bytes(
                data=Path(path).read_bytes(),
                mime_type="image/jpeg"
            )
        )

    response = generate_with_safe_retry(contents)
    text = response.text or "{}"
    data = json.loads(text)
    return data.get("jobs", [])


def main():
    global pending_jobs, processed_ids

    posts = [
        p for p in new_posts
        if str(p.get("post_id", "")) not in processed_ids
    ]

    if not posts:
        print("No new posts waiting for Gemini.")
        return

    posts = posts[:MAX_POSTS_PER_RUN]

    processed_this_run = 0
    failed_this_run = 0

    for post in posts:
        post_id = str(post.get("post_id", ""))
        username = post.get("username") or post.get("account", "")
        try:
            jobs = parse_carousel(post)

            if not jobs:
                print(
                    f"WARNING: Gemini returned zero jobs for {username} {post_id}. "
                    "Post remains unprocessed."
                )
                failed_this_run += 1
                continue

            existing_pending_ids = {j.get("job_id") for j in pending_jobs}
            new_count = 0

            for index, job in enumerate(jobs):
                if not isinstance(job, dict):
                    continue

                job_id = make_job_id(post_id, index, job)
                if job_id in existing_pending_ids:
                    continue

                job["job_id"] = job_id
                job["instagram_post_id"] = post_id
                job["instagram_username"] = username
                job["images"] = [str(p.relative_to(ROOT)) for p in get_downloaded_media_paths(post)]
                pending_jobs.append(job)
                existing_pending_ids.add(job_id)
                new_count += 1

            # Only mark the Instagram post processed AFTER Gemini succeeded
            # and its jobs were safely added to the queue.
            processed_ids.add(post_id)
            processed_this_run += 1

            print(
                f"Processed @{username} {post_id}: "
                f"{len(jobs)} job(s), {new_count} new"
            )

            if request_count >= MAX_REQUESTS_PER_RUN:
                print("Gemini request budget reached. Remaining posts will wait.")
                break

        except RuntimeError as e:
            reason = str(e)

            if reason == "REQUEST_BUDGET_EXHAUSTED":
                print("Gemini request budget exhausted. Remaining posts will wait.")
                break

            if reason == "GEMINI_429":
                print(
                    f"WARNING: Gemini 429 rate limit for post {post_id}. "
                    "Stopping this run. Post remains unprocessed."
                )
                failed_this_run += 1
                break

            if reason == "GEMINI_503":
                print(
                    f"WARNING: Gemini 503 unavailable for post {post_id}. "
                    "Post remains unprocessed; it will be retried next run."
                )
                failed_this_run += 1
                continue

            print(f"WARNING: Gemini failed for post {post_id}: {e}")
            failed_this_run += 1

        except Exception as e:
            print(f"WARNING: Gemini failed for post {post_id}: {e}")
            print("Post remains unprocessed and will be retried.")
            failed_this_run += 1

    processed_data["processed_posts"] = sorted(processed_ids)
    processed_file.write_text(
        json.dumps(processed_data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    pending_data["jobs"] = pending_jobs
    pending_data["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    pending_file.write_text(
        json.dumps(pending_data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print(
        f"AI complete: {processed_this_run} post(s) processed, "
        f"{failed_this_run} failed/retry."
    )
    print(f"Gemini requests used: {request_count}/{MAX_REQUESTS_PER_RUN}")
    print(f"Pending jobs: {len(pending_jobs)}")


if __name__ == "__main__":
    main()
