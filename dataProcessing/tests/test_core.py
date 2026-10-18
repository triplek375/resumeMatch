import json

import numpy as np

from dataProcessing.embedders import FakeEmbedder
from dataProcessing.jobs import Job, build_job_text, load_jobs
from dataProcessing.matching import rank_jobs


def test_build_job_text_uses_content_fields_and_skips_empty_values():
    job = Job(
        id="job-1",
        title="Data Engineer",
        company="Example Co.",
        location="Remote",
        url="https://example.com/job",
        description="Build data pipelines.",
    )

    assert build_job_text(job) == (
        "Title: Data Engineer\n"
        "Company: Example Co.\n"
        "Location: Remote\n"
        "Description: Build data pipelines."
    )
    empty_description = Job(**{**job.__dict__, "description": ""})
    assert "Description:" not in build_job_text(empty_description)


def test_load_jobs_applies_field_map_and_generates_missing_id(tmp_path):
    jobs_path = tmp_path / "jobs.json"
    jobs_path.write_text(
        json.dumps(
            [
                {
                    "role_name": "Data Engineer",
                    "organization": "Example Co.",
                    "city": "Remote",
                    "posting": "https://example.com/job",
                    "details": "Build pipelines.",
                }
            ]
        ),
        encoding="utf-8",
    )
    field_map = {
        "title": "role_name",
        "company": "organization",
        "location": "city",
        "url": "posting",
        "description": "details",
    }

    job = load_jobs(jobs_path, field_map)[0]

    assert job.title == "Data Engineer"
    assert job.company == "Example Co."
    assert job.description == "Build pipelines."
    assert job.id.startswith("job-")
    assert job.id == load_jobs(jobs_path, field_map)[0].id


def test_fake_embedder_is_deterministic_and_normalized():
    embedder = FakeEmbedder(vector_dimension=12)

    first = embedder.embed(["resume text", "job text"])
    second = embedder.embed(["resume text", "job text"])

    assert first.shape == (2, 12)
    np.testing.assert_array_equal(first, second)
    np.testing.assert_allclose(np.linalg.norm(first, axis=1), 1.0)


def test_rank_jobs_returns_descending_similarity():
    jobs = [
        Job("first", "First", "Co", "", "", ""),
        Job("second", "Second", "Co", "", "", ""),
        Job("third", "Third", "Co", "", "", ""),
    ]
    resume_vector = np.array([1.0, 0.0], dtype=np.float32)
    job_vectors = np.array(
        [[0.0, 1.0], [1.0, 0.0], [0.8, 0.6]], dtype=np.float32
    )

    matches = rank_jobs(jobs, job_vectors, resume_vector, top_n=2)

    assert [match.job.id for match in matches] == ["second", "third"]
    np.testing.assert_allclose([match.score for match in matches], [1.0, 0.8])