"""Embed jobs and store vector rows back to MariaDB."""

import argparse
import html
import re
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import db
from dataProcessing.embedders import Embedder
from dataProcessing.jobs import Job, build_job_text, load_jobs


def strip_html_and_entities(text: str) -> str:
    """Remove HTML tags and HTML entities from a text payload."""
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    cleaned = html.unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _text_for_embedding(job: Job) -> str:
    """Return normalized job text for embedding, without HTML markup."""
    text = build_job_text(job)
    return strip_html_and_entities(text)


def fetch_jobs_to_embed(limit: Optional[int] = None, reembed: bool = False) -> list[dict]:
    """Fetch DB jobs that need embedding or are selected for re-embedding."""
    if reembed:
        return db.get_jobs(limit=limit)
    return db.get_jobs_missing_embedding(limit=limit)


def embed_jobs(limit: Optional[int] = None, reembed: bool = False, batch_size: int = 32) -> int:
    """Embed jobs and write vectors back to the jobs table in a single transaction."""
    rows = fetch_jobs_to_embed(limit=limit, reembed=reembed)
    if not rows:
        return 0

    jobs = []
    for row in rows:
        job = Job(
            id=str(row.get("id", "")),
            title=str(row.get("title") or ""),
            company=str(row.get("company") or ""),
            location=str(row.get("location") or ""),
            url=str(row.get("url") or ""),
            description=str(row.get("description") or ""),
        )
        jobs.append(job)

    embedder = Embedder()
    for index in range(0, len(jobs), batch_size):
        batch_jobs = jobs[index : index + batch_size]
        texts = [_text_for_embedding(job) for job in batch_jobs]
        vectors = embedder.embed(texts)

        conn = db.get_connection()
        try:
            conn.begin()
            items = []
            for job, vector in zip(batch_jobs, vectors):
                if not job.id:
                    continue
                items.append((db.format_vector(vector), int(job.id)))
            if items:
                cursor = conn.cursor()
                cursor.executemany(
                    "UPDATE jobs SET embedding = Vec_FromText(%s) WHERE id = %s",
                    items,
                )
            conn.commit()
        finally:
            conn.close()

    return len(jobs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed job records for similarity matching.")
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of jobs processed.")
    parser.add_argument("--reembed", action="store_true", help="Recompute embeddings for all selected jobs.")
    parser.add_argument("--batch-size", type=int, default=32, help="Jobs to embed before writing a batch.")
    args = parser.parse_args()

    try:
        count = embed_jobs(limit=args.limit, reembed=args.reembed, batch_size=max(1, args.batch_size))
        print(f"Embedded {count} jobs.")
    except Exception as exc:  # pragma: no cover - command-line fallback
        print(f"Embedding failed: {exc}")
        raise


if __name__ == "__main__":
    main()
