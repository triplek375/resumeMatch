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
from typing import Any, Mapping, Optional, Sequence, Union

import requests
import yaml


DATA_SCRAPING_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = DATA_SCRAPING_DIR.parent
CONFIG_PATH = DATA_SCRAPING_DIR / "config.yaml"
LOGGER = logging.getLogger("dataScraping.greenhouse")
OUTPUT_FIELDS = (
    "source",
    "id",
    "company",
    "title",
    "location",
    "url",
    "description",
    "scraped_at",
)
COMPANIES_OUTPUT_FIELDS = (
    "company",
    "reason",
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


def resolve_search_path(
    path: Optional[Path] = None, project_root: Optional[Path] = None
) -> Path:
    """Resolve the search tokens file, checking project root for search.txt."""
    if path is not None:
        return path
    root = project_root if project_root is not None else PROJECT_ROOT
    search_root = root / "search.txt"
    if search_root.exists():
        return search_root
    board_root = root / "board_tokens.txt"
    if board_root.exists():
        return board_root
    return search_root


# Backwards compatibility alias
resolve_board_tokens_path = resolve_search_path


def parse_board_file(path: Path) -> tuple[list[str], list[str]]:
    """Read company tokens and optional roles from TXT, YAML, or JSON."""
    suffix = path.suffix.lower()
    companies: list[str] = []
    roles: list[str] = []

    if suffix == ".txt":
        current_section = "companies"
        with path.open("r", encoding="utf-8-sig") as token_file:
            for line in token_file:
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("#"):
                    header = stripped.lstrip("#").strip().lower()
                    if header in {"companies", "company", "boards", "board", "board_tokens", "tokens"}:
                        current_section = "companies"
                        continue
                    elif header in {"roles", "role", "keywords", "keyword", "titles", "title"}:
                        current_section = "roles"
                        continue
                    else:
                        continue
                if current_section == "roles":
                    roles.append(stripped)
                else:
                    companies.append(stripped)
    elif suffix in {".yaml", ".yml"}:
        data = load_yaml(path)
        if isinstance(data, dict):
            raw_companies = data.get("companies", [])
            raw_roles = data.get("roles", [])
            if isinstance(raw_companies, list):
                companies = [str(c) for c in raw_companies]
            if isinstance(raw_roles, list):
                roles = [str(r) for r in raw_roles]
        elif isinstance(data, list):
            companies = [str(c) for c in data]
    elif suffix == ".json":
        with path.open("r", encoding="utf-8-sig") as token_file:
            data = json.load(token_file)
        if isinstance(data, dict):
            raw_companies = data.get("companies", [])
            raw_roles = data.get("roles", [])
            if isinstance(raw_companies, list):
                companies = [str(c) for c in raw_companies]
            if isinstance(raw_roles, list):
                roles = [str(r) for r in raw_roles]
        elif isinstance(data, list):
            companies = [str(c) for c in data]
    else:
        raise ValueError("Company tokens file must be .txt, .yaml, .yml, or .json.")

    normalized_companies = list(dict.fromkeys(c.strip() for c in companies if c.strip()))
    normalized_roles = list(dict.fromkeys(r.strip() for r in roles if r.strip()))
    return normalized_companies, normalized_roles


def load_company_tokens(path: Path) -> list[str]:
    """Read board tokens from TXT, YAML, or JSON without a baked-in company list."""
    companies, _ = parse_board_file(path)
    if not companies:
        raise ValueError("Company tokens file contains no board tokens.")
    placeholder_markers = ("replace-with", "your-real-token", "<token>")
    if any(marker in token.casefold() for token in companies for marker in placeholder_markers):
        raise ValueError("Replace placeholder entries with real Greenhouse board tokens.")
    return companies


def load_roles(path: Path) -> list[str]:
    """Read target roles from TXT, YAML, or JSON if specified."""
    _, roles = parse_board_file(path)
    return roles


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
        "source": "greenhouse",
        "id": str(source_id),
        "company": company,
        "title": title,
        "location": location,
        "url": url,
        "description": description,
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
    role: Union[str, Sequence[str]],
    refresh: bool = False,
    limit_companies: Optional[int] = None,
    config_path: Optional[Path] = None,
    companies_path: Optional[Path] = None,
    project_root: Optional[Path] = None,
    session: Optional[requests.Session] = None,
    output_path: Optional[Path] = None,
    companies_output_path: Optional[Path] = None,
) -> list[dict[str, str]]:
    """Scrape configured companies, write matching postings to jobs CSV, and log unmatched companies."""
    root = project_root if project_root is not None else PROJECT_ROOT
    cfg_path = config_path if config_path is not None else CONFIG_PATH
    resolved_companies_path = resolve_board_tokens_path(companies_path, root)
    if not resolved_companies_path.exists():
        raise ValueError(
            f"Company tokens file not found at {resolved_companies_path}. Provide a valid file with --companies."
        )
    config = load_yaml(cfg_path)
    companies = load_company_tokens(resolved_companies_path)

    # Use exact role strings without keyword expansion
    if isinstance(role, str):
        target_role_strings = [role]
    else:
        target_role_strings = list(role)

    keywords = [r.replace("_", " ").strip() for r in target_role_strings if r.strip()]
    if not keywords:
        raise ValueError("No valid role search strings provided.")

    if limit_companies is not None and limit_companies < 1:
        raise ValueError("--limit-companies must be at least 1.")
    if limit_companies is not None:
        companies = companies[:limit_companies]

    request_config = config["request"]
    field_map = config["field_map"]
    cache_dir = root / "data" / "raw" / "greenhouse"
    resolved_output_path = (
        Path(output_path) if output_path is not None else root / "data" / "jobs.csv"
    )
    resolved_companies_output_path = (
        Path(companies_output_path)
        if companies_output_path is not None
        else root / "data" / "companies.csv"
    )
    timestamp = datetime.now(timezone.utc).isoformat()
    matching_jobs: list[dict[str, str]] = []
    unmatched_companies: list[dict[str, str]] = []
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
            raw_jobs = None
            try:
                raw_jobs, cached = fetch_company_jobs(
                    token, cache_dir, refresh, http, request_config
                )
                if raw_jobs is None:
                    LOGGER.error("company=%s fetched=0 kept=0 error=HTTP 404", token)
                    unmatched_companies.append({
                        "company": token,
                        "reason": "NoMatchingCompany",
                    })
                    continue
                successful_boards += 1
                normalized = [
                    normalize_job(job, company, timestamp, field_map)
                    for job in raw_jobs
                    if isinstance(job, Mapping)
                ]
                kept = filter_jobs(normalized, keywords)
                if not kept:
                    unmatched_companies.append({
                        "company": token,
                        "reason": "NoMatchingJob",
                    })
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
                if raw_jobs is None:
                    unmatched_companies.append({
                        "company": token,
                        "reason": "NoMatchingCompany",
                    })
    finally:
        if owned_session:
            http.close()

    resolved_companies_output_path.parent.mkdir(parents=True, exist_ok=True)
    with resolved_companies_output_path.open("w", encoding="utf-8-sig", newline="") as comp_file:
        writer = csv.DictWriter(comp_file, fieldnames=COMPANIES_OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(unmatched_companies)
    LOGGER.info(
        "unmatched_companies=%d output=%s",
        len(unmatched_companies),
        resolved_companies_output_path,
    )

    if successful_boards == 0:
        raise RuntimeError(
            "No Greenhouse boards returned successfully; output was not written. "
            "Check that the board tokens are valid and try again."
        )

    # Deduplicate jobs by id while preserving order
    deduped_jobs: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for job in matching_jobs:
        job_id = job.get("id") or job.get("url") or ""
        if job_id and job_id in seen_ids:
            continue
        if job_id:
            seen_ids.add(job_id)
        deduped_jobs.append(job)
    matching_jobs = deduped_jobs

    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    with resolved_output_path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(matching_jobs)
    LOGGER.info(
        "roles=%s total_kept=%d output=%s",
        keywords,
        len(matching_jobs),
        resolved_output_path,
    )
    return matching_jobs


scrape_jobs = scrape_role


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Greenhouse jobs by role.")
    parser.add_argument(
        "--role",
        required=False,
        help="Role key or title keyword (defaults to roles listed in search.txt)",
    )
    parser.add_argument(
        "--companies",
        required=False,
        type=Path,
        help="TXT, YAML, or JSON file containing Greenhouse board tokens (defaults to search.txt)",
    )
    parser.add_argument("--refresh", action="store_true", help="Refresh raw response caches")
    parser.add_argument("--limit-companies", type=int, help="Test with the first N companies")
    parser.add_argument(
        "--output",
        required=False,
        type=Path,
        help="Output CSV path (defaults to data/jobs.csv)",
    )
    parser.add_argument(
        "--companies-output",
        required=False,
        type=Path,
        help="Output CSV path for unmatched companies (defaults to data/companies.csv)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    tokens_path = resolve_search_path(args.companies)
    if not tokens_path.exists():
        parser.error(
            f"Search file not found at {tokens_path}. Please create search.txt or pass --companies."
        )

    roles_to_scrape: list[str] = []
    if args.role:
        roles_to_scrape = [args.role]
    else:
        try:
            roles_to_scrape = load_roles(tokens_path)
        except Exception as error:
            parser.error(str(error))
        if not roles_to_scrape:
            parser.error(
                f"No --role specified and no roles found under #roles in {tokens_path}. "
                "Specify --role or add a #roles section to search.txt."
            )

    try:
        LOGGER.info("Scraping for role(s): %s", ", ".join(roles_to_scrape))
        scrape_role(
            roles_to_scrape,
            refresh=args.refresh,
            limit_companies=args.limit_companies,
            companies_path=tokens_path,
            output_path=args.output,
            companies_output_path=args.companies_output,
        )
    except (ValueError, RuntimeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()