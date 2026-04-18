#!/usr/bin/env python3
import csv
import os
import shutil
import subprocess
from pathlib import Path

BASE = Path('/home/zczhao/GAgent/data/experiment_nature')
OLD = BASE / 'experiment_A'
NEW = BASE / 'experiment_A_v2'

MANIFEST_PRIMARY = NEW / 'manifests' / 'experiment1_manifest_expanded.csv'
MANIFEST_FALLBACK = NEW / 'manifests' / 'experiment1_manifest.csv'
MANIFEST_OUT = NEW / 'manifests' / 'experiment1_manifest_lr6gb.csv'
OUT_DIR = NEW / 'ena' / 'ont_6gb'
OLD_SUB_DIR = OLD / 'ena' / 'PRJEB88320' / 'ont' / 'subsample'

OUT_DIR.mkdir(parents=True, exist_ok=True)
(NEW / 'logs' / 'lr_downsample').mkdir(parents=True, exist_ok=True)

# Reuse fractions from the previous validated ONT downsampling script.
FRACTIONS = {
    'ERR14838501': 0.27,
    'ERR14838502': 1.00,
    'ERR14838503': 1.00,
    'ERR14838504': 0.19,
    'ERR14838505': 0.17,
    'ERR14838506': 0.19,
    'ERR14838507': 0.19,
    'ERR14838508': 0.21,
    'ERR14838509': 0.17,
    'ERR14838510': 0.17,
    'ERR14838511': 0.22,
    'ERR14838512': 0.38,
}


def run_cmd(cmd):
    print('+', ' '.join(cmd))
    subprocess.run(cmd, check=True)


def symlink_or_copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.symlink(src, dst)
    except OSError:
        shutil.copy2(src, dst)


manifest_in = MANIFEST_PRIMARY if MANIFEST_PRIMARY.exists() else MANIFEST_FALLBACK
if not manifest_in.exists():
    raise FileNotFoundError(f'Manifest not found: {MANIFEST_PRIMARY} or {MANIFEST_FALLBACK}')

rows = list(csv.DictReader(open(manifest_in)))
if not rows:
    raise RuntimeError(f'No rows in {manifest_in}')

for row in rows:
    sample_id = row['sample_id']
    ont_run = row['ont_run']
    src = Path(row.get('ont_fasta') or (OLD / 'ena' / 'PRJEB88320' / 'ont' / f'{ont_run}.nohuman.fasta'))
    dst = OUT_DIR / f'{sample_id}.fasta'
    frac = FRACTIONS.get(ont_run)

    if frac is None:
        raise KeyError(f'Missing downsample fraction for ONT run: {ont_run}')

    if not src.exists() or src.stat().st_size == 0:
        raise FileNotFoundError(f'Missing ONT source fasta for {sample_id}: {src}')

    if dst.exists() and dst.stat().st_size > 0:
        row['ont_6gb_fasta'] = str(dst)
        row['ont_6gb_method'] = 'existing'
        row['ont_6gb_fraction'] = str(frac)
        continue

    if frac >= 0.999:
        symlink_or_copy(src, dst)
        row['ont_6gb_method'] = 'full_input_symlink'
        row['ont_6gb_fraction'] = '1.0'
        row['ont_6gb_fasta'] = str(dst)
        continue

    old_sub = OLD_SUB_DIR / f'{ont_run}_6Gb.fasta'
    if old_sub.exists() and old_sub.stat().st_size > 0:
        symlink_or_copy(old_sub, dst)
        row['ont_6gb_method'] = 'reuse_old_subsample'
        row['ont_6gb_fraction'] = str(frac)
        row['ont_6gb_fasta'] = str(dst)
        continue

    run_cmd([
        'docker', 'run', '--rm', '--user', '1001:1000',
        '-v', f'{src.parent}:/input:ro',
        '-v', f'{OUT_DIR}:/output',
        'staphb/seqtk:latest',
        'sh', '-lc',
        f'seqtk sample /input/{src.name} {frac} > /output/{sample_id}.fasta',
    ])

    if not dst.exists() or dst.stat().st_size == 0:
        raise RuntimeError(f'Failed to generate ONT 6Gb fasta: {dst}')

    row['ont_6gb_method'] = 'seqtk_sample'
    row['ont_6gb_fraction'] = str(frac)
    row['ont_6gb_fasta'] = str(dst)

fieldnames = list(rows[0].keys())
with open(MANIFEST_OUT, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)

print(f'Input manifest: {manifest_in}')
print(f'Wrote LR6Gb manifest: {MANIFEST_OUT}')
print(f'Samples processed: {len(rows)}')
