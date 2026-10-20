"""Database connection and queries for MariaDB."""

import argparse
import csv
import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import pymysql
import pymysql.cursors

LOGGER = logging.getLogger("resumematch.db")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "resumematch")
DB_PASSWORD = os.getenv("DB_PASSWORD", "resumematch_pass")
DB_NAME = os.getenv("DB_NAME", "resumematch")
DB_SOCKET = os.getenv("DB_SOCKET", None)


def format_datetime(val: Any) -> Optional[str]:
    """Format ISO or date string to MariaDB DATETIME format (YYYY-MM-DD HH:MM:SS)."""
    if not val:
        return None
    s = str(val).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1]
    if "+" in s:
        s = s.split("+")[0]
    if "." in s:
        s = s.split(".")[0]
    return s.replace("T", " ")


def compute_url_hash(
    url: str = "",
    title: str = "",
    company: str = "",
    external_id: str = "",
) -> str:
    """Return a deterministic SHA-256 digest to deduplicate jobs."""
    if url:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()
    identity = f"{title}\0{company}\0{external_id}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def get_connection(
    host: Optional[str] = None,
    port: Optional[int] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
    database: Optional[str] = None,
    unix_socket: Optional[str] = None,
) -> pymysql.Connection:
    """Connect to MariaDB database using credentials or socket fallback."""
    h = host or DB_HOST
    p = port or DB_PORT
    u = user or DB_USER
    pwd = password if password is not None else DB_PASSWORD
    db = database or DB_NAME
    sock = unix_socket or DB_SOCKET

    conn_params: dict[str, Any] = {
        "user": u,
        "database": db,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": True,
    }
    if pwd:
        conn_params["password"] = pwd

    candidate_sockets = [s for s in (sock, "/run/mysqld/mysqld.sock", "/var/run/mysqld/mysqld.sock") if s and os.path.exists(s)]

    # Attempt 1: Standard connection (TCP or socket)
    if sock and os.path.exists(sock):
        try:
            return pymysql.connect(unix_socket=sock, **conn_params)
        except Exception:
            pass

    try:
        return pymysql.connect(host=h, port=p, **conn_params)
    except Exception as first_error:
        # Attempt 2: If connecting to localhost failed, try candidate sockets with configured credentials
        for s in candidate_sockets:
            try:
                return pymysql.connect(unix_socket=s, **conn_params)
            except Exception:
                pass

        # Attempt 3: If candidate sockets exist, try current OS user (socket auth)
        for s in candidate_sockets:
            try:
                os_user = os.getenv("USER", "root")
                return pymysql.connect(
                    unix_socket=s,
                    user=os_user,
                    database=db,
                    charset="utf8mb4",
                    cursorclass=pymysql.cursors.DictCursor,
                    autocommit=True,
                )
            except Exception:
                pass

        raise first_error


def save_jobs(
    jobs: Sequence[Mapping[str, Any]],
    conn: Optional[pymysql.Connection] = None,
) -> int:
    """Insert or update jobs in MariaDB 'jobs' table."""
    if not jobs:
        return 0

    owned = conn is None
    connection = conn or get_connection()
    try:
        with connection.cursor() as cursor:
            sql = """
                INSERT INTO jobs (
                    source, external_id, title, company, location,
                    url, url_hash, description, scraped_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    title = VALUES(title),
                    company = VALUES(company),
                    location = VALUES(location),
                    description = VALUES(description),
                    scraped_at = VALUES(scraped_at)
            """
            rows = []
            seen_hashes: set[str] = set()
            for j in jobs:
                source = str(j.get("source") or "greenhouse")
                ext_id = str(j.get("id") or j.get("external_id") or "")
                title = str(j.get("title") or "")
                company = str(j.get("company") or "")
                location = str(j.get("location") or "")
                url = str(j.get("url") or "")
                url_hash = str(
                    j.get("url_hash") or compute_url_hash(url, title, company, ext_id)
                )
                if url_hash in seen_hashes:
                    continue
                seen_hashes.add(url_hash)

                desc = str(j.get("description") or "")
                scraped_at = format_datetime(j.get("scraped_at"))
                rows.append((
                    source,
                    ext_id,
                    title,
                    company,
                    location,
                    url,
                    url_hash,
                    desc,
                    scraped_at,
                ))

            if rows:
                cursor.executemany(sql, rows)
            return len(rows)
    finally:
        if owned:
            connection.close()


def save_companies(
    companies: Sequence[Mapping[str, Any]],
    conn: Optional[pymysql.Connection] = None,
) -> int:
    """Insert or update unmatched companies in MariaDB 'companies' table."""
    if not companies:
        return 0

    owned = conn is None
    connection = conn or get_connection()
    try:
        with connection.cursor() as cursor:
            sql = """
                INSERT INTO companies (company, reason, scraped_at)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    reason = VALUES(reason),
                    scraped_at = VALUES(scraped_at)
            """
            rows = []
            seen_companies: set[str] = set()
            for c in companies:
                comp = str(c.get("company") or "").strip()
                if not comp or comp in seen_companies:
                    continue
                seen_companies.add(comp)
                reason = str(c.get("reason") or "NoMatchingJob")
                scraped_at = format_datetime(c.get("scraped_at"))
                rows.append((comp, reason, scraped_at))

            if rows:
                cursor.executemany(sql, rows)
            return len(rows)
    finally:
        if owned:
            connection.close()


def update_job_embedding(
    job_id: Any,
    embedding: Sequence[float],
    conn: Optional[pymysql.Connection] = None,
) -> None:
    """Update the native vector embedding for a specific job."""
    owned = conn is None
    connection = conn or get_connection()
    try:
        with connection.cursor() as cursor:
            if isinstance(embedding, (list, tuple)):
                vec_str = "[" + ", ".join(f"{float(x):.8f}" for x in embedding) + "]"
            else:
                vec_str = str(embedding)
            cursor.execute(
                "UPDATE jobs SET embedding = Vec_FromText(%s) WHERE id = %s",
                (vec_str, job_id),
            )
    finally:
        if owned:
            connection.close()


def get_jobs(
    conn: Optional[pymysql.Connection] = None,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Fetch jobs from the 'jobs' table."""
    owned = conn is None
    connection = conn or get_connection()
    try:
        with connection.cursor() as cursor:
            sql = (
                "SELECT id, source, external_id, title, company, location, "
                "url, url_hash, description, scraped_at "
                "FROM jobs ORDER BY id ASC"
            )
            if limit is not None and limit > 0:
                sql += f" LIMIT {int(limit)}"
            cursor.execute(sql)
            return list(cursor.fetchall())
    finally:
        if owned:
            connection.close()


def get_companies(
    conn: Optional[pymysql.Connection] = None,
) -> list[dict[str, Any]]:
    """Fetch companies from the 'companies' table."""
    owned = conn is None
    connection = conn or get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, company, reason, scraped_at FROM companies ORDER BY id ASC"
            )
            return list(cursor.fetchall())
    finally:
        if owned:
            connection.close()


def migrate_csv_files(
    jobs_csv_path: Optional[Path] = None,
    companies_csv_path: Optional[Path] = None,
    conn: Optional[pymysql.Connection] = None,
) -> tuple[int, int]:
    """Migrate rows from CSV files to MariaDB tables."""
    root = Path(__file__).resolve().parent
    j_path = jobs_csv_path or (root / "data" / "jobs.csv")
    c_path = companies_csv_path or (root / "data" / "companies.csv")

    jobs_migrated = 0
    companies_migrated = 0

    if c_path.exists():
        with c_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            comps = list(reader)
        companies_migrated = save_companies(comps, conn=conn)

    if j_path.exists():
        with j_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            jobs = list(reader)
        jobs_migrated = save_jobs(jobs, conn=conn)

    return jobs_migrated, companies_migrated


def main() -> None:
    parser = argparse.ArgumentParser(description="ResumeMatch MariaDB utility.")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("test", help="Test MariaDB connection and show tables")
    subparsers.add_parser("status", help="Show jobs and companies counts")
    subparsers.add_parser("migrate-csv", help="Migrate data from CSV files if present")

    args = parser.parse_args()

    if args.command == "test" or args.command is None:
        try:
            conn = get_connection()
            with conn.cursor() as cursor:
                cursor.execute("SHOW TABLES")
                tables = [list(r.values())[0] for r in cursor.fetchall()]
            print("Successfully connected to MariaDB!")
            print(f"Database: {DB_NAME}")
            print(f"Tables: {', '.join(tables)}")
            conn.close()
        except Exception as e:
            print(f"Connection failed: {e}")
            print("\nTo set up MariaDB, execute data/DDL.sql:")
            print("  sudo mariadb < data/DDL.sql")

    elif args.command == "status":
        try:
            jobs = get_jobs()
            comps = get_companies()
            print(f"Total jobs in DB: {len(jobs)}")
            print(f"Total companies in DB: {len(comps)}")
        except Exception as e:
            print(f"Error fetching status: {e}")

    elif args.command == "migrate-csv":
        try:
            j_cnt, c_cnt = migrate_csv_files()
            print(f"Migrated {j_cnt} jobs and {c_cnt} companies to database.")
        except Exception as e:
            print(f"Migration error: {e}")


if __name__ == "__main__":
    main()
