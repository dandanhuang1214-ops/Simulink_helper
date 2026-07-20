from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.services.evidence_snippets import build_compact_context
from app.services.retrieval_pipeline import retrieve_evidence_with_coverage


async def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect selected and compact prompt evidence for frozen cases.")
    parser.add_argument("--set", type=Path, default=Path("/app/docs/evaluation/EVAL_REGRESSION_V2.json"))
    parser.add_argument("--only", required=True)
    parser.add_argument("--profile", choices=["fast", "full"], default="fast")
    args = parser.parse_args()

    wanted = {value.strip().upper() for value in args.only.split(",") if value.strip()}
    cases = json.loads(args.set.read_text(encoding="utf-8"))["cases"]
    for case in cases:
        if case["id"].upper() not in wanted:
            continue
        trace: dict = {}
        result = await retrieve_evidence_with_coverage(
            case["question"],
            use_rewrite=False,
            use_rerank=True,
            retrieval_profile=args.profile,
            trace=trace,
        )
        context, compact, role = build_compact_context(case["question"], result.evidence, trace=trace)
        print(json.dumps({
            "id": case["id"],
            "question": case["question"],
            "role": role,
            "selected": [
                {
                    "id": item.get("chunk_id"),
                    "page": item.get("page"),
                    "heading": item.get("heading_path"),
                    "score": item.get("selector_score"),
                }
                for item in result.evidence
            ],
            "compact": [
                {
                    "id": item.get("chunk_id"),
                    "heading": item.get("heading_path"),
                    "snippet": item.get("prompt_snippet"),
                }
                for item in compact
            ],
            "context": context,
            "trace_keys": list(trace),
            "selection_trace": trace.get("evidence_selector"),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
