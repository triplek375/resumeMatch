"""Tests for database operations and dataProcessing db integration."""

import hashlib
from unittest.mock import MagicMock

import pytest

import db
from dataProcessing.jobs import Job, load_jobs, load_jobs_from_db


def test_compute_url_hash():
    url = "https://boards.greenhouse.io/test/jobs/123"
    expected = hashlib.sha256(url.encode("utf-8")).hexdigest()
    assert db.compute_url_hash(url) == expected

    # Empty url falls back to title, company, external_id
    fallback_expected = hashlib.sha256("Dev\0Acme\099".encode("utf-8")).hexdigest()
    assert db.compute_url_hash("", "Dev", "Acme", "99") == fallback_expected


def test_save_jobs_with_mock_connection():
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    jobs = [
        {
            "source": "greenhouse",
            "id": "100",
            "title": "Data Engineer",
            "company": "Acme",
            "location": "Remote",
            "url": "https://example.com/100",
            "description": "Pipelines",
            "scraped_at": "2026-10-20T12:00:00+00:00",
        }
    ]

    count = db.save_jobs(jobs, conn=mock_conn)
    assert count == 1
    mock_cursor.executemany.assert_called_once()
    sql, params = mock_cursor.executemany.call_args[0]
    assert "INSERT INTO jobs" in sql
    assert len(params) == 1
    assert params[0][0] == "greenhouse"
    assert params[0][1] == "100"
    assert params[0][2] == "Data Engineer"
    assert params[0][3] == "Acme"
    assert params[0][4] == "Remote"
    assert params[0][5] == "https://example.com/100"
    assert params[0][7] == "Pipelines"
    assert params[0][8] == "2026-10-20 12:00:00"


def test_save_companies_with_mock_connection():
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    companies = [
        {"company": "acme-co", "reason": "NoMatchingJob"},
        {"company": "not-found", "reason": "NoMatchingCompany"},
    ]

    count = db.save_companies(companies, conn=mock_conn)
    assert count == 2
    mock_cursor.executemany.assert_called_once()
    sql, params = mock_cursor.executemany.call_args[0]
    assert "INSERT INTO companies" in sql
    assert len(params) == 2
    assert params[0] == ("acme-co", "NoMatchingJob", None)
    assert params[1] == ("not-found", "NoMatchingCompany", None)


def test_get_jobs_with_mock_connection():
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [
        {
            "id": 1,
            "source": "greenhouse",
            "external_id": "100",
            "title": "Data Engineer",
            "company": "Acme",
            "location": "Remote",
            "url": "https://example.com/100",
            "url_hash": "hash1",
            "description": "Pipelines",
            "scraped_at": None,
        }
    ]

    jobs = db.get_jobs(conn=mock_conn, limit=10)
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Data Engineer"
    mock_cursor.execute.assert_called_once()
    assert "LIMIT 10" in mock_cursor.execute.call_args[0][0]


def test_load_jobs_from_db_returns_dataclass_jobs(monkeypatch):
    mock_rows = [
        {
            "id": 1,
            "source": "greenhouse",
            "external_id": "100",
            "title": "Data Engineer",
            "company": "Acme",
            "location": "Remote",
            "url": "https://example.com/100",
            "url_hash": "hash1",
            "description": "Pipelines",
            "scraped_at": None,
        }
    ]
    monkeypatch.setattr(db, "get_jobs", lambda conn=None, limit=None: mock_rows)

    jobs = load_jobs()
    assert len(jobs) == 1
    assert isinstance(jobs[0], Job)
    assert jobs[0].id == "1"
    assert jobs[0].title == "Data Engineer"
    assert jobs[0].company == "Acme"
    assert jobs[0].description == "Pipelines"


def test_update_job_embedding_with_mock_connection():
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    db.update_job_embedding(1, [0.1, 0.2, 0.3], conn=mock_conn)
    mock_cursor.execute.assert_called_once()
    sql, params = mock_cursor.execute.call_args[0]
    assert "UPDATE jobs SET embedding = Vec_FromText(%s) WHERE id = %s" in sql
    assert params[1] == 1
    assert "0.10000000" in params[0]


def test_get_jobs_missing_embedding_with_mock_connection():
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [{"id": 7, "title": "ML Engineer"}]

    jobs = db.get_jobs_missing_embedding(conn=mock_conn, limit=5)
    assert len(jobs) == 1
    assert jobs[0]["title"] == "ML Engineer"
    sql = mock_cursor.execute.call_args[0][0]
    assert "WHERE embedding IS NULL" in sql
    assert "LIMIT 5" in sql


def test_upsert_resume_with_mock_connection():
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"id": 42, "content_hash": "abc"}

    resume = db.upsert_resume(
        name="Ada",
        content="Python engineer",
        model="BAAI/bge-m3",
        embedding=[0.1, 0.2, 0.3],
        conn=mock_conn,
    )

    assert resume["id"] == 42
    sql, params = mock_cursor.execute.call_args_list[0][0]
    assert "INSERT INTO resumes" in sql
    assert params[0] == "Ada"
    assert params[2] == hashlib.sha256(b"Python engineer").hexdigest()
    assert "0.10000000" in params[4]


def test_search_similar_jobs_with_mock_connection():
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [{"id": 2, "title": "Data Engineer", "score": 0.92}]

    results = db.search_similar_jobs([0.1, 0.2, 0.3], top_n=5, role="Data", conn=mock_conn)
    assert len(results) == 1
    assert results[0]["title"] == "Data Engineer"
    sql, params = mock_cursor.execute.call_args[0]
    assert "VEC_DISTANCE_COSINE" in sql
    assert "AND j.title LIKE %s" in sql
    assert params[1] == "%Data%"
    assert params[2] == 5


def test_save_matches_with_mock_connection():
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    count = db.save_matches(
        resume_id=8,
        matches=[
            {"job_id": 11, "title": "ML Engineer", "score": 0.81},
            {"id": 12, "title": "Data Engineer", "score": 0.74},
        ],
        conn=mock_conn,
    )

    assert count == 2
    sql, params = mock_cursor.executemany.call_args[0]
    assert "INSERT INTO match_results" in sql
    assert params[0][0] == 8
    assert params[0][1] == 11
    assert params[0][2] == "ML Engineer"
    assert params[1][1] == 12

