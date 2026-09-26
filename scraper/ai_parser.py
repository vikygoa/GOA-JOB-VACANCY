import json
import os
from pathlib import Path
from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
NEW_FILE = ROOT / "data/new_posts.json"
JOBS_FILE = ROOT / "data/jobs.json"

SCHEMA = {
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
        "job_type": {"type": "string"}
    },
    "required": [
        "title", "company", "location", "qualification", "experience",
        "salary", "last_date", "contact", "description", "job_type"
    ]
}


def parse_image(client, model, image_path):
    mime = "image/jpeg"
    data = Path(image_path).read_bytes()

    prompt = """Read this job vacancy poster carefully.

Return ONLY factual information visible in the image.
Do not invent missing information. Use "Not specified" when a field is absent.
Clean obvious OCR/spelling issues.
Rewrite the description in clear, short English, but do not add facts.
If several jobs are present, describe the main vacancy represented by the poster.
This will be published on a Goa jobs website."""

    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=data, mime_type=mime),
            prompt
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=SCHEMA,
            temperature=0.1
        )
    )
    return json.loads(response.text)


def main():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY GitHub Secret is missing.")

    new_data = json.loads(NEW_FILE.read_text(encoding="utf-8"))
    old_data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    jobs = old_data.get("jobs", [])

    client = genai.Client(api_key=key)
    model = CONFIG.get("gemini_model", "gemini-2.5-flash")

    existing_ids = {j.get("source_post_id") for j in jobs}
    max_jobs = int(CONFIG.get("max_jobs_per_run", 30))

    for item in new_data.get("posts", [])[:max_jobs]:
        pid = item["post_id"]
        if pid in existing_ids:
            continue

        try:
            parsed = parse_image(client, model, ROOT / item["image"])
        except Exception as e:
            print(f"WARNING: Gemini failed for {pid}: {e}")
            continue

        parsed["source_account"] = item["account"]
        parsed["source_post_id"] = pid
        parsed["source_image"] = item["image"]
        parsed["source_url"] = (
            f"https://www.instagram.com/{item['account']}/"
        )
        jobs.insert(0, parsed)
        existing_ids.add(pid)
        print(f"Added job: {parsed.get('title')}")

    old_data["last_updated"] = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat()
    old_data["jobs"] = jobs[:500]

    JOBS_FILE.write_text(
        json.dumps(old_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
