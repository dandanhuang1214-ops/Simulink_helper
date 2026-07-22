from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.evaluation_refs import resolve_gold_evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Resolve stable evaluation Gold refs against the runtime database.")
    parser.add_argument("--set", type=Path, default=Path("docs/evaluation/EVAL_REGRESSION_V3_1.json"))
    args = parser.parse_args()
    payload = json.loads(args.set.read_text(encoding="utf-8"))
    rows = [resolve_gold_evidence(case) for case in payload["cases"]]
    print(json.dumps({
        "cases_resolved": len(rows),
        "gold_references": sum(len(row) for row in rows),
        "unique_ids": len({item for row in rows for item in row}),
    }, ensure_ascii=False, indent=2))
