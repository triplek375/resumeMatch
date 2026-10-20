import csv
import json
from pathlib import Path

import pytest

import db
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


@pytest.fixture(autouse=True)
def mock_db_operations(monkeypatch):
    """Mock database calls during unit tests so running MariaDB is not required."""
    recorded = {"jobs": [], "companies": []}

    def fake_save_jobs(jobs, conn=None):
        recorded["jobs"].extend(jobs)
        return len(jobs)

    def fake_save_companies(companies, conn=None):
        recorded["companies"].extend(companies)
        return len(companies)

    monkeypatch.setattr(db, "save_jobs", fake_save_jobs)
    monkeypatch.setattr(db, "save_companies", fake_save_companies)
    monkeypatch.setattr("dataScraping.greenhouse.save_jobs", fake_save_jobs, raising=False)
    monkeypatch.setattr("dataScraping.greenhouse.save_companies", fake_save_companies, raising=False)
    return recorded


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
        scrape_role(
            "data_engineer",
            companies_path=token_path,
            project_root=tmp_path,
            session=object(),
            output_path=output_path,
        )

    assert output_path.read_text(encoding="utf-8") == "previous results"


def test_scrape_role_writes_normalized_jobs_as_csv_and_db(tmp_path, monkeypatch, mock_db_operations):
    token_path = tmp_path / "boards.txt"
    token_path.write_text("example\n", encoding="utf-8")
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        "dataScraping.greenhouse.fetch_company_jobs",
        lambda *args: (fixture["jobs"], False),
    )

    output_path = tmp_path / "data" / "jobs.csv"
    selected = scrape_role(
        "data_engineer",
        companies_path=token_path,
        project_root=tmp_path,
        session=object(),
        output_path=output_path,
    )

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

    # Also verified saved to database
    assert len(mock_db_operations["jobs"]) == 1
    assert mock_db_operations["jobs"][0]["title"] == "Data Engineer"


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


def test_scrape_role_with_exact_string_without_expansion(tmp_path, monkeypatch, mock_db_operations):
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

    selected = scrape_role(
        "data engineer",
        companies_path=token_path,
        project_root=tmp_path,
        session=object(),
    )

    assert len(selected) == 1
    assert selected[0]["title"] == "Data Engineer"
    assert len(mock_db_operations["jobs"]) == 1
    assert mock_db_operations["jobs"][0]["title"] == "Data Engineer"


def test_scrape_multiple_roles_saves_to_database(tmp_path, monkeypatch, mock_db_operations):
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

    assert len(selected) == 2
    assert [j["title"] for j in selected] == ["Data Engineer", "Analytics Engineer"]
    assert len(mock_db_operations["jobs"]) == 2
    assert [j["title"] for j in mock_db_operations["jobs"]] == ["Data Engineer", "Analytics Engineer"]


def test_main_cli_saves_to_database_by_default(tmp_path, monkeypatch, mock_db_operations):
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

    assert len(mock_db_operations["jobs"]) == 2
    assert [j["title"] for j in mock_db_operations["jobs"]] == ["Data Engineer", "Analytics Engineer"]


def test_main_cli_custom_companies_output(tmp_path, monkeypatch):
    from dataScraping.greenhouse import main

    search_file = tmp_path / "search.txt"
    search_file.write_text(
        "#companies\nexample\nnot-found-co\n\n#roles\ndata engineer\n",
        encoding="utf-8",
    )
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def mock_fetch(token, *args, **kwargs):
        if token == "example":
            return fixture["jobs"], False
        return None, False

    monkeypatch.setattr("dataScraping.greenhouse.fetch_company_jobs", mock_fetch)
    monkeypatch.setattr("dataScraping.greenhouse.PROJECT_ROOT", tmp_path)
    custom_comp_csv = tmp_path / "custom_unmatched.csv"
    monkeypatch.setattr(
        "sys.argv",
        ["greenhouse.py", "--companies", str(search_file), "--companies-output", str(custom_comp_csv)],
    )

    main()

    assert custom_comp_csv.exists()
    with custom_comp_csv.open("r", encoding="utf-8-sig", newline="") as comp_file:
        reader = csv.DictReader(comp_file)
        rows = list(reader)
    assert len(rows) == 1
    assert rows[0] == {"company": "not-found-co", "reason": "NoMatchingCompany"}


def test_scrape_records_unmatched_companies_with_reasons(tmp_path, monkeypatch, mock_db_operations):
    search_file = tmp_path / "search.txt"
    search_file.write_text(
        "#companies\ncompany-matching\ncompany-no-jobs\ncompany-not-found\n\n#roles\ndata engineer\n",
        encoding="utf-8",
    )
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def mock_fetch(token, *args, **kwargs):
        if token == "company-matching":
            return fixture["jobs"], False
        elif token == "company-no-jobs":
            return [{"id": 999, "title": "Marketing Manager", "content": "Ad campaigns"}], False
        elif token == "company-not-found":
            return None, False
        raise ValueError(f"Unexpected token: {token}")

    monkeypatch.setattr("dataScraping.greenhouse.fetch_company_jobs", mock_fetch)

    selected = scrape_role(
        "data engineer",
        companies_path=search_file,
        project_root=tmp_path,
        session=object(),
    )

    assert len(selected) == 1
    assert selected[0]["company"] == "Company Matching"

    assert len(mock_db_operations["companies"]) == 2
    assert mock_db_operations["companies"] == [
        {"company": "company-no-jobs", "reason": "NoMatchingJob"},
        {"company": "company-not-found", "reason": "NoMatchingCompany"},
    ]


def test_failed_boards_still_records_unmatched_companies(tmp_path, monkeypatch, mock_db_operations):
    token_path = tmp_path / "search.txt"
    token_path.write_text("invalid-one\ninvalid-two\n", encoding="utf-8")
    monkeypatch.setattr(
        "dataScraping.greenhouse.fetch_company_jobs",
        lambda *args: (None, False),
    )

    with pytest.raises(RuntimeError, match="No Greenhouse boards returned"):
        scrape_role(
            "data engineer",
            companies_path=token_path,
            project_root=tmp_path,
            session=object(),
        )

    assert len(mock_db_operations["companies"]) == 2
    assert mock_db_operations["companies"] == [
        {"company": "invalid-one", "reason": "NoMatchingCompany"},
        {"company": "invalid-two", "reason": "NoMatchingCompany"},
    ]
