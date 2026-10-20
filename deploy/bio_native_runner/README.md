# Native bio_tools runner

Runs bio_tools on the host's own software (environment modules, conda) through Slurm instead of in
Docker containers. The app container hands each run to this process through the shared runtime
directory; see `tool_box/bio_tools/native_exec.py` for the trust model.

```
app container (uid 1001)                    host (this runner, a normal user)
  native_client.run_native_bio_tool           native_runner.py
    writes  <runtime>/_native/runs/<id>/request.json  ──►  validates, renders the command from
    polls   <runtime>/_native/runs/<id>/status.json   ◄──  tools_config.json, sbatch, polls squeue/sacct
    reads   <runtime>/_native/runs/<id>/work/<output>      job runs in <id>/work with `module load ...`
```

The runner executes only operations whose tool has a `native` block in `tools_config.json`, with
parameters checked against the operation's declared parameters, and reads only below `--read-root`
directories. Nothing in a request is ever run as a command.

## Install (620b)

```sh
install -d /data1/gagent/bio-runner /data1/gagent/logs
cp tool_box/bio_tools/native_runner.py tool_box/bio_tools/native_exec.py tool_box/bio_tools/tools_config.json \
   /data1/gagent/bio-runner/
mkdir -p ~/.config/systemd/user
cp deploy/bio_native_runner/gagent-bio-runner.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now gagent-bio-runner
loginctl show-user "$USER" | grep Linger     # must be Linger=yes so it survives logout and reboot
```

The runner re-reads `tools_config.json` when it changes; replace `native_runner.py` / `native_exec.py`
and `systemctl --user restart gagent-bio-runner` to update the code.

## App side

`BIO_TOOLS_EXECUTION_MODE=native_first` in `deploy/.env`: use the runner for operations that have a
native implementation, Docker for the rest, and Docker as the fallback only when no runner is
running. `native` never falls back. A native run that started and failed is reported as failed.

`HOST_RUNTIME_ROOT` (already set by the compose file) tells the app where `/app/runtime` is on the
host; `GAGENT_REFDATA_DIR` does the same for `/data`.

## Adding a tool

Add a `native` block to the tool in `tools_config.json`:

```json
"native": {"modules": ["BLAST+/2.12.0"], "cpus": 4, "mem_mb": 16384}
```

Optional keys: `conda_env` (a name under `--conda-envs-dir`; needs `--conda-base`), `path_tools`
(programs on the system PATH; checked before the command runs), `max_cpus`, `time_limit_seconds`, `partition`, `database: {"env": ..., "subdir": ...}` for
tools whose command uses `/work/database`. Command templates are shared with the Docker path:
`/work/{output}` becomes the run's work directory and `/work/database` the configured database.
An operation may set `native_command` to replace `command` on the host; use it to pass the CPU
allocation (`$GAGENT_CPUS`, also exported as `OMP_NUM_THREADS`) to tools that default to every core.
Set an operation's `native` to `false` to keep it on Docker.

## Conda environments (620b)

One environment per tool, named after the tool, built by `mkenvs.sh` (conda-forge + bioconda only,
`--override-channels`, so the Anaconda `defaults` terms of service are not involved):

```sh
CONDA_ENVS_PATH=/data1/gagent/conda/envs CONDA_PKGS_DIRS=/data1/gagent/conda/pkgs \
  conda create -y -n seqkit --override-channels -c conda-forge -c bioconda seqkit=2.8.0
```

Databases for the `database: {"subdir": ...}` tools live under `/data1/gagent/databases/<subdir>`; a
tool whose directory is empty answers `database_unavailable`.

## Reference databases (620b)

`mkdbs.sh <name>` installs one database under `/data1/gagent/databases/<name>` (run it with `sbatch`).
Installed: genomad, checkv, checkm, bakta (light), virsorter2, vibrant, phabox2, gtdbtk (release 214, 82 GB, the
release GTDB-Tk 2.3.2 is built for) and iphop (Aug23_rw, matching iPHoP 1.3.x; the "latest" database needs 1.4).
Tools whose database is missing answer `database_unavailable`. `mkdbs.sh` downloads in parallel parts (the servers give
about 5 MB/s per connection) and `KEEP_STAGE=1` resumes an interrupted run. Submit it with `sbatch --export=KEEP_STAGE=1`
or plain `sbatch`; do not use `--export=ALL` from a shell that loaded modules (it breaks curl's libraries) and give
iphop at least 32 GB of memory (8 GB was OOM-killed while unpacking).

## Environment fixes that are not in the package defaults

- `genomad`: needs `tf-keras` and `TF_USE_LEGACY_KERAS=1` (Keras 3 rejects its models). The variable is stored in the
  environment (`conda env config vars set -n genomad TF_USE_LEGACY_KERAS=1`).
- `virsorter2`: build it together with its own dependencies, because the runner uses `--use-conda-off` and
  `virsorter setup --skip-deps-install` (its bundled mamba is broken):
  `virsorter=2.2.4 last ncbi-genome-download ruamel.yaml prodigal=2.6 screed=1 "hmmer!=3.3.1" scikit-learn=0.22.1
  imbalanced-learn pandas=1.2 seaborn "numpy<1.24" python=3.8`.
- `vibrant`: exits 0 even when it fails, so its native command also checks that the output folder is not empty.
- `checkm`: reads its data from `CHECKM_DATA_PATH` (set by the native command).

## What the model is offered

- The runner writes `capabilities` into `runner.json` every minute: for each native operation, `null` (can run) or why
  not (`environment_not_installed`, `program_not_installed`, `database_unavailable`).
- With `BIO_TOOLS_EXECUTION_MODE=native` the app lists and describes only operations that can run, names the rest under
  `unavailable_tools` / `unavailable_operations`, and builds the tool schema and DeepThink catalog from the native
  tools only. This is read at app start, so changing the mode needs an app restart.
- A tool with `"native_only": true` (phabox2: host software, no Docker image) is offered only in `native` and
  `native_first`.
- phabox2 lives in `/data1/gagent/conda/envs/phabox2` (a clone of the `phabox_env` environment) with its database in
  `/data1/gagent/databases/phabox2` (a copy of `~/phabox_database/phabox_db_v2_1`).
