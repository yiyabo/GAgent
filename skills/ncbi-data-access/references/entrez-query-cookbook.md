# Entrez query cookbook

Patterns checked against the live E-utilities. Always read `query_translation` in the result: it
shows how NCBI parsed the query, and `notes` repeats NCBI warnings such as ignored phrases.

## nuccore / protein

| Goal | Term |
|------|------|
| Complete RefSeq phage genomes for a host | `Klebsiella phage[title] AND complete genome[title] AND srcdb_refseq[prop]` |
| Viruses mentioning a host, length window | `Viruses[orgn] AND Klebsiella[title] AND 30000:60000[slen]` |
| One named phage | `Escherichia phage T7[orgn] AND complete genome[title]` |
| Proteins by product name in a taxon | `Caudoviricetes[orgn] AND endolysin[title]` (db `protein`) |
| Recently updated records | add `2024/01/01:3000[mdat]` |

- `[orgn]` matches taxonomy names only (`Escherichia phage T7` works because it is an existing name;
  generic phrases such as `Klebsiella phage` do not).
- `[title]` is a word match on the record definition line; quote phrases if needed.
- `srcdb_refseq[prop]` limits to RefSeq. `biomol_genomic[prop]` keeps genomic molecules.
- Accessions can be summarized directly with `action="summary"`; no search needed.

## assembly

| Goal | Term |
|------|------|
| Latest complete assemblies of a species | `Klebsiella pneumoniae[orgn] AND "complete genome"[Assembly Level] AND latest[filter]` |
| RefSeq reference genomes | add `"reference genome"[RefSeq Category]` |

The summary returns `ftp_refseq` / `ftp_genbank` as https directory links. File names inside are
`<last path part>_genomic.fna.gz`, `_protein.faa.gz`, `_genomic.gff.gz`, `_genomic.gbff.gz`.

## taxonomy

| Goal | Call |
|------|------|
| Lineage of a name | `action="taxonomy"`, `term="Lambdavirus lambda"` |
| Lineage of taxids | `action="taxonomy"`, `ids=["10710"]` |
| Families under a class | `action="search"`, `db="taxonomy"`, `term="Caudoviricetes[subtree] AND family[rank]"` |

Old phage names (for example "Enterobacteria phage lambda") often appear in `other_names`.

## gene / bioproject / biosample / sra

Generic summaries are returned. Typical terms: `lacZ[gene] AND Escherichia coli[orgn]` (gene). For
BioProject and SRA, search with the accession itself as the term (`PRJNA257197`, `SRR390728`). The
tools do not download reads; SRA data needs an offline job.
