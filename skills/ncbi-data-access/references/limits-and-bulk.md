# Limits and bulk work

## What the server enforces

- NCBI allows 3 requests per second per IP without an API key (10 with a key). This server keeps all
  users together under about 2.5 per second (8 with a key) so that colleagues on the same network are
  not blocked.
- Each chat session may have at most 2 NCBI requests in flight; extra calls wait their turn.
- Rate-limit and server errors are retried up to 4 times with backoff, honouring `Retry-After`.
- `sequence_fetch` downloads in batches of 200 ids and caches versioned accessions for 24 hours, so
  repeating a download of `NC_001416.1` costs nothing.
- `sequence_fetch` responses are capped at 10 MB by default (raise `max_bytes` for large GenBank
  sets); `url_fetch` at 50 MB.

## Rough sizing

| Job | Calls | Fine in chat? |
|-----|-------|---------------|
| Summaries of 200 accessions | 1 | yes |
| FASTA of 500 phage genomes | 3 | yes |
| GenBank of 500 phage genomes | 3, about 50 to 150 MB | split into batches with `max_bytes` raised |
| Search paging through 5,000 hits | 50 | tighten the query first |
| All RefSeq bacterial assemblies | thousands of files | no: offline bulk job |

## Bulk jobs

For whole taxa, thousands of genomes, SRA reads or nt/nr-scale data, tell the user the query and
`count`, estimate the size, and recommend an offline download with NCBI Datasets
(`datasets download genome taxon ...`) run outside chat, ideally on weekends or between 21:00 and
05:00 US Eastern time as NCBI asks. Disk space on the shared server is limited, so ask before
planning anything above a few GB.
