import json
import re
import sys
from pathlib import Path

RESULT_DIR = Path(sys.argv[1]).resolve()
RESULT = json.loads((RESULT_DIR / "result.json").read_text(encoding="utf-8"))
SESSION = (RESULT_DIR / "session").resolve()
ANSWER = str(RESULT.get("final_answer") or "")


def find_file(name):
    for base in (SESSION / "deliverables", SESSION / "results", SESSION):
        cand = base / name
        if cand.is_file():
            return cand
    hits = sorted(p for p in SESSION.rglob(name) if p.is_file())
    return hits[0] if hits else None


def uploads():
    udir = SESSION / "uploads"
    assert udir.is_dir(), "uploads directory missing in session"
    return udir


def num_in_text(text, value, nd=2):
    compact = str(text).replace(",", "")
    target = round(float(value), nd)
    forms = {f"{target:.{nd}f}"}
    if float(target).is_integer():
        forms.add(str(int(target)))
    return any(form in compact for form in forms)


def close2(a, b, nd=2):
    return abs(round(float(a), nd) - round(float(b), nd)) <= 1e-6


assert RESULT.get("ok_run"), f"driver run failed: {RESULT.get('error')}"

records = {}
cur = None
for ln in (uploads() / "sequences.fasta").read_text(encoding="utf-8").splitlines():
    if ln.startswith(">"):
        cur = ln[1:].split()[0]
        records[cur] = []
    else:
        records[cur].append(ln.strip())
seqs = {k: "".join(v) for k, v in records.items()}
longest = max(seqs, key=lambda k: len(seqs[k]))
def gc(s):
    return (s.count("G") + s.count("C")) / len(s)
gc_top = max(seqs, key=lambda k: gc(seqs[k]))

assert num_in_text(ANSWER, len(seqs), 0), f"seq count {len(seqs)} missing"
assert longest in ANSWER, f"longest id {longest} missing"
assert num_in_text(ANSWER, len(seqs[longest]), 0), f"longest len {len(seqs[longest])} missing"
assert gc_top in ANSWER, f"gc-top id {gc_top} missing"
