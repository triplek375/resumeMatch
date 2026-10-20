"""Job loading, field normalization, and embedding-text construction."""

import csv
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Union

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dataProcessing.config import load_config


@dataclass(frozen=True)
class Job:
    id: str
    title: str
    company: str
    location: str
    url: str
    description: str


def _as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _generated_id(url: str, title: str, company: str) -> str:
    identity = url if url else f"{title}\0{company}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"job-{digest}"


def _normalize_job(row: Mapping[str, Any], field_map: Mapping[str, str]) -> Job:
    values = {
        name: _as_text(row.get(source))
        for name, source in field_map.items()
        if name in {"id", "title", "company", "location", "url", "description"}
    }
    for name in ("title", "company", "location", "url", "description"):
        values.setdefault(name, "")
    job_id = values.get("id", "") or _generated_id(
        values["url"], values["title"], values["company"]
    )
    return Job(
        id=job_id,
        **{
            name: values[name]
            for name in ("title", "company", "location", "url", "description")
        },
    )


def _read_rows(path: Path) -> Iterable[Mapping[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        with path.open("r", encoding="utf-8-sig") as input_file:
            rows = json.load(input_file)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("JSON job data must be a list of objects.")
        return rows
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as input_file:
            return list(csv.DictReader(input_file))
    raise ValueError("Jobs file must have a .json or .csv extension.")


def load_jobs_from_db(
    conn: Optional[Any] = None,
    field_map: Optional[Mapping[str, str]] = None,
) -> list[Job]:
    """Load jobs from MariaDB 'jobs' table."""
    from db import get_jobs

    mapping = field_map if field_map is not None else load_config()["field_map"]
    rows = get_jobs(conn=conn)
    return [_normalize_job(row, mapping) for row in rows]


def load_jobs(
    path: Optional[Union[Path, str]] = None,
    field_map: Optional[Mapping[str, str]] = None,
    conn: Optional[Any] = None,
) -> list[Job]:
    """Load jobs from database (default) or from a JSON/CSV file if path is specified."""
    if path is None:
        return load_jobs_from_db(conn=conn, field_map=field_map)
    mapping = field_map if field_map is not None else load_config()["field_map"]
    input_path = Path(path)
    rows = _read_rows(input_path)
    return [_normalize_job(row, mapping) for row in rows]


def build_job_text(job: Job) -> str:
    """Build the plain-text content used to embed one job."""
    fields = (
        ("Title", job.title),
        ("Company", job.company),
        ("Location", job.location),
        ("Description", job.description),
    )
    return "\n".join(f"{label}: {value}" for label, value in fields if value)