"""Clone job (step 2.B.2) that also runs filter + profiler after a successful clone.

A failed clone sets `clone_failed` and raises, so the worker marks the job
`failed` and analysis (filter/profile) does not proceed.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.intelligence.repository.ast import extract_repository
from app.intelligence.repository.chunker import chunk_repository
from app.intelligence.repository.clone import clone_repository
from app.intelligence.repository.filter import filter_repository
from app.intelligence.repository.graph import write_graph
from app.intelligence.repository.profiler import profile_repository
from app.services.vectors import index_code_chunks
from app.jobs.registry import ProgressReporter, register
from app.models.job import Job
from app.models.repository import Repository


def repository_clone(job: Job, db: Session, report_progress: ProgressReporter) -> None:
    repository = db.scalar(
        select(Repository).where(Repository.project_id == job.project_id)
    )
    if repository is None:
        raise RuntimeError("no repository attached to this project")

    settings = get_settings()
    report_progress("Cloning", 10, f"Cloning {repository.url}")
    dest = clone_repository(db, repository, settings)

    report_progress("Filtering", 55, "Filtering files")
    filtered = filter_repository(dest)
    report_progress(
        "Filtering",
        70,
        f"included={filtered.included_count} skipped={filtered.skipped_count}",
    )

    report_progress("Profiling", 80, "Profiling architecture")
    profile = profile_repository(dest)
    repository.architecture_profile = profile.model_dump()
    db.commit()

    report_progress("Extracting", 85, "Extracting symbols")
    extracted = extract_repository(dest, filtered.included_paths)
    report_progress(
        "Extracting",
        90,
        f"parsed={extracted.parsed_count} unparsed={extracted.unparsed_count}",
    )

    report_progress("Graphing", 88, "Writing code graph")
    write_graph(
        job.project_id,
        repository.id,
        extracted,
        commit_hash=repository.cloned_commit_hash,
        settings=settings,
    )

    report_progress("Chunking", 91, "Chunking source")
    chunks = chunk_repository(dest, extracted)

    report_progress("Embedding", 95, f"Indexing {len(chunks)} chunks")
    stats = index_code_chunks(
        job.project_id,
        repository.id,
        chunks,
        commit_hash=repository.cloned_commit_hash,
        settings=settings,
    )
    repository.last_indexed_commit = repository.cloned_commit_hash
    db.commit()
    report_progress(
        "Finishing",
        100,
        f"Repository cloned and profiled included={filtered.included_count} skipped={filtered.skipped_count} parsed={extracted.parsed_count} unparsed={extracted.unparsed_count} embedded={stats.embedded} reused={stats.reused}",
    )


register("repository_clone", repository_clone)
