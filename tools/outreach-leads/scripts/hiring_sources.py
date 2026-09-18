"""Merge independent hiring evidence without presenting old observations as fresh."""
from __future__ import annotations

from copy import deepcopy


def merge_hiring(profile: dict, row: dict) -> None:
    """Keep per-board snapshots/history; aggregate only boards checked by this row.

    Counts represent board listings, not unique vacancies. Same job on two boards
    intentionally remains two separately attributable records.
    """
    sources = deepcopy(profile.get("hiring_sources") or {})
    # Migrate a pre-multi-source profile without mislabelling aggregate records.
    prior_aggregate = profile.get("hiring") or {}
    if not sources and prior_aggregate:
        # The existing prep recrawl retains `hiring` but not new top-level keys.
        # Recover source snapshots from the aggregate's provenance and job rows.
        if isinstance(prior_aggregate.get("sources"), dict):
            for board, metadata in prior_aggregate["sources"].items():
                sources[board] = deepcopy(metadata)
                sources[board]["jobs"] = [deepcopy(j) for j in prior_aggregate.get("jobs", []) if j.get("source") == board]
        else:
            sources["linkedin"] = deepcopy(prior_aggregate)
    history = deepcopy(profile.get("hiring_last_successful") or prior_aggregate.get("source_history") or {})
    for board, previous in history.items():
        sources.setdefault(board, deepcopy(previous))
    updates = {}
    if row.get("hiring") and not row.get("linkedin_skipped"):
        updates["linkedin"] = row["hiring"]
    if isinstance(row.get("indeed"), dict):
        updates["indeed"] = row["indeed"]["hiring"]
    for board, current in updates.items():
        previous = sources.get(board) or {}
        if current.get("status") == "unknown" and previous.get("status") in {"hiring", "no_public_jobs_found"}:
            history[board] = deepcopy(previous)
        fresh = deepcopy(current)
        previous_jobs = {j.get("job_id"): j for j in previous.get("jobs", [])}
        for job in fresh.get("jobs", []):
            old = previous_jobs.get(job.get("job_id")) or {}
            if not job.get("description"):
                if old.get("description"):
                    job["last_successful_details"] = {k: deepcopy(v) for k, v in old.items() if k not in {"last_successful_details", "detail_checks"}}
                elif old.get("last_successful_details"):
                    job["last_successful_details"] = deepcopy(old["last_successful_details"])
        sources[board] = fresh
        updates[board] = fresh
    if not updates:
        return
    for board, previous in sources.items():
        if board not in updates and previous.get("status") in {"hiring", "no_public_jobs_found"}:
            history[board] = deepcopy(previous)
    profile["hiring_sources"] = sources
    if history:
        profile["hiring_last_successful"] = history
        if "linkedin" in history:
            profile["linkedin_last_successful_hiring"] = deepcopy(history["linkedin"])
    jobs, seen = [], set()
    for board, current in updates.items():
        for raw in current.get("jobs", []):
            key = (board, raw.get("job_id") or raw.get("url"))
            if key in seen:
                continue
            seen.add(key)
            job = deepcopy(raw)
            job.update(source=board, source_job_key=board + ":" + str(key[1]))
            jobs.append(job)
    hiring = any(s.get("is_hiring") is True for s in updates.values())
    empty = all(s.get("status") == "no_public_jobs_found" for s in updates.values())
    profile["hiring"] = {
        "status": "hiring" if hiring else "no_public_jobs_found" if empty else "unknown",
        "is_hiring": True if hiring else None,
        "observed_job_count": len(jobs), "jobs": jobs,
        "checked_at": row.get("checked_at"), "complete": False,
        "coverage": "public_listings_by_source", "distinct_vacancy_count": None,
        "deduplication": "source_and_job_id", "checked_sources": list(updates),
        "not_checked_sources": [b for b in sources if b not in updates],
        "stop_reason": "; ".join(b + ":" + str(s.get("stop_reason", "unknown")) for b, s in updates.items()),
        "source_urls": list(dict.fromkeys(u for s in updates.values() for u in s.get("source_urls", []))),
        "sources": {b: {k: deepcopy(v) for k, v in s.items() if k != "jobs"} for b, s in updates.items()},
        "source_history": deepcopy(history),
    }
