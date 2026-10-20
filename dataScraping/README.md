# Greenhouse Job Scraper

Supply the Greenhouse board tokens you want searched; this project does not bake in a fixed company list. Greenhouse's public API requires a board token for every company and does not expose a global all-company search endpoint. The scraper accepts a TXT file with one token per line, a YAML list, or a JSON list (or an object with a `companies` list). Tokens that return 404 are logged and skipped.

The scraper caches raw successful API responses as JSON in `data/raw/greenhouse/` before parsing. It reuses those files on later runs unless `--refresh` is supplied, filters titles matching target roles, and writes normalized results as CSV to `data/jobs.csv`.

For companies where the board was not found (HTTP 404) or where no matching roles were found, an entry is written to `data/companies.csv` with columns `company,reason`. The reason will be `NoMatchingCompany` or `NoMatchingJob`.

## PowerShell setup

Run from the project root with Python 3.10 or newer:

```powershell
py -3.10 -m venv .\dataScraping\.venv
.\dataScraping\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r .\dataScraping\requirements.txt
```

## Run

Run directly (reads target companies and roles from `search.txt` by default):

```powershell
python -m dataScraping.greenhouse
```

Optional flags to limit companies or refresh caches:

```powershell
python -m dataScraping.greenhouse --limit-companies 2
python -m dataScraping.greenhouse --refresh
```

You can still override the role or custom token file via arguments:

```powershell
python -m dataScraping.greenhouse --role data_engineer --companies .\search.txt
```

The first command is useful for a small initial run. Requests use a timeout, a descriptive configurable User-Agent, randomized 1-2 second spacing, and exponential backoff for rate limits and server errors.

## Tests

```powershell
python -m pytest .\dataScraping\tests -q
```

Tests use the saved fixture under `tests/fixtures/` and make no network requests.