"""CLI to embed a resume, rank jobs, and optionally save/export matches."""

import argparse
import csv
import sys
from pathlib import Path
from typing import Iterable, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import db
from dataProcessing.embedders import Embedder


def load_resume_text(resume_path: Path) -> str:
    """Read the candidate resume text from a .txt file."""
    if resume_path.suffix.lower() != ".txt":
        raise ValueError("Resume file must be a .txt file.")
    return resume_path.read_text(encoding="utf-8-sig").strip()


def build_match_rows(rows: Sequence[dict], role_name: Optional[str] = None) -> list[dict]:
    """Normalize DB rows into match payloads for save/export."""
    prepared = []
    for row in rows:
        title = str(row.get("title") or "")
        prepared.append(
            {
                "job_id": row.get("id"),
                "title": title,
                "company": row.get("company") or "",
                "location": row.get("location") or "",
                "score": float(row.get("score") or 0.0),
                "role": role_name or title,
            }
        )
    return prepared


def print_match_table(rows: Sequence[dict]) -> None:
    """Render a compact match table to stdout."""
    if not rows:
        print("No matching jobs found.")
        return

    headers = ["Rank", "Job ID", "Title", "Company", "Location", "Score"]
    table = []
    for idx, row in enumerate(rows, start=1):
        table.append(
            [
                idx,
                row.get("job_id"),
                row.get("title") or "",
                row.get("company") or "",
                row.get("location") or "",
                f"{float(row.get('score') or 0.0):.4f}",
            ]
        )

    widths = [len(str(header)) for header in headers]
    for row in table:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(str(value)))

    def fmt(values: Sequence[object]) -> str:
        return " | ".join(str(value).ljust(widths[idx]) for idx, value in enumerate(values))

    print(fmt(headers))
    print("-+-".join("-" * width for width in widths))
    for row in table:
        print(fmt(row))


def export_matches(rows: Sequence[dict], out_path: Path) -> None:
    """Write match rows to a CSV file."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["job_id", "title", "company", "location", "score"])
        for row in rows:
            writer.writerow(
                [
                    row.get("job_id"),
                    row.get("title") or "",
                    row.get("company") or "",
                    row.get("location") or "",
                    f"{float(row.get('score') or 0.0):.6f}",
                ]
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed a resume and rank jobs by cosine similarity.")
    parser.add_argument("--resume", required=True, help="Path to the resume .txt file.")
    parser.add_argument("--top", type=int, default=20, help="Number of jobs to rank.")
    parser.add_argument("--role", default=None, help="Optional title filter applied with LIKE matching.")
    parser.add_argument("--save", action="store_true", help="Persist the top matches to match_results.")
    parser.add_argument(
        "--export",
        default=None,
        help="Optional output CSV path, e.g. data/matches.csv",
    )
    args = parser.parse_args()

    resume_path = Path(args.resume)
    if args.top <= 0:
        raise ValueError("--top must be greater than 0.")

    resume_text = load_resume_text(resume_path)
    embedder = Embedder()
    resume_vector = embedder.embed([resume_text])[0].tolist()

    conn = db.get_connection()
    try:
        resume_record = db.upsert_resume(
            name=resume_path.stem,
            content=resume_text,
            model=embedder.model_name,
            embedding=resume_vector,
            conn=conn,
        )
        if not resume_record:
            raise RuntimeError("Resume insert/update failed.")

        rows = db.search_similar_jobs(
            embedding=resume_vector,
            top_n=args.top,
            role=args.role,
            conn=conn,
        )
        prepared = build_match_rows(rows, role_name=args.role)

        if args.save:
            saved = db.save_matches(resume_record["id"], prepared, conn=conn)
            print(f"Saved {saved} matches to match_results.")

        if args.export:
            export_path = Path(args.export)
            export_matches(prepared, export_path)
            print(f"Exported {len(prepared)} matches to {export_path}.")

        print_match_table(prepared)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
