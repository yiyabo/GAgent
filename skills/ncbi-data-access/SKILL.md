---
name: ncbi-data-access
description: "Find and download NCBI data with the built-in tools: Entrez searches (nuccore, protein, assembly, taxonomy, gene, SRA), accession summaries, taxon lineages, FASTA/GenBank/CDS downloads and assembly files. Covers query syntax that actually matches, the shared rate limit, batching and bulk-download limits."
---

# NCBI Data Access

Use this skill when a task needs records from NCBI: accessions, genomes, proteins, assemblies,
taxonomy, genes, BioProjects/BioSamples or SRA runs.

## Network facts (read first)

- Reach NCBI **only through the tools below**, never with `urllib`, `requests`, Biopython `Entrez`
  or `curl` from code. Every NCBI call from every user of this server shares **one rate limit**
  (about 2.5 requests per second); the tools queue and retry within it, direct calls skip it and can
  get the server's address blocked for everyone. In code mode, call the tools through the tool
  interface instead.
- The `datasets` CLI and EDirect are not installed.
- Your job is to make few, large calls.

## Tool map

| Need | Tool | Call |
|------|------|------|
| Find records by query | `ncbi_search` | `action="search"`, `db`, `term`, `retmax` (≤100), `retstart` |
| Metadata for known ids | `ncbi_search` | `action="summary"`, `db`, `ids` (≤200, accessions or UIDs; `GCF_`/`GCA_` for assembly) |
| Lineage / rank of a taxon | `ncbi_search` | `action="taxonomy"`, `term` (name) or `ids` (taxids, ≤5) |
| Sequences | `sequence_fetch` | `accessions=[...]`, `format="fasta"` |
| Annotation (features, products) | `sequence_fetch` | `format="genbank"` (`database="protein"` for protein records) |
| A genome's proteome / gene set | `sequence_fetch` | `format="fasta_cds_aa"` or `"fasta_cds_na"` (nuccore only) |
| Assembly files | `url_fetch` | `{ftp_refseq}/{last path part}_genomic.fna.gz` (also `_protein.faa.gz`, `_genomic.gff.gz`) |
| Papers / PubMed | `literature_pipeline` | not `ncbi_search` |

Then analyse downloaded files with `bio_tools` or code.

## Workflow

1. **Search narrowly first.** Start with `retmax` 20 and read `count`, `query_translation` and
   `notes`. If `count` is 0, fix the query (see below) instead of guessing accessions.
2. **Page only if needed.** Use `next_retstart` to page. If `count` is in the thousands, tighten the
   query (RefSeq only, length range, complete genomes) before paging.
3. **Batch.** Pass all accessions in one `summary` or `sequence_fetch` call. Never loop one call per
   accession.
4. **Download the right format.** Need gene products or coordinates: `genbank`. Need proteins for
   HMM/BLAST: `fasta_cds_aa`. Just the sequence: `fasta`.
5. **Record provenance.** Report accession.version, the exact query string, `count`, and the date.

## Query rules that matter

- `[orgn]` needs a real taxon name. NCBI now uses ICTV binomials for phages, so
  `Klebsiella phage[orgn]` returns 0. Put host or common names in `[title]`:
  `Klebsiella phage[title] AND complete genome[title] AND srcdb_refseq[prop]`.
- `Viruses[orgn] AND Klebsiella[title]` restricts to viruses with a host word in the title.
- There is no `[host]` field in nuccore.
- Useful filters: `srcdb_refseq[prop]` (RefSeq only), `30000:60000[slen]` (length),
  `"complete genome"[Assembly Level]` and `latest[filter]` (assembly db),
  `Caudoviricetes[subtree] AND family[rank]` (taxonomy db).
- When unsure of a current name, run `action="taxonomy"` with the old name and read
  `other_names` and `lineage`.

More patterns: `references/entrez-query-cookbook.md`.

## Errors

- `ncbi_rate_limited`: NCBI is busy after retries. Wait, then retry once with fewer, larger calls.
  Tell the user if it persists; do not hammer.
- `no_cds_features` (CDS formats): the record has no annotated CDS. Use `genbank` to confirm, or
  predict genes with `bio_tools` (prodigal) on the FASTA.
- GenBank and CDS downloads have no ENA/UniProt fallback; only `fasta` falls back.
- `missing` in a summary lists ids NCBI did not return (typo, withdrawn, wrong `db`).

## Scale limits

- Tens to a few hundred records: fine with these tools.
- Thousands of genomes, whole taxa or nt/nr-scale work: do not attempt in chat. Explain the scale,
  give the query and `count`, and suggest an offline bulk job (NCBI Datasets, off-peak hours). See
  `references/limits-and-bulk.md`.

## Reporting

- Distinguish NCBI metadata (title, organism, length) from your own analysis.
- Prefer RefSeq (`NC_`, `NZ_`, `GCF_`) for reference sets and say when you used GenBank-only records.
- Never invent accessions, counts or lineages; quote what the tools returned.
