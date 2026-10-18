"""Fetch, cache, normalize, and filter Greenhouse board postings."""

import argparse
import csv
import hashlib
import html
import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import requests
import yaml


DATA_SCRAPING_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = DATA_SCRAPING_DIR.parent
CONFIG_PATH = DATA_SCRAPING_DIR / "config.yaml"
LOGGER = logging.getLogger("dataScraping.greenhouse")
OUTPUT_FIELDS = (
    "id",
    "title",
    "company",
    "location",
    "url",
    "description",
    "source",
    "scraped_at",
)


class _ReadableHTMLParser(HTMLParser):
    """Extract text while retaining paragraph and list boundaries."""

    _BLOCK_TAGS = {
        "address", "article", "blockquote", "div", "dl", "dt", "dd", "h1",
        "h2", "h3", "h4", "h5", "h6", "li", "ol", "p", "section", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in {"script", "style"}:
            self.ignored_depth += 1
            return
        if self.ignored_depth:
            return
        if tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.ignored_depth:
            self.ignored_depth -= 1
            return
        if not self.ignored_depth and tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)


def clean_html(value: Any) -> str:
    """Unescape escaped HTML and return readable plain text."""
    if value is None:
        return ""
    parser = _ReadableHTMLParser()
    parser.feed(html.unescape(str(value)))
    parser.close()
    lines = []
    for line in "".join(parser.parts).splitlines():
        cleaned = re.sub(r"[\t\f\v ]+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as yaml_file:
        return yaml.safe_load(yaml_file)


def load_company_tokens(path: Path) -> list[str]:
    """Read board tokens from TXT, YAML, or JSON without a baked-in company list."""
    suffix = path.suffix.lower()
    if suffix == ".txt":
        with path.open("r", encoding="utf-8-sig") as token_file:
            tokens = [
                line.strip()
                for line in token_file
                if line.strip() and not line.lstrip().startswith("#")
            ]
    elif suffix in {".yaml", ".yml"}:
        data = load_yaml(path)
        tokens = data.get("companies") if isinstance(data, dict) else data
    elif suffix == ".json":
        with path.open("r", encoding="utf-8-sig") as token_file:
            data = json.load(token_file)
        tokens = data.get("companies") if isinstance(data, dict) else data
    else:
        raise ValueError("Company tokens file must be .txt, .yaml, .yml, or .json.")

    if not isinstance(tokens, list) or any(not isinstance(token, str) for token in tokens):
        raise ValueError("Company tokens must be a list of strings.")
    normalized = list(dict.fromkeys(token.strip() for token in tokens if token.strip()))
    if not normalized:
        raise ValueError("Company tokens file contains no board tokens.")
    placeholder_markers = ("replace-with", "your-real-token", "<token>")
    if any(marker in token.casefold() for token in normalized for marker in placeholder_markers):
        raise ValueError("Replace placeholder entries with real Greenhouse board tokens.")
    return normalized


def _mapped_value(row: Mapping[str, Any], source_path: str) -> Any:
    value: Any = row
    for key in source_path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def normalize_job(
    raw_job: Mapping[str, Any],
    company: str,
    scraped_at: str,
    field_map: Mapping[str, str],
) -> dict[str, str]:
    """Map one Greenhouse posting to the normalized output contract."""
    title = str(_mapped_value(raw_job, field_map["title"]) or "").strip()
    location_value = _mapped_value(raw_job, field_map["location"])
    location = str(location_value or "").strip()
    url = str(_mapped_value(raw_job, field_map["url"]) or "").strip()
    description = clean_html(_mapped_value(raw_job, field_map["description"]))
    source_id = _mapped_value(raw_job, field_map.get("id", "id"))
    if source_id is None or not str(source_id).strip():
        identity = url or f"{title}\0{company}"
        source_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()

    return {
        "id": str(source_id),
        "title": title,
        "company": company,
        "location": location,
        "url": url,
        "description": description,
        "source": "greenhouse",
        "scraped_at": scraped_at,
    }


def filter_jobs(
    jobs: Sequence[dict[str, str]], keywords: Sequence[str]
) -> list[dict[str, str]]:
    """Keep jobs whose title contains at least one case-insensitive keyword."""
    normalized_keywords = [keyword.casefold().strip() for keyword in keywords if keyword.strip()]
    if not normalized_keywords:
        return []
    return [
        job
        for job in jobs
        if any(keyword in job["title"].casefold() for keyword in normalized_keywords)
    ]


def _read_cached_response(cache_path: Path) -> dict[str, Any]:
    with cache_path.open("r", encoding="utf-8") as cache_file:
        payload = json.load(cache_file)
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError("Cached Greenhouse response must contain a jobs list.")
    return payload


def fetch_company_jobs(
    token: str,
    cache_dir: Path,
    refresh: bool,
    session: requests.Session,
    request_config: Mapping[str, Any],
) -> tuple[Optional[list[dict[str, Any]]], bool]:
    """Read a cached response or fetch/cache one; return jobs and cache status."""
    cache_path = cache_dir / f"{token}.json"
    if cache_path.exists() and not refresh:
        return _read_cached_response(cache_path)["jobs"], True

    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    headers = {"User-Agent": str(request_config["user_agent"])}
    retries = int(request_config["max_retries"])
    timeout = float(request_config["timeout_seconds"])
    backoff = float(request_config["backoff_seconds"])

    for attempt in range(retries + 1):
        try:
            response = session.get(url, headers=headers, timeout=timeout)
            if response.status_code == 404:
                return None, False
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < retries:
                    time.sleep(backoff * (2**attempt))
                    continue
            response.raise_for_status()
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(response.text, encoding="utf-8")
            payload = json.loads(response.text)
            if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
                raise ValueError("Greenhouse response must contain a jobs list.")
            return payload["jobs"], False
        except requests.RequestException:
            if attempt >= retries:
                raise
            time.sleep(backoff * (2**attempt))
    raise RuntimeError(f"Could not fetch Greenhouse board {token}.")


def scrape_role(
    role: str,
    refresh: bool = False,
    limit_companies: Optional[int] = None,
    config_path: Path = CONFIG_PATH,
    companies_path: Optional[Path] = None,
    project_root: Path = PROJECT_ROOT,
    session: Optional[requests.Session] = None,
) -> list[dict[str, str]]:
    """Scrape configured companies and write normalized postings for a role."""
    if companies_path is None:
        raise ValueError("Provide a board-token file with --companies.")
    config = load_yaml(config_path)
    companies = load_company_tokens(companies_path)
    roles = config.get("roles", {})
    if role not in roles:
        raise ValueError(f"Unknown role '{role}'. Available roles: {', '.join(roles)}")
    if limit_companies is not None and limit_companies < 1:
        raise ValueError("--limit-companies must be at least 1.")
    if limit_companies is not None:
        companies = companies[:limit_companies]

    request_config = config["request"]
    field_map = config["field_map"]
    cache_dir = project_root / "data" / "raw" / "greenhouse"
    output_path = project_root / "data" / "jobs" / f"greenhouse_{role}.csv"
    timestamp = datetime.now(timezone.utc).isoformat()
    matching_jobs: list[dict[str, str]] = []
    successful_boards = 0
    owned_session = session is None
    http = session or requests.Session()
    network_requests = 0

    try:
        for token in companies:
            company = token.replace("-", " ").title()
            cache_path = cache_dir / f"{token}.json"
            making_request = refresh or not cache_path.exists()
            if making_request and network_requests:
                delay = random.uniform(
                    float(request_config["delay_min_seconds"]),
                    float(request_config["delay_max_seconds"]),
                )
                time.sleep(delay)
            if making_request:
                network_requests += 1
            try:
                raw_jobs, cached = fetch_company_jobs(
                    token, cache_dir, refresh, http, request_config
                )
                if raw_jobs is None:
                    LOGGER.error("company=%s fetched=0 kept=0 error=HTTP 404", token)
                    continue
                successful_boards += 1
                normalized = [
                    normalize_job(job, company, timestamp, field_map)
                    for job in raw_jobs
                    if isinstance(job, Mapping)
                ]
                kept = filter_jobs(normalized, roles[role])
                matching_jobs.extend(kept)
                cache_note = " cache=hit" if cached else ""
                LOGGER.info(
                    "company=%s fetched=%d kept=%d errors=0%s",
                    token,
                    len(raw_jobs),
                    len(kept),
                    cache_note,
                )
            except Exception as error:
                LOGGER.error(
                    "company=%s fetched=0 kept=0 errors=1 error=%s",
                    token,
                    error,
                )
    finally:
        if owned_session:
            http.close()

    if successful_boards == 0:
        raise RuntimeError(
            "No Greenhouse boards returned successfully; output was not written. "
            "Check that the board tokens are valid and try again."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(matching_jobs)
    LOGGER.info("role=%s total_kept=%d output=%s", role, len(matching_jobs), output_path)
    return matching_jobs


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Greenhouse jobs by role.")
    parser.add_argument("--role", required=True, help="Role key from dataScraping/config.yaml")
    parser.add_argument(
        "--companies",
        required=True,
        type=Path,
        help="TXT, YAML, or JSON file containing Greenhouse board tokens",
    )
    parser.add_argument("--refresh", action="store_true", help="Refresh raw response caches")
    parser.add_argument("--limit-companies", type=int, help="Test with the first N companies")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        scrape_role(
            args.role,
            args.refresh,
            args.limit_companies,
            companies_path=args.companies,
        )
    except (ValueError, RuntimeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()