from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.database import initialize_database
from app.services.acceptance import run_acceptance_after_job
from app.services.storage import ensure_storage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run acceptance questions after an import job completes.")
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--title", default="验收 - Stateflow User Guide")
    parser.add_argument("--question-file", default="/app/knowledge/evaluations/acceptance_questions_v1.md")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--poll-seconds", type=int, default=30)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    initialize_database()
    ensure_storage()
    result = await run_acceptance_after_job(
        args.job_id,
        title=args.title,
        question_file=Path(args.question_file),
        limit=args.limit,
        poll_seconds=args.poll_seconds,
    )
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
