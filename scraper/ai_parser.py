import json
import os
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

NEW_FILE = ROOT / "data" / "new_posts.json"
PROCESSED_FILE = ROOT / "data" / "processed_posts.json"
PENDING_FILE = ROOT / "data" / "pending_jobs.json"

JOB_SCHEMA = {
    "type": "object",
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "company": {"type": "string"},
                    "location": {"type": "string"},
                    "qualification": {"type": "string"},
                    "experience": {"type": "string"},
                    "salary": {"type": "string"},
                    "last_date": {"type": "string"},
                    "contact": {"type": "string"},
                    "description": {"type": "string"},
                    "job_type": {"type": "string"},
                },
                "required": [
                    "title",
                    "company",
                    "location",
                    "qualification",
                    "experience",
                    "salary",
                    "last_date",
                    "contact",
                    "description",
                    "job_type",
                ],
            },
        }
    },
    "required": ["jobs"],
}


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def mark_processed(post_id):
    data = load_json(
        PROCESSED_FILE,
        {"processed_posts": []},
    )

    ids = data.get("processed_posts", [])
    if not isinstance(ids, list):
        ids = []

    if post_id not in ids:
        ids.append(post_id)

    data["processed_posts"] = list(dict.fromkeys(ids))[-2000:]

    PROCESSED_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def parse_carousel(client, model, image_paths):
    contents = []

    # Gemini receives every slide from ONE Instagram post in ONE request.
    for image_path in image_paths:
        contents.append(
            types.Part.from_bytes(
                data=Path(ROOT / image_path).read_bytes(),
                mime_type="image/jpeg",
            )
        )

    contents.append(
        """
You are extracting jobs from ONE Instagram job announcement.

The attached images are ALL slides of the same Instagram post.
Read them together. Some slides may continue information from
previous slides.

Create one job object for EACH distinct vacancy/post listed in
the complete carousel.

Rules:
- Do not treat every slide as a separate job if slides are only
  continuation/details.
- Combine information across slides when necessary.
- Do not invent missing information.
- Use "Not specified" when information is absent.
- Correct obvious spelling/OCR errors.
- Preserve company names, locations, salaries, dates and contacts.
- Rewrite each description clearly and briefly without adding facts.
- If one poster contains 10 different posts, return 10 jobs.
- Return only the requested JSON structure.
"""
    )

    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=JOB_SCHEMA,
            temperature=0.1,
        ),
    )

    if not response.text:
        raise RuntimeError("Gemini returned an empty response")

    data = json.loads(response.text)

    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise RuntimeError("Gemini did not return a jobs array")

    return data["jobs"]


def make_job_id(post_id, index, job):
    # Stable ID: same Instagram post + same extracted position.
    # This prevents publishing the same extracted job twice.
    title = str(job.get("title", "")).strip().lower()
    company = str(job.get("company", "")).strip().lower()

    import hashlib
    raw = f"{post_id}|{index}|{title}|{company}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY GitHub Secret is missing.")

    new_data = load_json(NEW_FILE, {"posts": []})
    pending_data = load_json(
        PENDING_FILE,
        {
            "last_updated": None,
            "jobs": [],
        },
    )

    pending = pending_data.get("jobs", [])
    if not isinstance(pending, list):
        pending = []

    existing_job_ids = {
        str(job.get("job_id"))
        for job in pending
        if job.get("job_id")
    }

    client = genai.Client(api_key=api_key)
    model = CONFIG.get("gemini_model", "gemini-3.8-flash")
    max_posts = int(CONFIG.get("max_ai_posts_per_run", 30))

    success = 0
    failed = 0

    for post in new_data.get("posts", [])[:max_posts]:
        post_id = str(post["post_id"])

        try:
            extracted_jobs = parse_carousel(
                client,
                model,
                post["images"],
            )
        except Exception as exc:
            failed += 1
            print(
                f"WARNING: Gemini failed for post {post_id}: {exc}"
            )
            print("Post remains unprocessed and will be retried.")
            continue

        if not extracted_jobs:
            failed += 1
            print(
                f"WARNING: Gemini returned zero jobs for {post_id}. "
                "Post remains unprocessed."
            )
            continue

        added = 0

        for index, job in enumerate(extracted_jobs, start=1):
            job_id = make_job_id(
                post_id,
                index,
                job,
            )

            if job_id in existing_job_ids:
                continue

            job["job_id"] = job_id
            job["source_post_id"] = post_id
            job["source_account"] = post["account"]
            job["source_images"] = post["images"]
            job["source_url"] = (
                f"https://www.instagram.com/{post['account']}/"
            )
            job["created_at"] = datetime.now(
                timezone.utc
            ).isoformat()

            pending.append(job)
            existing_job_ids.add(job_id)
            added += 1

        # CRITICAL:
        # Mark the Instagram post processed only after the whole
        # carousel was successfully interpreted and its jobs
        # were added/deduplicated in the pending queue.
        mark_processed(post_id)

        success += 1

        print(
            f"Processed @{post['account']} {post_id}: "
            f"{len(extracted_jobs)} job(s), {added} new"
        )

    pending_data["last_updated"] = datetime.now(
        timezone.utc
    ).isoformat()

    pending_data["jobs"] = pending[-2000:]

    PENDING_FILE.write_text(
        json.dumps(
            pending_data,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(
        f"AI complete: {success} post(s) processed, "
        f"{failed} post(s) failed/retry."
    )
    print(f"Pending jobs: {len(pending_data['jobs'])}")


if __name__ == "__main__":
    main()
