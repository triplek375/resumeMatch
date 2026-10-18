# Resume Matching Core

Stage 1 provides configuration, job loading, dense embedding interfaces, fake sample data, and core tests. The sample jobs and resume are fictional test inputs; they are not real postings or candidate data.

## PowerShell setup

Run these commands from the project root with Python 3.10 or newer:

```powershell
py -3.10 -m venv .\dataProcessing\.venv
.\dataProcessing\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r .\dataProcessing\requirements.txt
```

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that PowerShell session, then activate the environment again.

## Stage 1 tests

```powershell
python -m pytest .\dataProcessing\tests -q
```

The tests use `FakeEmbedder` and do not download the BGE-M3 model. Later stages will add the job-vector cache and the CLI commands for these sample inputs:

```powershell
python -m dataProcessing.embed_jobs --jobs .\dataProcessing\samples\sample_jobs.json --limit 3
python -m dataProcessing.match --resume .\dataProcessing\samples\sample_resume.txt --top 5
```