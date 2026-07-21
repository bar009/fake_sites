"""Run one scan target in a disposable process controlled by ScanWorker."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fakeshop.db import Repository
from fakeshop.jobs import ScanWorker


def run(payload_path: Path) -> int:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    result_path = Path(payload["result_path"])
    result = {"ok": False, "error": "Target runner stopped before completion"}
    try:
        repository = Repository(Path(payload["db_path"]))
        worker = ScanWorker(
            repository, Path(payload["data_dir"]),
            isolate_targets=False, inter_target_delay=0,
        )
        worker._process_target(
            payload["scan"], payload["target"], Path(payload["screenshot_dir"]),
        )
        result = {"ok": True, "error": ""}
        return_code = 0
    except Exception as exc:  # parent records the error and continues the batch
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return_code = 1
    temporary = result_path.with_suffix(result_path.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    temporary.replace(result_path)
    return return_code


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m fakeshop.target_runner <payload.json>")
    raise SystemExit(run(Path(sys.argv[1])))


if __name__ == "__main__":
    main()
