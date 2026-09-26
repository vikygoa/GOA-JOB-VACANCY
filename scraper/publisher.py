import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PENDING_FILE = ROOT / "data" / "pending_jobs.json"
PUBLISHED_FILE = ROOT / "data" / "jobs.json"

PUBLISH_COUNT = 3


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def main():
    pending_data = load_json(
        PENDING_FILE,
        {
            "last_updated": None,
            "jobs": [],
        },
    )

    published_data = load_json(
        PUBLISHED_FILE,
        {
            "last_updated": None,
            "jobs": [],
        },
    )

    pending = pending_data.get("jobs", [])
    published = published_data.get("jobs", [])

    if not isinstance(pending, list):
        pending = []

    if not isinstance(published, list):
        published = []

    published_ids = {
        str(job.get("job_id"))
        for job in published
        if job.get("job_id")
    }

    # Remove anything that somehow already exists in published data.
    pending = [
        job
        for job in pending
        if str(job.get("job_id")) not in published_ids
    ]

    # Publish only three jobs per run.
    to_publish = pending[:PUBLISH_COUNT]
    remaining = pending[PUBLISH_COUNT:]

    if to_publish:
        for job in reversed(to_publish):
            published.insert(0, job)

        print(
            f"Published {len(to_publish)} job(s) this run."
        )
    else:
        print("No pending jobs to publish.")

    # Keep a reasonable history.
    published_data["jobs"] = published[:500]
    published_data["last_updated"] = datetime.now(
        timezone.utc
    ).isoformat()

    pending_data["jobs"] = remaining
    pending_data["last_updated"] = datetime.now(
        timezone.utc
    ).isoformat()

    PUBLISHED_FILE.write_text(
        json.dumps(
            published_data,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    PENDING_FILE.write_text(
        json.dumps(
            pending_data,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(
        f"Published total: {len(published_data['jobs'])}"
    )
    print(
        f"Still pending: {len(pending_data['jobs'])}"
    )


if __name__ == "__main__":
    main()
