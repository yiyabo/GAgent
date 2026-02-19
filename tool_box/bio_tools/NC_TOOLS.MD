
---

## 1) （QC / ）

* **Nextflow** (v22.10.5)： in-house /（）
* **HTStream SuperDeduper** (v1.3.3)：（deduplicate）
* **TrimGalore** (v0.6.7)：/
* **bwa** (v0.7.17)： hg38， **** （ reads ）
* **Dorado** (v0.5.3；“super-high accuracy” model v3.4.0)：ONT  POD5 **basecalling + demultiplex**  FASTQ
* **NanoPlot** (v1.41.6)：（）/
* **minimap2** (v2.26-r1175)： v38  **** 
* **Seqtk subseq** (v1.4-r130)： FASTQ （）

---

## 2) （MAGs）

* **MetaHIT（；）** (v1.2.9)：
* **metaFlye** (v2.9.2-b1786)：（ nano-hq， Q20+ reads ）
* **myloasm** (v0.1.0)： Flye ，“”
* **Bakta** (v1.8.2)： **/** （ gff ，）
* **MetaBAT** (v2.5) / **CONCOCT** (v1.1.0) / **MaxBin** (v2.2.7)： contigs **** draft genomes
* **DAS Tool** (v1.1.6)： binning （bin consolidation）
* **CheckM** (v1.2.2)： bin （/）
* **GTDB-Tk** (v2.3.0；GTDB r214)： bins  **** 

---

## 3) /

* **geNomad** (v1.7.6) / **VIBRANT** (v1.2.1) / **VirSorter2** (v2.2.4) / **Cenote-Taker3** (v3.4.0)： **/** （）
* **CheckV** (v1.0.1) +  **CheckV companion scripts** ： host-trimming ；/ identity & coverage
* **Snakemake** (v5.26.0)： VirSorter2  Snakemake， VirSorter2 
* **blast+** (v2.2.31)： contigs ↔  ； T1/T2 /
* **Bowtie 2** (v2.5.4)： reads 
* **SAMtools** (v1.21)： per-base coverage（）

---

## 4) /

* **FastANI** (v1.34)： T1/T2  MAG  ANI，“strain replacement”
* **NGMLR** (v0.2.7)： reads ↔ ，（SV）
* **SAMtools** (v1.9)：SV  BAM 
* **Sniffles2** (v2.2)： **** 
* **iPHoP** (v1.3.3；db iPHoP_db_Aug23_rw)： **** 
* **MMseqs2** (v14.7e284；mmseqs taxonomy module， GTDB v214.1)： contig  **** ，

---

## 5) / + 

* **AliTV** ： bins/（）
* **pharokka** (v1.7.3；database v1.4.0)： **** （ --meta ）
* **LoVis4u** (v0.1.4.1)：（synteny）； pharokka  gff， Bakta  gff 
* **HMMER** (v3.4；hmmsearch)： Pfam HMM profiles ，/
* **ISEScan** (v1.7.2.3)： IS （ IS30  “IScream phages”）
* **NCBI Datasets tool** ： Clostridia /

---

## 6) ，

* **Phanta v1.0** ：（ **VLP-enriched** ） IScream phage  profile / 
* **DamageProfiler v1.1** ：（paleofeces） IS30/phage contig  **ancient DNA damage pattern** 
