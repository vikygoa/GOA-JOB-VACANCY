import json
import os
from pathlib import Path
from datetime import datetime, timezone

from google import genai
from google.genai import types


ROOT = Path(__file__).resolve().parents[1]

CONFIG = json.loads(
    (ROOT / "config.json").read_text(
        encoding="utf-8"
    )
)

NEW_FILE = ROOT / "data" / "new_posts.json"
JOBS_FILE = ROOT / "data" / "jobs.json"
PROCESSED_FILE = ROOT / "data" / "processed_posts.json"


# Structured output schema sent to Gemini.
JOB_SCHEMA = {
    "type": "object",

    "properties": {

        "title": {
            "type": "string"
        },

        "company": {
            "type": "string"
        },

        "location": {
            "type": "string"
        },

        "qualification": {
            "type": "string"
        },

        "experience": {
            "type": "string"
        },

        "salary": {
            "type": "string"
        },

        "last_date": {
            "type": "string"
        },

        "contact": {
            "type": "string"
        },

        "description": {
            "type": "string"
        },

        "job_type": {
            "type": "string"
        }
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
        "job_type"
    ]
}


def load_json(path, default):
    """Safely load a JSON file."""

    try:

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        return data

    except Exception:

        return default


def mark_processed(post_id):
    """
    Mark an Instagram post as processed.

    IMPORTANT:
    This function is called ONLY after Gemini
    successfully returns valid job data.
    """

    data = load_json(
        PROCESSED_FILE,
        {
            "processed_posts": []
        }
    )

    ids = data.get(
        "processed_posts",
        []
    )

    if not isinstance(ids, list):
        ids = []

    if post_id not in ids:
        ids.append(post_id)

    # Keep the file from growing forever.
    ids = list(
        dict.fromkeys(ids)
    )[-1000:]

    data["processed_posts"] = ids

    with open(
        PROCESSED_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False
        )


def parse_job_image(
    client,
    model,
    image_path
):
    """Send a job poster image to Gemini."""

    image_data = Path(
        image_path
    ).read_bytes()

    prompt = """
Read this job vacancy poster carefully.

Extract only information that is actually visible
in the image.

Rules:

1. Do not invent information.
2. If information is missing, use "Not specified".
3. Correct obvious spelling/OCR errors.
4. Keep company names and contact information accurate.
5. Rewrite the description into clear, short English.
6. Do not add facts that are not visible.
7. The job is intended for a Goa jobs website.
8. Return only the requested JSON structure.
"""

    response = client.models.generate_content(

        model=model,

        contents=[

            types.Part.from_bytes(
                data=image_data,
                mime_type="image/jpeg"
            ),

            prompt
        ],

        config=types.GenerateContentConfig(

            response_mime_type="application/json",

            response_schema=JOB_SCHEMA,

            temperature=0.1
        )
    )

    if not response.text:
        raise RuntimeError(
            "Gemini returned an empty response"
        )

    try:

        parsed = json.loads(
            response.text
        )

    except json.JSONDecodeError as error:

        raise RuntimeError(
            f"Gemini returned invalid JSON: {error}"
        )

    if not isinstance(parsed, dict):
        raise RuntimeError(
            "Gemini response is not a JSON object"
        )

    return parsed


def main():

    api_key = os.environ.get(
        "GEMINI_API_KEY"
    )

    if not api_key:

        raise RuntimeError(
            "GEMINI_API_KEY GitHub Secret is missing."
        )

    new_data = load_json(
        NEW_FILE,
        {
            "posts": []
        }
    )

    jobs_data = load_json(
        JOBS_FILE,
        {
            "last_updated": None,
            "jobs": []
        }
    )

    processed_data = load_json(
        PROCESSED_FILE,
        {
            "processed_posts": []
        }
    )

    jobs = jobs_data.get(
        "jobs",
        []
    )

    if not isinstance(jobs, list):
        jobs = []

    processed_ids = set(
        processed_data.get(
            "processed_posts",
            []
        )
    )

    existing_job_ids = set()

    for job in jobs:

        post_id = job.get(
            "source_post_id"
        )

        if post_id:
            existing_job_ids.add(
                str(post_id)
            )

    client = genai.Client(
        api_key=api_key
    )

    model = CONFIG.get(
        "gemini_model",
        "gemini-3.8-flash"
    )

    max_jobs = int(
        CONFIG.get(
            "max_jobs_per_run",
            30
        )
    )

    success_count = 0
    failure_count = 0

    posts = new_data.get(
        "posts",
        []
    )

    for item in posts[:max_jobs]:

        post_id = str(
            item["post_id"]
        )

        # Safety check.
        if post_id in processed_ids:
            continue

        if post_id in existing_job_ids:

            # The job already exists in jobs.json.
            # It is safe to mark it processed.
            mark_processed(
                post_id
            )

            processed_ids.add(
                post_id
            )

            continue

        image_path = ROOT / item["image"]

        if not image_path.exists():

            failure_count += 1

            print(
                f"WARNING: Image missing for "
                f"{post_id}. "
                f"Post will be retried."
            )

            continue

        try:

            parsed = parse_job_image(
                client,
                model,
                image_path
            )

        except Exception as error:

            failure_count += 1

            print(
                f"WARNING: Gemini failed for "
                f"{post_id}: {error}"
            )

            print(
                "Post was NOT marked processed "
                "and will be retried."
            )

            continue

        # Add source information only AFTER
        # Gemini returned valid job data.
        parsed["source_account"] = (
            item["account"]
        )

        parsed["source_post_id"] = (
            post_id
        )

        parsed["source_image"] = (
            item["image"]
        )

        parsed["source_url"] = (
            f"https://www.instagram.com/"
            f"{item['account']}/"
        )

        parsed["processed_at"] = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )

        # Add the new job.
        jobs.insert(
            0,
            parsed
        )

        existing_job_ids.add(
            post_id
        )

        # CRITICAL:
        # Only now mark the Instagram post
        # as successfully processed.
        mark_processed(
            post_id
        )

        processed_ids.add(
            post_id
        )

        success_count += 1

        print(
            f"Added job: "
            f"{parsed.get('title', 'Untitled')}"
        )

    jobs_data["last_updated"] = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    jobs_data["jobs"] = jobs[:500]

    with open(
        JOBS_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            jobs_data,
            f,
            indent=2,
            ensure_ascii=False
        )

    print("")
    print(
        "Gemini processing complete."
    )
    print(
        f"Successful jobs: {success_count}"
    )
    print(
        f"Failed/retry jobs: {failure_count}"
    )
    print(
        f"Total jobs stored: {len(jobs_data['jobs'])}"
    )


if __name__ == "__main__":
    main()
