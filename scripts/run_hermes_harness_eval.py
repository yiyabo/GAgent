#!/usr/bin/env python3
"""Prepare one pinned Hermes comparison trial; --execute explicitly runs it."""
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--hermes-root", required=True)
    parser.add_argument("--hermes-python", required=True)
    parser.add_argument("--case", default="table_clean")
    parser.add_argument("--model", default=os.getenv("HERMES_HARNESS_MODEL"))
    parser.add_argument("--base-url", default=os.getenv("HERMES_HARNESS_BASE_URL"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--config")
    args = parser.parse_args()
    if not args.model or not args.base_url:
        parser.error("explicit model and OpenAI-compatible base URL are required")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.services.harness_eval.config import EvalSuiteConfig
    from app.services.harness_eval.hermes_adapter import HermesRunConfig, dry_run_hermes_trial, run_hermes_trial
    cfg = EvalSuiteConfig(**(json.loads(Path(args.config).read_text()) if args.config else {})).validate()
    hermes = HermesRunConfig(args.hermes_root, args.hermes_python, args.base_url, args.model)
    root = Path(args.root).resolve()
    if args.execute:
        key = os.getenv("HERMES_HARNESS_API_KEY")
        if not key:
            parser.error("HERMES_HARNESS_API_KEY must be supplied in the process environment")
        report = run_hermes_trial(args.case, root, cfg, hermes, api_key=key)
        print(json.dumps({key: report[key] for key in ("production_status", "provider_attempts", "usage_source", "total_tokens")}, ensure_ascii=False))
    else:
        report = dry_run_hermes_trial(args.case, root, cfg, hermes)
        print(json.dumps({"prepared": str(root), "executed": False, "revision": report["installation"]["revision"]}))


if __name__ == "__main__":
    main()
