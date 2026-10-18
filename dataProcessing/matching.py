"""Vector ranking helpers shared by the matching CLI and tests."""

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from dataProcessing.jobs import Job


@dataclass(frozen=True)
class Match:
    job: Job
    score: float


def rank_jobs(
    jobs: Sequence[Job], job_vectors: np.ndarray, resume_vector: np.ndarray, top_n: int
) -> list[Match]:
    """Rank normalized job vectors by dot-product similarity to the resume."""
    vectors = np.asarray(job_vectors)
    resume = np.asarray(resume_vector).reshape(-1)
    if vectors.ndim != 2 or vectors.shape[0] != len(jobs):
        raise ValueError("job_vectors must have one row per job.")
    if vectors.shape[1] != resume.size:
        raise ValueError("Resume and job vectors must have the same dimension.")
    if top_n < 0:
        raise ValueError("top_n cannot be negative.")

    scores = vectors @ resume
    order = np.argsort(-scores, kind="stable")[:top_n]
    return [Match(jobs[int(index)], float(scores[index])) for index in order]