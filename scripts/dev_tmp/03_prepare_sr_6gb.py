#!/usr/bin/env python3
import csv
import os
import shutil
import subprocess
from pathlib import Path

BASE = Path('/home/zczhao/GAgent/data/experiment_nature')
NEW = BASE / 'experiment_A_v2'

MANIFEST_IN = NEW / 'manifests' / 'experiment1_manifest_lr6gb.csv'
MANIFEST_OUT = NEW / 'manifests' / 'experiment1_manifest_ready.csv'
OUT_DIR = NEW / 'ena' / 'sr_6gb'
STATS_CACHE = NEW / 'manifests' / 'sr_fastq_base_stats.tsv'
TARGET_BASES = 6_000_000_000
SEED = 100

OUT_DIR.mkdir(parents=True, exist_ok=True)
(NEW / 'logs' / 'sr_downsample').mkdir(parents=True, exist_ok=True)


def run_cmd(cmd, capture=False):
    print('+', ' '.join(cmd))
    if capture:
        p = subprocess.run(cmd, check=True, text=True, capture_output=True)
        return p.stdout.strip()
    subprocess.run(cmd, check=True)
    return ''


def symlink_or_copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.symlink(src, dst)
    except OSError:
        shutil.copy2(src, dst)


stats = {}
if STATS_CACHE.exists():
    for row in csv.DictReader(open(STATS_CACHE)):
        stats[row['file_path']] = int(row['base_count'])


def get_fastq_bases(path: Path) -> int:
    rp = path.resolve()
    key = str(rp)
    if key in stats:
        return stats[key]

    awk_prog = '$1=="ALL" {print $2}'
    cmd = [
        'docker', 'run', '--rm',
        '-v', f'{rp.parent}:/input:ro',
        'staphb/seqtk:latest',
        'sh', '-lc',
        f"seqtk fqchk /input/{rp.name} | awk '{awk_prog}'",
    ]
    out = run_cmd(cmd, capture=True)
    try:
        bases = int(out.splitlines()[-1].strip())
    except Exception as e:
        raise RuntimeError(f'Failed to parse fqchk output for {rp}: {out}') from e

    stats[key] = bases
    return bases


def downsample_fastq(src: Path, dst: Path, frac: float):
    rp = src.resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        'docker', 'run', '--rm', '--user', '1001:1000',
        '-v', f'{rp.parent}:/input:ro',
        '-v', f'{dst.parent}:/output',
        'staphb/seqtk:latest',
        'sh', '-lc',
        f'seqtk sample -s{SEED} /input/{rp.name} {frac:.8f} | gzip -1 > /output/{dst.name}',
    ]
    run_cmd(cmd)


rows = list(csv.DictReader(open(MANIFEST_IN)))
if not rows:
    raise RuntimeError(f'No rows in {MANIFEST_IN}')

for row in rows:
    sample = row['sample_id']
    r1 = Path(row['sr_r1'])
    r2 = Path(row['sr_r2'])
    se = Path(row['sr_se']) if row.get('sr_se') else None

    if not r1.exists() or not r2.exists():
        raise FileNotFoundError(f'Missing SR reads for {sample}: {r1}, {r2}')

    b1 = get_fastq_bases(r1)
    b2 = get_fastq_bases(r2)
    bse = get_fastq_bases(se) if se and se.exists() else 0
    total_bases = b1 + b2 + bse
    frac = min(1.0, TARGET_BASES / total_bases) if total_bases > 0 else 1.0

    out_r1 = OUT_DIR / f'{sample}_R1.fastq.gz'
    out_r2 = OUT_DIR / f'{sample}_R2.fastq.gz'
    out_se = OUT_DIR / f'{sample}_SE.fastq.gz' if se and se.exists() else None

    if out_r1.exists() and out_r1.stat().st_size > 0 and out_r2.exists() and out_r2.stat().st_size > 0:
        pass
    elif frac >= 0.999999:
        symlink_or_copy(r1.resolve(), out_r1)
        symlink_or_copy(r2.resolve(), out_r2)
        if out_se and se:
            symlink_or_copy(se.resolve(), out_se)
    else:
        downsample_fastq(r1, out_r1, frac)
        downsample_fastq(r2, out_r2, frac)
        if out_se and se:
            downsample_fastq(se, out_se, frac)

    row['sr_total_bases'] = str(total_bases)
    row['sr_fraction'] = f'{frac:.8f}'
    row['sr_6gb_r1'] = str(out_r1)
    row['sr_6gb_r2'] = str(out_r2)
    row['sr_6gb_se'] = str(out_se) if out_se else ''

with open(STATS_CACHE, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=['file_path', 'base_count'])
    w.writeheader()
    for k in sorted(stats.keys()):
        w.writerow({'file_path': k, 'base_count': stats[k]})

fieldnames = list(rows[0].keys())
with open(MANIFEST_OUT, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)

print(f'Wrote final manifest: {MANIFEST_OUT}')
print(f'Samples processed: {len(rows)}')
