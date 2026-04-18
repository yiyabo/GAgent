#!/usr/bin/env python3
import csv
import os
import shutil
import subprocess
import time
from pathlib import Path

import requests

BASE = Path('/home/zczhao/GAgent/data/experiment_nature')
OLD = BASE / 'experiment_A'
NEW = BASE / 'experiment_A_v2'
MANIFEST = NEW / 'manifests' / 'experiment1_manifest.csv'

ONT_OLD_DIR = OLD / 'ena' / 'PRJEB88320' / 'ont'
SR_T2_OLD_DIR = OLD / 'ena' / 'PRJEB88320' / 'illumina'

ONT_NEW_DIR = NEW / 'ena' / 'ont_nohuman'
SR_NEW_DIR = NEW / 'ena' / 'sr_reads'
CACHE_DIR = NEW / 'ena' / 'download_cache'

ENA_API = 'https://www.ebi.ac.uk/ena/portal/api/filereport'

for d in [ONT_NEW_DIR, SR_NEW_DIR, CACHE_DIR, NEW / 'manifests']:
    d.mkdir(parents=True, exist_ok=True)


def run_cmd(cmd):
    print('+', ' '.join(cmd))
    subprocess.run(cmd, check=True)


def download_if_missing(url: str, out: Path, min_bytes: int = 1):
    if out.exists() and out.stat().st_size >= max(1, min_bytes):
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    target = max(1, min_bytes)
    last_err = None
    for attempt in range(1, 21):
        if out.exists() and out.stat().st_size >= target:
            return
        aria2 = shutil.which('aria2c')
        try:
            if aria2:
                run_cmd([aria2, '--all-proxy=', '-c', '-x', '8', '-s', '8', '-k', '1M', '-d', str(out.parent), '-o', out.name, url])
            else:
                run_cmd(['wget', '--no-proxy', '-q', '-c', '-O', str(out), url])
        except subprocess.CalledProcessError as e:
            last_err = e
            cur_size = out.stat().st_size if out.exists() else 0
            print(f'WARN: download attempt {attempt}/20 failed for {out.name}; size={cur_size} bytes')
            time.sleep(min(60, attempt * 3))
            continue
        if out.exists() and out.stat().st_size >= target:
            return
        cur_size = out.stat().st_size if out.exists() else 0
        print(f'WARN: download attempt {attempt}/20 incomplete for {out.name}; size={cur_size} expected>={target}')
        time.sleep(min(60, attempt * 3))
    if out.exists() and out.stat().st_size >= target:
        return
    raise RuntimeError(f'Download did not reach expected size for {out.name}: got {out.stat().st_size if out.exists() else 0}, expected >= {target}') from last_err


def symlink_or_copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.symlink(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def fetch_fastq_entries(run: str):
    params = {
        'accession': run,
        'result': 'read_run',
        'fields': 'run_accession,fastq_ftp,fastq_bytes,library_layout',
        'format': 'tsv',
    }
    resp = requests.get(ENA_API, params=params)
    resp.raise_for_status()
    lines = [x.strip() for x in resp.text.strip().splitlines() if x.strip()]
    if len(lines) < 2:
        raise RuntimeError(f'ENA API returned no run record for {run}')
    header = lines[0].split('\t')
    values = lines[1].split('\t')
    data = dict(zip(header, values))
    ftp_paths = [x for x in data.get('fastq_ftp', '').split(';') if x]
    byte_fields = [x for x in data.get('fastq_bytes', '').split(';') if x]
    if not ftp_paths:
        raise RuntimeError(f'ENA API has empty fastq_ftp for {run}')
    bytes_list = []
    for x in byte_fields:
        try:
            bytes_list.append(int(x))
        except ValueError:
            bytes_list.append(0)
    if len(bytes_list) < len(ftp_paths):
        bytes_list.extend([0] * (len(ftp_paths) - len(bytes_list)))

    entries = []
    for p, b in zip(ftp_paths, bytes_list):
        if p.startswith('ftp.sra.ebi.ac.uk/'):
            u = 'https://' + p
        elif p.startswith('ftp://'):
            u = p.replace('ftp://', 'https://', 1)
        elif p.startswith('http://') or p.startswith('https://'):
            u = p
        else:
            u = 'https://' + p
        entries.append((u, b))
    return entries


def choose_paired_urls(run: str):
    entries = fetch_fastq_entries(run)
    u1 = [(u, b) for u, b in entries if u.endswith('_1.fastq.gz')]
    u2 = [(u, b) for u, b in entries if u.endswith('_2.fastq.gz')]
    if not u1 or not u2:
        raise RuntimeError(f'Cannot find paired FASTQ URLs for {run}: {entries}')
    return u1[0], u2[0]


def choose_single_url(run: str):
    entries = fetch_fastq_entries(run)
    bare = [(u, b) for u, b in entries if u.endswith(f'/{run}.fastq.gz')]
    if bare:
        return bare[0]
    non_paired = [(u, b) for u, b in entries if not u.endswith('_1.fastq.gz') and not u.endswith('_2.fastq.gz')]
    if non_paired:
        return non_paired[0]
    raise RuntimeError(f'Cannot find single FASTQ URL for {run}: {entries}')


rows = list(csv.DictReader(open(MANIFEST)))
if not rows:
    raise RuntimeError(f'Empty manifest: {MANIFEST}')

expanded = []

for r in rows:
    sample_id = r['sample_id']
    ont_run = r['ont_run']
    sr_run = r['sr_paired_run']
    sr_unpaired = (r.get('sr_unpaired_run') or '').strip()
    sr_project = r['sr_project']

    ont_src = ONT_OLD_DIR / f'{ont_run}.nohuman.fasta'
    ont_dst = ONT_NEW_DIR / f'{sample_id}.fasta'
    if not ont_src.exists():
        raise FileNotFoundError(f'Missing ONT nohuman input: {ont_src}')
    symlink_or_copy(ont_src, ont_dst)

    if sr_project == 'PRJEB88320':
        r1_src = SR_T2_OLD_DIR / f'{sr_run}_1.fastq.gz'
        r2_src = SR_T2_OLD_DIR / f'{sr_run}_2.fastq.gz'
        if not r1_src.exists() or not r2_src.exists():
            raise FileNotFoundError(f'Missing PRJEB88320 paired files for {sample_id}: {sr_run}')
    else:
        r1_src = CACHE_DIR / f'{sr_run}_1.fastq.gz'
        r2_src = CACHE_DIR / f'{sr_run}_2.fastq.gz'
        (u1, b1), (u2, b2) = choose_paired_urls(sr_run)
        download_if_missing(u1, r1_src, b1)
        download_if_missing(u2, r2_src, b2)

    r1_dst = SR_NEW_DIR / f'{sample_id}_R1.fastq.gz'
    r2_dst = SR_NEW_DIR / f'{sample_id}_R2.fastq.gz'
    symlink_or_copy(r1_src, r1_dst)
    symlink_or_copy(r2_src, r2_dst)

    se_dst = ''
    if r.get('sr_use_unpaired', '').lower() == 'true' and sr_unpaired:
        if sr_project == 'PRJEB88320':
            se_src = SR_T2_OLD_DIR / f'{sr_unpaired}.fastq.gz'
            if not se_src.exists():
                se_src = CACHE_DIR / f'{sr_unpaired}.fastq.gz'
                u, b = choose_single_url(sr_unpaired)
                download_if_missing(u, se_src, b)
        else:
            se_src = CACHE_DIR / f'{sr_unpaired}.fastq.gz'
            u, b = choose_single_url(sr_unpaired)
            download_if_missing(u, se_src, b)
        se_dst_path = SR_NEW_DIR / f'{sample_id}_SE.fastq.gz'
        symlink_or_copy(se_src, se_dst_path)
        se_dst = str(se_dst_path)

    expanded.append({
        **r,
        'ont_fasta': str(ont_dst),
        'sr_r1': str(r1_dst),
        'sr_r2': str(r2_dst),
        'sr_se': se_dst,
    })

expanded_path = NEW / 'manifests' / 'experiment1_manifest_expanded.csv'
with open(expanded_path, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(expanded[0].keys()))
    w.writeheader()
    w.writerows(expanded)

print(f'Wrote expanded manifest: {expanded_path}')
print(f'Samples prepared: {len(expanded)}')
