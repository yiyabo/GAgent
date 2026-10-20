"""Deterministic synthetic inputs for evolution scenarios (standard library only).

Every generator takes an explicit seed, so a scenario always receives the same
bytes. Oracle values are derived from these inputs by the scenario builders in
scenarios.py; they are never written into the agent's session directory.
"""

import random

BASES = "ACGT"


def rng_for(seed):
    return random.Random(seed)


def rand_dna(rng, length):
    return "".join(rng.choice(BASES) for _ in range(length))


def wrap(seq, width=70):
    return "\n".join(seq[i:i + width] for i in range(0, len(seq), width))


def to_fasta(records):
    """records: iterable of (name, sequence)."""
    return "".join(f">{name}\n{wrap(seq)}\n" for name, seq in records)


def to_fastq(records):
    """records: iterable of (name, sequence, quality)."""
    return "".join(f"@{name}\n{seq}\n+\n{qual}\n" for name, seq, qual in records)


def parse_fasta(text):
    records = []
    name = None
    parts = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(">"):
            if name is not None:
                records.append((name, "".join(parts)))
            tokens = line[1:].split()
            name = tokens[0] if tokens else ""
            parts = []
        elif line:
            parts.append(line)
    if name is not None:
        records.append((name, "".join(parts)))
    return records


def n50(lengths):
    total = sum(lengths)
    running = 0
    for length in sorted(lengths, reverse=True):
        running += length
        if running * 2 >= total:
            return length
    return 0


def gc_percent(seq):
    seq = seq.upper()
    if not seq:
        return 0.0
    return 100.0 * sum(1 for base in seq if base in "GC") / len(seq)


def mutate(rng, seq, snps):
    chars = list(seq)
    for pos in rng.sample(range(len(chars)), snps):
        chars[pos] = rng.choice([base for base in BASES if base != chars[pos]])
    return "".join(chars)


def sample_reads(rng, ref, count, read_len, prefix="read"):
    """Error-free reads sampled from ref. Returns (name, sequence, quality)."""
    reads = []
    for i in range(count):
        start = rng.randint(0, len(ref) - read_len)
        reads.append((f"{prefix}{i + 1:05d}", ref[start:start + read_len], "I" * read_len))
    return reads
