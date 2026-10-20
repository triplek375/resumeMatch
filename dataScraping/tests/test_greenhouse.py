import csv
import json
from pathlib import Path

import pytest

from dataScraping.greenhouse import (
    clean_html,
    filter_jobs,
    load_company_tokens,
    load_roles,
    load_yaml,
    normalize_job,
    parse_board_file,
    resolve_search_path,
    scrape_role,
)


SCRAPER_DIR = Path(__file__).resolve().parents[1]
FIXTURE_PATH = SCRAPER_DIR / "tests" / "fixtures" / "greenhouse_response.json"


def test_clean_html_unescapes_content_and_keeps_paragraph_and_bullet_breaks():
    cleaned = clean_html(
        "&lt;p&gt;Build reliable &amp;amp; scalable pipelines.&lt;/p&gt;"
        "&lt;ul&gt;&lt;li&gt;Use Python&lt;/li&gt;&lt;li&gt;Own ETL&lt;/li&gt;&lt;/ul&gt;"
    )

    assert cleaned == "Build reliable & scalable pipelines.\n- Use Python\n- Own ETL"


def test_role_filter_is_case_insensitive_and_matches_any_keyword():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    config = load_yaml(SCRAPER_DIR / "config.yaml")
    jobs = [
        normalize_job(raw, "Example", "2026-10-17T00:00:00+00:00", config["field_map"])
        for raw in fixture["jobs"]
    ]

    selected = filter_jobs(jobs, ["Data Engineer", "Analytics Engineer"])

    assert [job["title"] for job in selected] == ["Data Engineer", "Analytics Engineer"]


def test_field_mapping_produces_the_normalized_job_contract():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    config = load_yaml(SCRAPER_DIR / "config.yaml")

    job = normalize_job(
        fixture["jobs"][0],
        "Example",
        "2026-10-17T00:00:00+00:00",
        config["field_map"],
    )

    assert list(job) == [
        "source", "id", "company", "title", "location", "url", "description", "scraped_at"
    ]
    assert job["source"] == "greenhouse"
    assert job["id"] == "101"
    assert job["company"] == "Example"
    assert job["title"] == "Data Engineer"
    assert job["location"] == "New York, NY"
    assert job["url"] == "https://boards.greenhouse.io/example/jobs/101"
    assert "- Own ETL workflows" in job["description"]


def test_load_company_tokens_reads_external_token_file_and_removes_duplicates(tmp_path):
    token_path = tmp_path / "boards.txt"
    token_path.write_text(
        "# Greenhouse boards\ncompany-a\ncompany-b\ncompany-a\n",
        encoding="utf-8",
    )

    assert load_company_tokens(token_path) == ["company-a", "company-b"]


def test_load_company_tokens_rejects_placeholder(tmp_path):
    token_path = tmp_path / "boards.txt"
    token_path.write_text("replace-with-a-real-board-token\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Replace placeholder"):
        load_company_tokens(token_path)


def test_failed_boards_do_not_overwrite_previous_output(tmp_path, monkeypatch):
    token_path = tmp_path / "boards.txt"
    token_path.write_text("invalid-board\n", encoding="utf-8")
    output_path = tmp_path / "data" / "jobs.csv"
    output_path.parent.mkdir(parents=True)
    output_path.write_text("previous results", encoding="utf-8")
    monkeypatch.setattr(
        "dataScraping.greenhouse.fetch_company_jobs",
        lambda *args: (None, False),
    )

    with pytest.raises(RuntimeError, match="No Greenhouse boards returned"):
        from dataScraping.greenhouse import scrape_role

        scrape_role(
            "data_engineer",
            companies_path=token_path,
            project_root=tmp_path,
            session=object(),
        )

    assert output_path.read_text(encoding="utf-8") == "previous results"


def test_scrape_role_writes_normalized_jobs_as_csv(tmp_path, monkeypatch):
    token_path = tmp_path / "boards.txt"
    token_path.write_text("example\n", encoding="utf-8")
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        "dataScraping.greenhouse.fetch_company_jobs",
        lambda *args: (fixture["jobs"], False),
    )

    selected = scrape_role(
        "data_engineer",
        companies_path=token_path,
        project_root=tmp_path,
        session=object(),
    )

    output_path = tmp_path / "data" / "jobs.csv"
    with output_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        rows = list(reader)

    assert len(selected) == 1
    assert len(rows) == 1
    assert reader.fieldnames == [
        "source", "id", "company", "title", "location", "url", "description", "scraped_at"
    ]
    assert rows[0]["source"] == "greenhouse"
    assert rows[0]["title"] == "Data Engineer"
    assert "- Own ETL workflows" in rows[0]["description"]


def test_parse_board_file_extracts_companies_and_roles(tmp_path):
    board_file = tmp_path / "search.txt"
    board_file.write_text(
        "#companies\nstripe\nairbnb\n\n#roles\ndata engineer\ncloud engineer\n",
        encoding="utf-8",
    )

    companies, roles = parse_board_file(board_file)
    assert companies == ["stripe", "airbnb"]
    assert roles == ["data engineer", "cloud engineer"]
    assert load_company_tokens(board_file) == ["stripe", "airbnb"]
    assert load_roles(board_file) == ["data engineer", "cloud engineer"]


def test_scrape_role_with_exact_string_without_expansion(tmp_path, monkeypatch):
    token_path = tmp_path / "search.txt"
    token_path.write_text(
        "#companies\nexample\n\n#roles\ndata engineer\n",
        encoding="utf-8",
    )
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        "dataScraping.greenhouse.fetch_company_jobs",
        lambda *args: (fixture["jobs"], False),
    )

    # Scrapes strictly for the exact string "data engineer" without keyword expansion
    selected = scrape_role(
        "data engineer",
        companies_path=token_path,
        project_root=tmp_path,
        session=object(),
    )

    output_path = tmp_path / "data" / "jobs.csv"
    assert output_path.exists()
    assert len(selected) == 1
    assert selected[0]["title"] == "Data Engineer"


def test_scrape_multiple_roles_writes_single_jobs_csv(tmp_path, monkeypatch):
    token_path = tmp_path / "search.txt"
    token_path.write_text(
        "#companies\nexample\n\n#roles\ndata engineer\nanalytics engineer\n",
        encoding="utf-8",
    )
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        "dataScraping.greenhouse.fetch_company_jobs",
        lambda *args: (fixture["jobs"], False),
    )

    selected = scrape_role(
        ["data engineer", "analytics engineer"],
        companies_path=token_path,
        project_root=tmp_path,
        session=object(),
    )

    output_path = tmp_path / "data" / "jobs.csv"
    assert output_path.exists()
    assert not (tmp_path / "data" / "jobs").exists()
    with output_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        rows = list(reader)

    assert reader.fieldnames == [
        "source", "id", "company", "title", "location", "url", "description", "scraped_at"
    ]
    assert len(selected) == 2
    assert len(rows) == 2
    assert [row["title"] for row in rows] == ["Data Engineer", "Analytics Engineer"]


def test_main_cli_writes_single_jobs_csv(tmp_path, monkeypatch):
    from dataScraping.greenhouse import main

    search_file = tmp_path / "search.txt"
    search_file.write_text(
        "#companies\nexample\n\n#roles\ndata engineer\nanalytics engineer\n",
        encoding="utf-8",
    )
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        "dataScraping.greenhouse.fetch_company_jobs",
        lambda *args: (fixture["jobs"], False),
    )
    monkeypatch.setattr("dataScraping.greenhouse.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["greenhouse.py", "--companies", str(search_file)],
    )

    main()

    output_path = tmp_path / "data" / "jobs.csv"
    assert output_path.exists()
    with output_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        rows = list(reader)
    assert len(rows) == 2
    assert reader.fieldnames == [
        "source", "id", "company", "title", "location", "url", "description", "scraped_at"
    ]
