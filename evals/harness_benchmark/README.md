# Harness Benchmark: pi vs qwen-code

Measures the two nested-delegation CLI backends on identical small tasks
(the kind code_executor delegates): file manipulation, bug fixing, data
extraction. Each harness gets the same workspace and the same fixed
instruction ("read task_prompt.md and follow it"); success is decided by a
per-task checker, and wall-clock seconds + reported tokens are recorded.

## Run (on .8, inside the runtime container)

```sh
# image: gagent-qwen-code-runtime:pi-0.85 (Node 22 + pi 0.85.1 + qwen-code)
docker run --rm -v /data/phage-agent:/app -w /app \
  gagent-qwen-code-runtime:pi-0.85 \
  sh -c "set -a; . /app/.env; set +a; unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy; \
         python3 evals/harness_benchmark/runner.py --harness pi --out /app/data/tools/bench_pi.json"
# then the same with --harness qwen --out /app/data/tools/bench_qwen.json
```

## Tasks

| task | skill |
|---|---|
| t1_csv_count | read csv, count rows, write result |
| t2_json_extract | filter+sort json fields |
| t3_fix_bug | fix off-by-one so provided test passes |
| t4_name_swap | text transform |
| t5_sum_log | parse log ignoring comments, float sum |
| t6_dedup | dedupe+sort |
| t7_md_titles | multi-file aggregation |
| t8_top_words | frequency analysis |

Add tasks as `tasks/tN_name/{task_prompt.md, fixtures/, check.py}` — the
runner picks up any directory containing `task_prompt.md`.
