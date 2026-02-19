# Bioinformatics Tools Help Documentation
Generated on: 2026 01 07  21:26:20 CST

## 1. Nextflow
========================================
Usage: nextflow [options] COMMAND [arg...]

Options:
  -C
     Use the specified configuration file(s) overriding any defaults
  -D
     Set JVM properties
  -bg
     Execute nextflow in background
  -c, -config
     Add the specified file to configuration set
  -config-ignore-includes
     Disable the parsing of config includes
  -d, -dockerize
     Launch nextflow via Docker (experimental)
  -h
     Print this help
  -log
     Set nextflow log file path
  -q, -quiet
     Do not print information messages
  -syslog
     Send logs to syslog server (eg. localhost:514)
  -v, -version
     Print the program version

Commands:
  clean         Clean up project cache and work directories
  clone         Clone a project into a folder
  config        Print a project configuration
  console       Launch Nextflow interactive console
  drop          Delete the local copy of a project
  help          Print the usage help for a command
  info          Print project and system runtime information
  kuberun       Execute a workflow in a Kubernetes cluster (experimental)
  list          List all downloaded projects
  log           Print executions log and runtime info
  pull          Download or update a project
  run           Execute a pipeline project
  secrets       Manage pipeline secrets (preview)
  self-update   Update nextflow runtime to the latest available version
  view          View project script file(s)


## 2. HTStream
========================================
/usr/local/env-execute: line 3: exec: htstream: not found

## 3. TrimGalore
========================================

 USAGE:

trim_galore [options] <filename(s)>


-h/--help               Print this help message and exits.

-v/--version            Print the version information and exits.

-q/--quality <INT>      Trim low-quality ends from reads in addition to adapter removal. For
                        RRBS samples, quality trimming will be performed first, and adapter
                        trimming is carried in a second round. Other files are quality and adapter
                        trimmed in a single pass. The algorithm is the same as the one used by BWA
                        (Subtract INT from all qualities; compute partial sums from all indices
                        to the end of the sequence; cut sequence at the index at which the sum is
                        minimal). Default Phred score: 20.

--phred33               Instructs Cutadapt to use ASCII+33 quality scores as Phred scores
                        (Sanger/Illumina 1.9+ encoding) for quality trimming. Default: ON.

--phred64               Instructs Cutadapt to use ASCII+64 quality scores as Phred scores
                        (Illumina 1.5 encoding) for quality trimming.

--fastqc                Run FastQC in the default mode on the FastQ file once trimming is complete.

--fastqc_args "<ARGS>"  Passes extra arguments to FastQC. If more than one argument is to be passed
                        to FastQC they must be in the form "arg1 arg2 etc.". An example would be:
                        --fastqc_args "--nogroup --outdir /home/". Passing extra arguments will
                        automatically invoke FastQC, so --fastqc does not have to be specified
                        separately.

-a/--adapter <STRING>   Adapter sequence to be trimmed. If not specified explicitly, Trim Galore will
                        try to auto-detect whether the Illumina universal, Nextera transposase or Illumina
                        small RNA adapter sequence was used. Also see '--illumina', '--nextera' and
                        '--small_rna'. If no adapter can be detected within the first 1 million sequences
                        of the first file specified or if there is a tie between several adapter sequences,
                        Trim Galore defaults to '--illumina' (as long as the Illumina adapter was one of the
                        options, else '--nextera' is the default). A single base
                        may also be given as e.g. -a A{10}, to be expanded to -a AAAAAAAAAA.

-a2/--adapter2 <STRING> Optional adapter sequence to be trimmed off read 2 of paired-end files. This
                        option requires '--paired' to be specified as well. If the libraries to be trimmed
                        are smallRNA then a2 will be set to the Illumina small RNA 5' adapter automatically
                        (GATCGTCGGACT). A single base may also be given as e.g. -a2 A{10}, to be expanded
                        to -a2 AAAAAAAAAA.

--illumina              Adapter sequence to be trimmed is the first 13bp of the Illumina universal adapter
                        'AGATCGGAAGAGC' instead of the default auto-detection of adapter sequence.

--nextera               Adapter sequence to be trimmed is the first 12bp of the Nextera adapter
                        'CTGTCTCTTATA' instead of the default auto-detection of adapter sequence.

--small_rna             Adapter sequence to be trimmed is the first 12bp of the Illumina Small RNA 3' Adapter
                        'TGGAATTCTCGG' instead of the default auto-detection of adapter sequence. Selecting
                        to trim smallRNA adapters will also lower the --length value to 18bp. If the smallRNA
                        libraries are paired-end then a2 will be set to the Illumina small RNA 5' adapter
                        automatically (GATCGTCGGACT) unless -a 2 had been defined explicitly.

--consider_already_trimmed <INT>     During adapter auto-detection, the limit set by <INT> allows the user to 
                        set a threshold up to which the file is considered already adapter-trimmed. If no adapter
                        sequence exceeds this threshold, no additional adapter trimming will be performed (technically,
                        the adapter is set to '-a X'). Quality trimming is still performed as usual.
                        Default: NOT SELECTED (i.e. normal auto-detection precedence rules apply).                     

--max_length <INT>      Discard reads that are longer than <INT> bp after trimming. This is only advised for
                        smallRNA sequencing to remove non-small RNA sequences.


--stringency <INT>      Overlap with adapter sequence required to trim a sequence. Defaults to a
                        very stringent setting of 1, i.e. even a single bp of overlapping sequence
                        will be trimmed off from the 3' end of any read.

-e <ERROR RATE>         Maximum allowed error rate (no. of errors divided by the length of the matching
                        region) (default: 0.1)

--gzip                  Compress the output file with GZIP. If the input files are GZIP-compressed
                        the output files will automatically be GZIP compressed as well. As of v0.2.8 the
                        compression will take place on the fly.

--dont_gzip             Output files won't be compressed with GZIP. This option overrides --gzip.

--length <INT>          Discard reads that became shorter than length INT because of either
                        quality or adapter trimming. A value of '0' effectively disables
                        this behaviour. Default: 20 bp.

                        For paired-end files, both reads of a read-pair need to be longer than
                        <INT> bp to be printed out to validated paired-end files (see option --paired).
                        If only one read became too short there is the possibility of keeping such
                        unpaired single-end reads (see --retain_unpaired). Default pair-cutoff: 20 bp.

--max_n COUNT           The total number of Ns (as integer) a read may contain before it will be removed altogether.
                        In a paired-end setting, either read exceeding this limit will result in the entire
                        pair being removed from the trimmed output files.

--trim-n                Removes Ns from either side of the read. This option does currently not work in RRBS mode.

-o/--output_dir <DIR>   If specified all output will be written to this directory instead of the current
                        directory. If the directory doesn't exist it will be created for you.

--no_report_file        If specified no report file will be generated.

--suppress_warn         If specified any output to STDOUT or STDERR will be suppressed.

--clip_R1 <int>         Instructs Trim Galore to remove <int> bp from the 5' end of read 1 (or single-end
                        reads). This may be useful if the qualities were very poor, or if there is some
                        sort of unwanted bias at the 5' end. Default: OFF.

--clip_R2 <int>         Instructs Trim Galore to remove <int> bp from the 5' end of read 2 (paired-end reads
                        only). This may be useful if the qualities were very poor, or if there is some sort
                        of unwanted bias at the 5' end. For paired-end BS-Seq, it is recommended to remove
                        the first few bp because the end-repair reaction may introduce a bias towards low
                        methylation. Please refer to the M-bias plot section in the Bismark User Guide for
                        some examples. Default: OFF.

--three_prime_clip_R1 <int>     Instructs Trim Galore to remove <int> bp from the 3' end of read 1 (or single-end
                        reads) AFTER adapter/quality trimming has been performed. This may remove some unwanted
                        bias from the 3' end that is not directly related to adapter sequence or basecall quality.
                        Default: OFF.

--three_prime_clip_R2 <int>     Instructs Trim Galore to remove <int> bp from the 3' end of read 2 AFTER
                        adapter/quality trimming has been performed. This may remove some unwanted bias from
                        the 3' end that is not directly related to adapter sequence or basecall quality.
                        Default: OFF.

--2colour/--nextseq INT This enables the option '--nextseq-trim=3'CUTOFF' within Cutadapt, which will set a quality
                        cutoff (that is normally given with -q instead), but qualities of G bases are ignored.
                        This trimming is in common for the NextSeq- and NovaSeq-platforms, where basecalls without
                        any signal are called as high-quality G bases. This is mutually exlusive with '-q INT'.


--path_to_cutadapt </path/to/cutadapt>     You may use this option to specify a path to the Cutadapt executable,
                        e.g. /my/home/cutadapt-1.7.1/bin/cutadapt. Else it is assumed that Cutadapt is in
                        the PATH.

--basename <PREFERRED_NAME>	Use PREFERRED_NAME as the basename for output files, instead of deriving the filenames from
                        the input files. Single-end data would be called PREFERRED_NAME_trimmed.fq(.gz), or
                        PREFERRED_NAME_val_1.fq(.gz) and PREFERRED_NAME_val_2.fq(.gz) for paired-end data. --basename
                        only works when 1 file (single-end) or 2 files (paired-end) are specified, but not for longer lists.

-j/--cores INT          Number of cores to be used for trimming [default: 1]. For Cutadapt to work with multiple cores, it
                        requires Python 3 as well as parallel gzip (pigz) installed on the system. The version of Python used 
                        is detected from the shebang line of the Cutadapt executable (either 'cutadapt', or a specified path).
                        If Python 2 is detected, --cores is set to 1.
                        If pigz cannot be detected on your system, Trim Galore reverts to using gzip compression. Please note
                        that gzip compression will slow down multi-core processes so much that it is hardly worthwhile, please 
                        see: https://github.com/FelixKrueger/TrimGalore/issues/16#issuecomment-458557103 for more info).
						
                        Actual core usage: It should be mentioned that the actual number of cores used is a little convoluted.
                        Assuming that Python 3 is used and pigz is installed, --cores 2 would use 2 cores to read the input
                        (probably not at a high usage though), 2 cores to write to the output (at moderately high usage), and 
                        2 cores for Cutadapt itself + 2 additional cores for Cutadapt (not sure what they are used for) + 1 core
                        for Trim Galore itself. So this can be up to 9 cores, even though most of them won't be used at 100% for
                        most of the time. Paired-end processing uses twice as many cores for the validation (= writing out) step.
                        --cores 4 would then be: 4 (read) + 4 (write) + 4 (Cutadapt) + 2 (extra Cutadapt) +	1 (Trim Galore) = 15.

                        It seems that --cores 4 could be a sweet spot, anything above has diminishing returns.
			


SPECIFIC TRIMMING - without adapter/quality trimming

--hardtrim5 <int>       Instead of performing adapter-/quality trimming, this option will simply hard-trim sequences
                        to <int> bp at the 5'-end. Once hard-trimming of files is complete, Trim Galore will exit.
                        Hard-trimmed output files will end in .<int>_5prime.fq(.gz). Here is an example:

                        before:         CCTAAGGAAACAAGTACACTCCACACATGCATAAAGGAAATCAAATGTTATTTTTAAGAAAATGGAAAAT
                        --hardtrim5 20: CCTAAGGAAACAAGTACACT

--hardtrim3 <int>       Instead of performing adapter-/quality trimming, this option will simply hard-trim sequences
                        to <int> bp at the 3'-end. Once hard-trimming of files is complete, Trim Galore will exit.
                        Hard-trimmed output files will end in .<int>_3prime.fq(.gz). Here is an example:

                        before:         CCTAAGGAAACAAGTACACTCCACACATGCATAAAGGAAATCAAATGTTATTTTTAAGAAAATGGAAAAT
                        --hardtrim3 20:                                                   TTTTTAAGAAAATGGAAAAT

--clock                 In this mode, reads are trimmed in a specific way that is currently used for the Mouse
                        Epigenetic Clock (see here: Multi-tissue DNA methylation age predictor in mouse, Stubbs et al.,
                        Genome Biology, 2017 18:68 https://doi.org/10.1186/s13059-017-1203-5). Following this, Trim Galore
                        will exit.

                        In it's current implementation, the dual-UMI RRBS reads come in the following format:

                        Read 1  5' UUUUUUUU CAGTA FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF TACTG UUUUUUUU 3'
                        Read 2  3' UUUUUUUU GTCAT FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF ATGAC UUUUUUUU 5'

                        Where UUUUUUUU is a random 8-mer unique molecular identifier (UMI), CAGTA is a constant region,
                        and FFFFFFF... is the actual RRBS-Fragment to be sequenced. The UMIs for Read 1 (R1) and
                        Read 2 (R2), as well as the fixed sequences (F1 or F2), are written into the read ID and
                        removed from the actual sequence. Here is an example:

                        R1: @HWI-D00436:407:CCAETANXX:1:1101:4105:1905 1:N:0: CGATGTTT
                            ATCTAGTTCAGTACGGTGTTTTCGAATTAGAAAAATATGTATAGAGGAAATAGATATAAAGGCGTATTCGTTATTG
                        R2: @HWI-D00436:407:CCAETANXX:1:1101:4105:1905 3:N:0: CGATGTTT
                            CAATTTTGCAGTACAAAAATAATACCTCCTCTATTTATCCAAAATCACAAAAAACCACCCACTTAACTTTCCCTAA

                        R1: @HWI-D00436:407:CCAETANXX:1:1101:4105:1905 1:N:0: CGATGTTT:R1:ATCTAGTT:R2:CAATTTTG:F1:CAGT:F2:CAGT
                                         CGGTGTTTTCGAATTAGAAAAATATGTATAGAGGAAATAGATATAAAGGCGTATTCGTTATTG
                        R2: @HWI-D00436:407:CCAETANXX:1:1101:4105:1905 3:N:0: CGATGTTT:R1:ATCTAGTT:R2:CAATTTTG:F1:CAGT:F2:CAGT
                                         CAAAAATAATACCTCCTCTATTTATCCAAAATCACAAAAAACCACCCACTTAACTTTCCCTAA

                        Following clock trimming, the resulting files (.clock_UMI.R1.fq(.gz) and .clock_UMI.R2.fq(.gz))
                        should be adapter- and quality trimmed with Trim Galore as usual. In addition, reads need to be trimmed
                        by 15bp from their 3' end to get rid of potential UMI and fixed sequences. The command is:

                        trim_galore --paired --three_prime_clip_R1 15 --three_prime_clip_R2 15 *.clock_UMI.R1.fq.gz *.clock_UMI.R2.fq.gz

                        Following this, reads should be aligned with Bismark and deduplicated with UmiBam
                        in '--dual_index' mode (see here: https://github.com/FelixKrueger/Umi-Grinder). UmiBam recognises
                        the UMIs within this pattern: R1:(ATCTAGTT):R2:(CAATTTTG): as (UMI R1) and (UMI R2).

--polyA                 This is a new, still experimental, trimming mode to identify and remove poly-A tails from sequences.
                        When --polyA is selected, Trim Galore attempts to identify from the first supplied sample whether
                        sequences contain more often a stretch of either 'AAAAAAAAAA' or 'TTTTTTTTTT'. This determines
                        if Read 1 of a paired-end end file, or single-end files, are trimmed for PolyA or PolyT. In case of
                        paired-end sequencing, Read2 is trimmed for the complementary base from the start of the reads. The
                        auto-detection uses a default of A{20} for Read1 (3'-end trimming) and T{150} for Read2 (5'-end trimming).
                        These values may be changed manually using the options -a and -a2.

                        In addition to trimming the sequences, white spaces are replaced with _ and it records in the read ID
                        how many bases were trimmed so it can later be used to identify PolyA trimmed sequences. This is currently done
                        by writing tags to both the start ("32:A:") and end ("_PolyA:32") of the reads in the following example:

                        @READ-ID:1:1102:22039:36996 1:N:0:CCTAATCC
                        GCCTAAGGAAACAAGTACACTCCACACATGCATAAAGGAAATCAAATGTTATTTTTAAGAAAATGGAAAATAAAAACTTTATAAACACCAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA

                        @32:A:READ-ID:1:1102:22039:36996_1:N:0:CCTAATCC_PolyA:32
                        GCCTAAGGAAACAAGTACACTCCACACATGCATAAAGGAAATCAAATGTTATTTTTAAGAAAATGGAAAATAAAAACTTTATAAACACC

                        PLEASE NOTE: The poly-A trimming mode expects that sequences were both adapter and quality trimmed
                        before looking for Poly-A tails, and it is the user's responsibility to carry out an initial round of
                        trimming. The following sequence:
 
                        1) trim_galore file.fastq.gz
                        2) trim_galore --polyA file_trimmed.fq.gz
                        3) zcat file_trimmed_trimmed.fq.gz | grep -A 3 PolyA | grep -v ^-- > PolyA_trimmed.fastq

                        Will 1) trim qualities and Illumina adapter contamination, 2) find and remove PolyA contamination.
                        Finally, if desired, 3) will specifically find PolyA trimmed sequences to a specific FastQ file of your choice.

--implicon              This is a special mode of operation for paired-end data, such as required for the IMPLICON method, where a UMI sequence
                        is getting transferred from the start of Read 2 to the readID of both reads. Following this, Trim Galore will exit.

                        In it's current implementation, the UMI carrying reads come in the following format:

                        Read 1  5' FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF 3'
                        Read 2  3' UUUUUUUUFFFFFFFFFFFFFFFFFFFFFFFFFFFF 5'

                        Where UUUUUUUU is a random 8-mer unique molecular identifier (UMI) and FFFFFFF... is the actual fragment to be
                        sequenced. The UMI of Read 2 (R2) is written into the read ID of both reads and removed from the actual sequence.
                        Here is an example:

                        R1: @HWI-D00436:407:CCAETANXX:1:1101:4105:1905 1:N:0: CGATGTTT
                            ATCTAGTTCAGTACGGTGTTTTCGAATTAGAAAAATATGTATAGAGGAAATAGATATAAAGGCGTATTCGTTATTG
                        R2: @HWI-D00436:407:CCAETANXX:1:1101:4105:1905 3:N:0: CGATGTTT
                            CAATTTTGCAGTACAAAAATAATACCTCCTCTATTTATCCAAAATCACAAAAAACCACCCACTTAACTTTCCCTAA
                        
                        After --implicon trimming:
                        R1: @HWI-D00436:407:CCAETANXX:1:1101:4105:1905 1:N:0: CGATGTTT:CAATTTTG
                            ATCTAGTTCAGTACGGTGTTTTCGAATTAGAAAAATATGTATAGAGGAAATAGATATAAAGGCGTATTCGTTATTG
                        R2: @HWI-D00436:407:CCAETANXX:1:1101:4105:1905 3:N:0: CGATGTTT:CAATTTTG
                                    CAGTACAAAAATAATACCTCCTCTATTTATCCAAAATCACAAAAAACCACCCACTTAACTTTCCCTAA

RRBS-specific options (MspI digested material):

--rrbs                  Specifies that the input file was an MspI digested RRBS sample (recognition
                        site: CCGG). Single-end or Read 1 sequences (paired-end) which were adapter-trimmed
                        will have a further 2 bp removed from their 3' end. Sequences which were merely
                        trimmed because of poor quality will not be shortened further. Read 2 of paired-end
                        libraries will in addition have the first 2 bp removed from the 5' end (by setting
                        '--clip_r2 2'). This is to avoid using artificial methylation calls from the filled-in
                        cytosine positions close to the 3' MspI site in sequenced fragments.
                        This option is not recommended for users of the NuGEN ovation RRBS System 1-16
                        kit (see below).

--non_directional       Selecting this option for non-directional RRBS libraries will screen
                        quality-trimmed sequences for 'CAA' or 'CGA' at the start of the read
                        and, if found, removes the first two basepairs. Like with the option
                        '--rrbs' this avoids using cytosine positions that were filled-in
                        during the end-repair step. '--non_directional' requires '--rrbs' to
                        be specified as well. Note that this option does not set '--clip_r2 2' in
                        paired-end mode.

--keep                  Keep the quality trimmed intermediate file. Default: off, which means
                        the temporary file is being deleted after adapter trimming. Only has
                        an effect for RRBS samples since other FastQ files are not trimmed
                        for poor qualities separately.


Note for RRBS using the NuGEN Ovation RRBS System 1-16 kit:

Owing to the fact that the NuGEN Ovation kit attaches a varying number of nucleotides (0-3) after each MspI
site Trim Galore should be run WITHOUT the option --rrbs. This trimming is accomplished in a subsequent
diversity trimming step afterwards (see their manual).



Note for RRBS using MseI:

If your DNA material was digested with MseI (recognition motif: TTAA) instead of MspI it is NOT necessary
to specify --rrbs or --non_directional since virtually all reads should start with the sequence
'TAA', and this holds true for both directional and non-directional libraries. As the end-repair of 'TAA'
restricted sites does not involve any cytosines it does not need to be treated especially. Instead, simply
run Trim Galore! in the standard (i.e. non-RRBS) mode.




Paired-end specific options:

--paired                This option performs length trimming of quality/adapter/RRBS trimmed reads for
                        paired-end files. To pass the validation test, both sequences of a sequence pair
                        are required to have a certain minimum length which is governed by the option
                        --length (see above). If only one read passes this length threshold the
                        other read can be rescued (see option --retain_unpaired). Using this option lets
                        you discard too short read pairs without disturbing the sequence-by-sequence order
                        of FastQ files which is required by many aligners.

                        Trim Galore! expects paired-end files to be supplied in a pairwise fashion, e.g.
                        file1_1.fq file1_2.fq SRR2_1.fq.gz SRR2_2.fq.gz ... .

-t/--trim1              Trims 1 bp off every read from its 3' end. This may be needed for FastQ files that
                        are to be aligned as paired-end data with Bowtie. This is because Bowtie (1) regards
                        alignments like this:

                          R1 --------------------------->     or this:    ----------------------->  R1
                          R2 <---------------------------                       <-----------------  R2

                        as invalid (whenever a start/end coordinate is contained within the other read).
                        NOTE: If you are planning to use Bowtie2, BWA etc. you don't need to specify this option.

--retain_unpaired       If only one of the two paired-end reads became too short, the longer
                        read will be written to either '.unpaired_1.fq' or '.unpaired_2.fq'
                        output files. The length cutoff for unpaired single-end reads is
                        governed by the parameters -r1/--length_1 and -r2/--length_2. Default: OFF.

-r1/--length_1 <INT>    Unpaired single-end read length cutoff needed for read 1 to be written to
                        '.unpaired_1.fq' output file. These reads may be mapped in single-end mode.
                        Default: 35 bp.

-r2/--length_2 <INT>    Unpaired single-end read length cutoff needed for read 2 to be written to
                        '.unpaired_2.fq' output file. These reads may be mapped in single-end mode.
                        Default: 35 bp.

Last modified on 07 October 2020.


## 4. bwa
========================================

Program: bwa (alignment via Burrows-Wheeler transformation)
Version: 0.7.17-r1188
Contact: Heng Li <lh3@sanger.ac.uk>

Usage:   bwa <command> [options]

Command: index         index sequences in the FASTA format
         mem           BWA-MEM algorithm
         fastmap       identify super-maximal exact matches
         pemerge       merge overlapping paired ends (EXPERIMENTAL)
         aln           gapped/ungapped alignment
         samse         generate alignment (single ended)
         sampe         generate alignment (paired ended)
         bwasw         BWA-SW for long queries

         shm           manage indices in shared memory
         fa2pac        convert FASTA to PAC format
         pac2bwt       generate BWT from PAC
         pac2bwtgen    alternative algorithm for generating BWT
         bwtupdate     update .bwt to the new format
         bwt2sa        generate SA from BWT and Occ

Note: To use BWA, you need to first index the genome with `bwa index'.
      There are three alignment algorithms in BWA: `mem', `bwasw', and
      `aln/samse/sampe'. If you are not sure which to use, try `bwa mem'
      first. Please `man ./bwa.1' for the manual.


## 5. NanoPlot
========================================
usage: NanoPlot [-h] [-v] [-t THREADS] [--verbose] [--store] [--raw] [--huge]
                [-o OUTDIR] [--no_static] [-p PREFIX] [--tsv_stats]
                [--info_in_report] [--maxlength N] [--minlength N]
                [--drop_outliers] [--downsample N] [--loglength]
                [--percentqual] [--alength] [--minqual N] [--runtime_until N]
                [--readtype {1D,2D,1D2}] [--barcoded] [--no_supplementary]
                [-c COLOR] [-cm COLORMAP]
                [-f [{png,jpg,jpeg,webp,svg,pdf,eps,json} ...]]
                [--plots [{kde,hex,dot} ...]] [--legacy [{kde,dot,hex} ...]]
                [--listcolors] [--listcolormaps] [--no-N50] [--N50]
                [--title TITLE] [--font_scale FONT_SCALE] [--dpi DPI]
                [--hide_stats]
                (--fastq file [file ...] | --fasta file [file ...] | --fastq_rich file [file ...] | --fastq_minimal file [file ...] | --summary file [file ...] | --bam file [file ...] | --ubam file [file ...] | --cram file [file ...] | --pickle pickle | --feather file [file ...])

CREATES VARIOUS PLOTS FOR LONG READ SEQUENCING DATA.

General options:
  -h, --help            show the help and exit
  -v, --version         Print version and exit.
  -t, --threads THREADS
                        Set the allowed number of threads to be used by the script
  --verbose             Write log messages also to terminal.
  --store               Store the extracted data in a pickle file for future plotting.
  --raw                 Store the extracted data in tab separated file.
  --huge                Input data is one very large file.
  -o, --outdir OUTDIR   Specify directory in which output has to be created.
  --no_static           Do not make static (png) plots.
  -p, --prefix PREFIX   Specify an optional prefix to be used for the output files.
  --tsv_stats           Output the stats file as a properly formatted TSV.
  --info_in_report      Add NanoPlot run info in the report.

Options for filtering or transforming input prior to plotting:
  --maxlength N         Hide reads longer than length specified.
  --minlength N         Hide reads shorter than length specified.
  --drop_outliers       Drop outlier reads with extreme long length.
  --downsample N        Reduce dataset to N reads by random sampling.
  --loglength           Additionally show logarithmic scaling of lengths in plots.
  --percentqual         Use qualities as theoretical percent identities.
  --alength             Use aligned read lengths rather than sequenced length (bam mode)
  --minqual N           Drop reads with an average quality lower than specified.
  --runtime_until N     Only take the N first hours of a run
  --readtype {1D,2D,1D2}
                        Which read type to extract information about from summary. Options are 1D, 2D,
                        1D2
  --barcoded            Use if you want to split the summary file by barcode
  --no_supplementary    Use if you want to remove supplementary alignments

Options for customizing the plots created:
  -c, --color COLOR     Specify a valid matplotlib color for the plots
  -cm, --colormap COLORMAP
                        Specify a valid matplotlib colormap for the heatmap
  -f, --format [{png,jpg,jpeg,webp,svg,pdf,eps,json} ...]
                        Specify the output format of the plots, which are in addition to the html files
  --plots [{kde,hex,dot} ...]
                        Specify which bivariate plots have to be made.
  --legacy [{kde,dot,hex} ...]
                        Specify which bivariate plots have to be made (legacy mode).
  --listcolors          List the colors which are available for plotting and exit.
  --listcolormaps       List the colors which are available for plotting and exit.
  --no-N50              Hide the N50 mark in the read length histogram
  --N50                 Show the N50 mark in the read length histogram
  --title TITLE         Add a title to all plots, requires quoting if using spaces
  --font_scale FONT_SCALE
                        Scale the font of the plots by a factor
  --dpi DPI             Set the dpi for saving images
  --hide_stats          Not adding Pearson R stats in some bivariate plots

Input data sources, one of these is required.:
  --fastq file [file ...]
                        Data is in one or more default fastq file(s).
  --fasta file [file ...]
                        Data is in one or more fasta file(s).
  --fastq_rich file [file ...]
                        Data is in one or more fastq file(s) generated by albacore, MinKNOW or guppy
                        with additional information concerning channel and time.
  --fastq_minimal file [file ...]
                        Data is in one or more fastq file(s) generated by albacore, MinKNOW or guppy
                        with additional information concerning channel and time. Is extracted swiftly
                        without elaborate checks.
  --summary file [file ...]
                        Data is in one or more summary file(s) generated by albacore or guppy.
  --bam file [file ...]
                        Data is in one or more sorted bam file(s).
  --ubam file [file ...]
                        Data is in one or more unmapped bam file(s).
  --cram file [file ...]
                        Data is in one or more sorted cram file(s).
  --pickle pickle       Data is a pickle file stored earlier.
  --feather, --arrow file [file ...]
                        Data is in one or more feather file(s).

EXAMPLES:
    NanoPlot --summary sequencing_summary.txt --loglength -o summary-plots-log-transformed
    NanoPlot -t 2 --fastq reads1.fastq.gz reads2.fastq.gz --maxlength 40000 --plots hex dot
    NanoPlot --color yellow --bam alignment1.bam alignment2.bam alignment3.bam --downsample 10000
    

## 6. Seqtk
========================================

Usage:   seqtk <command> <arguments>
Version: 1.4-r122

Command: seq       common transformation of FASTA/Q
         size      report the number sequences and bases
         comp      get the nucleotide composition of FASTA/Q
         sample    subsample sequences
         subseq    extract subsequences from FASTA/Q
         fqchk     fastq QC (base/quality summary)
         mergepe   interleave two PE FASTA/Q files
         split     split one file into multiple smaller files
         trimfq    trim FASTQ using the Phred algorithm

         hety      regional heterozygosity
         gc        identify high- or low-GC regions
         mutfa     point mutate FASTA at specified positions
         mergefa   merge two FASTA/Q files
         famask    apply a X-coded FASTA to a source FASTA
         dropse    drop unpaired from interleaved PE FASTA/Q
         rename    rename sequence names
         randbase  choose a random base from hets
         cutN      cut sequence at long N
         gap       get the gap locations
         listhet   extract the position of each het
         hpc       homopolyer-compressed sequence
         telo      identify telomere repeats in asm or long reads


## 7. MegaHIT
========================================
MEGAHIT v1.2.9

contact: Dinghua Li <voutcn@gmail.com>

Usage:
  megahit [options] {-1 <pe1> -2 <pe2> | --12 <pe12> | -r <se>} [-o <out_dir>]

  Input options that can be specified for multiple times (supporting plain text and gz/bz2 extensions)
    -1                       <pe1>          comma-separated list of fasta/q paired-end #1 files, paired with files in <pe2>
    -2                       <pe2>          comma-separated list of fasta/q paired-end #2 files, paired with files in <pe1>
    --12                     <pe12>         comma-separated list of interleaved fasta/q paired-end files
    -r/--read                <se>           comma-separated list of fasta/q single-end files

Optional Arguments:
  Basic assembly options:
    --min-count              <int>          minimum multiplicity for filtering (k_min+1)-mers [2]
    --k-list                 <int,int,..>   comma-separated list of kmer size
                                            all must be odd, in the range 15-255, increment <= 28)
                                            [21,29,39,59,79,99,119,141]

  Another way to set --k-list (overrides --k-list if one of them set):
    --k-min                  <int>          minimum kmer size (<= 255), must be odd number [21]
    --k-max                  <int>          maximum kmer size (<= 255), must be odd number [141]
    --k-step                 <int>          increment of kmer size of each iteration (<= 28), must be even number [10]

  Advanced assembly options:
    --no-mercy                              do not add mercy kmers
    --bubble-level           <int>          intensity of bubble merging (0-2), 0 to disable [2]
    --merge-level            <l,s>          merge complex bubbles of length <= l*kmer_size and similarity >= s [20,0.95]
    --prune-level            <int>          strength of low depth pruning (0-3) [2]
    --prune-depth            <int>          remove unitigs with avg kmer depth less than this value [2]
    --disconnect-ratio       <float>        disconnect unitigs if its depth is less than this ratio times 
                                            the total depth of itself and its siblings [0.1]  
    --low-local-ratio        <float>        remove unitigs if its depth is less than this ratio times
                                            the average depth of the neighborhoods [0.2]
    --max-tip-len            <int>          remove tips less than this value [2*k]
    --cleaning-rounds        <int>          number of rounds for graph cleanning [5]
    --no-local                              disable local assembly
    --kmin-1pass                            use 1pass mode to build SdBG of k_min

  Presets parameters:
    --presets                <str>          override a group of parameters; possible values:
                                            meta-sensitive: '--min-count 1 --k-list 21,29,39,49,...,129,141'
                                            meta-large: '--k-min 27 --k-max 127 --k-step 10'
                                            (large & complex metagenomes, like soil)

  Hardware options:
    -m/--memory              <float>        max memory in byte to be used in SdBG construction
                                            (if set between 0-1, fraction of the machine's total memory) [0.9]
    --mem-flag               <int>          SdBG builder memory mode. 0: minimum; 1: moderate;
                                            others: use all memory specified by '-m/--memory' [1]
    -t/--num-cpu-threads     <int>          number of CPU threads [# of logical processors]
    --no-hw-accel                           run MEGAHIT without BMI2 and POPCNT hardware instructions

  Output options:
    -o/--out-dir             <string>       output directory [./megahit_out]
    --out-prefix             <string>       output prefix (the contig file will be OUT_DIR/OUT_PREFIX.contigs.fa)
    --min-contig-len         <int>          minimum length of contigs to output [200]
    --keep-tmp-files                        keep all temporary files
    --tmp-dir                <string>       set temp directory

Other Arguments:
    --continue                              continue a MEGAHIT run from its last available check point.
                                            please set the output directory correctly when using this option.
    --test                                  run MEGAHIT on a toy test dataset
    -h/--help                               print the usage message
    -v/--version                            print version


## 8. Flye
========================================
usage: flye (--pacbio-raw | --pacbio-corr | --pacbio-hifi | --nano-raw |
	     --nano-corr | --nano-hq ) file1 [file_2 ...]
	     --out-dir PATH

	     [--genome-size SIZE] [--threads int] [--iterations int]
	     [--meta] [--polish-target] [--min-overlap SIZE]
	     [--keep-haplotypes] [--debug] [--version] [--help] 
	     [--scaffold] [--resume] [--resume-from] [--stop-after] 
	     [--read-error float] [--extra-params] 
	     [--deterministic]

Assembly of long reads with repeat graphs

optional arguments:
  -h, --help            show this help message and exit
  --pacbio-raw path [path ...]
                        PacBio regular CLR reads (<20% error)
  --pacbio-corr path [path ...]
                        PacBio reads that were corrected with other methods
                        (<3% error)
  --pacbio-hifi path [path ...]
                        PacBio HiFi reads (<1% error)
  --nano-raw path [path ...]
                        ONT regular reads, pre-Guppy5 (<20% error)
  --nano-corr path [path ...]
                        ONT reads that were corrected with other methods (<3%
                        error)
  --nano-hq path [path ...]
                        ONT high-quality reads: Guppy5+ SUP or Q20 (<5% error)
  --subassemblies path [path ...]
                        [deprecated] high-quality contigs input
  -g size, --genome-size size
                        estimated genome size (for example, 5m or 2.6g)
  -o path, --out-dir path
                        Output directory
  -t int, --threads int
                        number of parallel threads [1]
  -i int, --iterations int
                        number of polishing iterations [1]
  -m int, --min-overlap int
                        minimum overlap between reads [auto]
  --asm-coverage int    reduced coverage for initial disjointig assembly [not
                        set]
  --hifi-error float    [deprecated] same as --read-error
  --read-error float    adjust parameters for given read error rate (as
                        fraction e.g. 0.03)
  --extra-params extra_params
                        extra configuration parameters list (comma-separated)
  --plasmids            unused (retained for backward compatibility)
  --meta                metagenome / uneven coverage mode
  --keep-haplotypes     do not collapse alternative haplotypes
  --no-alt-contigs      do not output contigs representing alternative
                        haplotypes
  --scaffold            enable scaffolding using graph [disabled by default]
  --trestle             [deprecated] enable Trestle [disabled by default]
  --polish-target path  run polisher on the target sequence
  --resume              resume from the last completed stage
  --resume-from stage_name
                        resume from a custom stage
  --stop-after stage_name
                        stop after the specified stage completed
  --debug               enable debug output
  -v, --version         show program's version number and exit
  --deterministic       perform disjointig assembly single-threaded

Input reads can be in FASTA or FASTQ format, uncompressed
or compressed with gz. Currently, PacBio (CLR, HiFi, corrected)
and ONT reads (regular, HQ, corrected) are supported. Expected error rates are
<15% for PB CLR/regular ONT; <5% for ONT HQ, <3% for corrected, and <1% for HiFi. Note that Flye
was primarily developed to run on uncorrected reads. You may specify multiple
files with reads (separated by spaces). Mixing different read
types is not yet supported. The --meta option enables the mode
for metagenome/uneven coverage assembly.

To reduce memory consumption for large genome assemblies,
you can use a subset of the longest reads for initial disjointig
assembly by specifying --asm-coverage and --genome-size options. Typically,
40x coverage is enough to produce good disjointigs.

You can run Flye polisher as a standalone tool using
--polish-target option.

## 9. Bakta
========================================
usage: bakta [--db DB] [--min-contig-length MIN_CONTIG_LENGTH]
             [--prefix PREFIX] [--output OUTPUT] [--force] [--genus GENUS]
             [--species SPECIES] [--strain STRAIN] [--plasmid PLASMID]
             [--complete] [--prodigal-tf PRODIGAL_TF]
             [--translation-table {11,4}] [--gram {+,-,?}] [--locus LOCUS]
             [--locus-tag LOCUS_TAG] [--keep-contig-headers]
             [--replicons REPLICONS] [--compliant] [--proteins PROTEINS]
             [--meta] [--skip-trna] [--skip-tmrna] [--skip-rrna]
             [--skip-ncrna] [--skip-ncrna-region] [--skip-crispr] [--skip-cds]
             [--skip-pseudo] [--skip-sorf] [--skip-gap] [--skip-ori]
             [--skip-plot] [--help] [--verbose] [--debug] [--threads THREADS]
             [--tmp-dir TMP_DIR] [--version]
             <genome>

Rapid & standardized annotation of bacterial genomes, MAGs & plasmids

positional arguments:
  <genome>              Genome sequences in (zipped) fasta format

Input / Output:
  --db DB, -d DB        Database path (default = <bakta_path>/db). Can also be
                        provided as BAKTA_DB environment variable.
  --min-contig-length MIN_CONTIG_LENGTH, -m MIN_CONTIG_LENGTH
                        Minimum contig size (default = 1; 200 in compliant
                        mode)
  --prefix PREFIX, -p PREFIX
                        Prefix for output files
  --output OUTPUT, -o OUTPUT
                        Output directory (default = current working directory)
  --force, -f           Force overwriting existing output folder (except for
                        current working directory)

Organism:
  --genus GENUS         Genus name
  --species SPECIES     Species name
  --strain STRAIN       Strain name
  --plasmid PLASMID     Plasmid name

Annotation:
  --complete            All sequences are complete replicons
                        (chromosome/plasmid[s])
  --prodigal-tf PRODIGAL_TF
                        Path to existing Prodigal training file to use for CDS
                        prediction
  --translation-table {11,4}
                        Translation table: 11/4 (default = 11)
  --gram {+,-,?}        Gram type for signal peptide predictions: +/-/?
                        (default = ?)
  --locus LOCUS         Locus prefix (default = 'contig')
  --locus-tag LOCUS_TAG
                        Locus tag prefix (default = autogenerated)
  --keep-contig-headers
                        Keep original contig headers
  --replicons REPLICONS, -r REPLICONS
                        Replicon information table (tsv/csv)
  --compliant           Force Genbank/ENA/DDJB compliance
  --proteins PROTEINS   Fasta file of trusted protein sequences for CDS
                        annotation
  --meta                Run in metagenome mode. This only affects CDS
                        prediction.

Workflow:
  --skip-trna           Skip tRNA detection & annotation
  --skip-tmrna          Skip tmRNA detection & annotation
  --skip-rrna           Skip rRNA detection & annotation
  --skip-ncrna          Skip ncRNA detection & annotation
  --skip-ncrna-region   Skip ncRNA region detection & annotation
  --skip-crispr         Skip CRISPR array detection & annotation
  --skip-cds            Skip CDS detection & annotation
  --skip-pseudo         Skip pseudogene detection & annotation
  --skip-sorf           Skip sORF detection & annotation
  --skip-gap            Skip gap detection & annotation
  --skip-ori            Skip oriC/oriT detection & annotation
  --skip-plot           Skip generation of circular genome plots

General:
  --help, -h            Show this help message and exit
  --verbose, -v         Print verbose information
  --debug               Run Bakta in debug mode. Temp data will not be
                        removed.
  --threads THREADS, -t THREADS
                        Number of threads to use (default = number of
                        available CPUs)
  --tmp-dir TMP_DIR     Location for temporary files (default = system
                        dependent auto detection)
  --version             show program's version number and exit

Version: 1.8.2
DOI: 10.1099/mgen.0.000685
URL: github.com/oschwengers/bakta

Citation:
Schwengers O., Jelonek L., Dieckmann M. A., Beyvers S., Blom J., Goesmann A. (2021).
Bakta: rapid and standardized annotation of bacterial genomes via alignment-free sequence identification.
Microbial Genomics, 7(11). https://doi.org/10.1099/mgen.0.000685

## 10. MetaBAT2
========================================
docker: Error response from daemon: failed to create task for container: failed to create shim task: OCI runtime create failed: runc create failed: unable to start container process: exec: "run_MetaBAT.sh": executable file not found in $PATH: unknown.

## 11. CONCOCT
========================================
/usr/local/lib/python3.12/site-packages/concoct/__init__.py:1: UserWarning: pkg_resources is deprecated as an API. See https://setuptools.pypa.io/en/latest/pkg_resources.html. The pkg_resources package is slated for removal as early as 2025-11-30. Refrain from using this package or pin to Setuptools<81.
  import pkg_resources  # part of setuptools
usage: concoct [-h] [--coverage_file COVERAGE_FILE]
               [--composition_file COMPOSITION_FILE] [-c CLUSTERS]
               [-k KMER_LENGTH] [-t THREADS] [-l LENGTH_THRESHOLD]
               [-r READ_LENGTH] [--total_percentage_pca TOTAL_PERCENTAGE_PCA]
               [-b BASENAME] [-s SEED] [-i ITERATIONS]
               [--no_cov_normalization] [--no_total_coverage]
               [--no_original_data] [-o] [-d] [-v]

options:
  -h, --help            show this help message and exit
  --coverage_file COVERAGE_FILE
                        specify the coverage file, containing a table where
                        each row correspond to a contig, and each column
                        correspond to a sample. The values are the average
                        coverage for this contig in that sample. All values
                        are separated with tabs.
  --composition_file COMPOSITION_FILE
                        specify the composition file, containing sequences in
                        fasta format. It is named the composition file since
                        it is used to calculate the kmer composition (the
                        genomic signature) of each contig.
  -c CLUSTERS, --clusters CLUSTERS
                        specify maximal number of clusters for VGMM, default
                        400.
  -k KMER_LENGTH, --kmer_length KMER_LENGTH
                        specify kmer length, default 4.
  -t THREADS, --threads THREADS
                        Number of threads to use
  -l LENGTH_THRESHOLD, --length_threshold LENGTH_THRESHOLD
                        specify the sequence length threshold, contigs shorter
                        than this value will not be included. Defaults to
                        1000.
  -r READ_LENGTH, --read_length READ_LENGTH
                        specify read length for coverage, default 100
  --total_percentage_pca TOTAL_PERCENTAGE_PCA
                        The percentage of variance explained by the principal
                        components for the combined data.
  -b BASENAME, --basename BASENAME
                        Specify the basename for files or directory where
                        outputwill be placed. Path to existing directory or
                        basenamewith a trailing '/' will be interpreted as a
                        directory.If not provided, current directory will be
                        used.
  -s SEED, --seed SEED  Specify an integer to use as seed for clustering. 0
                        gives a random seed, 1 is the default seed and any
                        other positive integer can be used. Other values give
                        ArgumentTypeError.
  -i ITERATIONS, --iterations ITERATIONS
                        Specify maximum number of iterations for the VBGMM.
                        Default value is 500
  --no_cov_normalization
                        By default the coverage is normalized with regards to
                        samples, then normalized with regards of contigs and
                        finally log transformed. By setting this flag you skip
                        the normalization and only do log transorm of the
                        coverage.
  --no_total_coverage   By default, the total coverage is added as a new
                        column in the coverage data matrix, independently of
                        coverage normalization but previous to log
                        transformation. Use this tag to escape this behaviour.
  --no_original_data    By default the original data is saved to disk. For big
                        datasets, especially when a large k is used for
                        compositional data, this file can become very large.
                        Use this tag if you don't want to save the original
                        data.
  -o, --converge_out    Write convergence info to files.
  -d, --debug           Debug parameters.
  -v, --version         show program's version number and exit

## 12. MaxBin2
========================================
docker: Error response from daemon: failed to create task for container: failed to create shim task: OCI runtime create failed: runc create failed: unable to start container process: exec: "run_MaxBin.sh": executable file not found in $PATH: unknown.

## 13. DAS Tool
========================================
DAS Tool

Usage:
  DAS_Tool [options] -i <contig2bin> -c <contigs_fasta> -o <outputbasename>
  DAS_Tool -i <contig2bin> -c <contigs_fasta> -o <outputbasename> [--labels=<labels>] [--proteins=<proteins_fasta>] [--threads=<threads>] [--search_engine=<search_engine>] [--score_threshold=<score_threshold>] [--dbDirectory=<dbDirectory> ] [--megabin_penalty=<megabin_penalty>] [--duplicate_penalty=<duplicate_penalty>] [--write_bin_evals] [--write_bins] [--write_unbinned] [--resume] [--debug]
  DAS_Tool [--version]
  DAS_Tool [--help]

Options:
   -i --bins=<contig2bin>                   Comma separated list of tab separated contigs to bin tables.
   -c --contigs=<contigs>                   Contigs in fasta format.
   -o --outputbasename=<outputbasename>     Basename of output files.
   -l --labels=<labels>                     Comma separated list of binning prediction names.
   --search_engine=<search_engine>          Engine used for single copy gene identification (diamond/blastp/usearch) [default: diamond].
   -p --proteins=<proteins>                 Predicted proteins (optional) in prodigal fasta format (>contigID_geneNo).
                                            Gene prediction step will be skipped.
   --write_bin_evals                        Write evaluation of input bin sets.
   --write_bins                             Export bins as fasta files.
   --write_unbinned                         Write unbinned contigs.
   -t --threads=<threads>                   Number of threads to use [default: 1].
   --score_threshold=<score_threshold>      Score threshold until selection algorithm will keep selecting bins (0..1) [default: 0.5].
   --duplicate_penalty=<duplicate_penalty>  Penalty for duplicate single copy genes per bin (weight b).
                                            Only change if you know what you are doing (0..3) [default: 0.6].
   --megabin_penalty=<megabin_penalty>      Penalty for megabins (weight c). Only change if you know what you are doing (0..3) [default: 0.5].
   --dbDirectory=<dbDirectory>              Directory of single copy gene database [default: db].
   --resume                                 Use existing predicted single copy gene files from a previous run.
   --debug                                  Write debug information to log file.
   -v --version                             Print version number and exit.
   -h --help                                Show this.


Please cite: Sieber et al., 2018, Nature Microbiology (https://doi.org/10.1038/s41564-018-0171-1). 

## 14. CheckM
========================================
usage: checkm lineage_wf [-h] [-r] [--ali] [--nt] [-g] [-u UNIQUE] [-m MULTI]
                         [--force_domain] [--no_refinement]
                         [--individual_markers] [--skip_adj_correction]
                         [--skip_pseudogene_correction]
                         [--aai_strain AAI_STRAIN] [-a ALIGNMENT_FILE]
                         [--ignore_thresholds] [-e E_VALUE] [-l LENGTH]
                         [-f FILE] [--tab_table] [-x EXTENSION] [-t THREADS]
                         [--pplacer_threads PPLACER_THREADS] [-q]
                         [--tmpdir TMPDIR]
                         bin_input output_dir

Runs tree, lineage_set, analyze, qa

positional arguments:
  bin_input             directory containing bins (fasta format) or path to file describing genomes/genes - tab separated in 2 or 3 columns [genome ID, genome fna, genome translation file (pep)]
  output_dir            directory to write output files

optional arguments:
  -h, --help            show this help message and exit
  -r, --reduced_tree    use reduced tree (requires <16GB of memory) for determining lineage of each bin
  --ali                 generate HMMER alignment file for each bin
  --nt                  generate nucleotide gene sequences for each bin
  -g, --genes           bins contain genes as amino acids instead of nucleotide contigs
  -u, --unique UNIQUE   minimum number of unique phylogenetic markers required to use lineage-specific marker set (default: 10)
  -m, --multi MULTI     maximum number of multi-copy phylogenetic markers before defaulting to domain-level marker set (default: 10)
  --force_domain        use domain-level sets for all bins
  --no_refinement       do not perform lineage-specific marker set refinement
  --individual_markers  treat marker as independent (i.e., ignore co-located set structure)
  --skip_adj_correction
                        do not exclude adjacent marker genes when estimating contamination
  --skip_pseudogene_correction
                        skip identification and filtering of pseudogenes
  --aai_strain AAI_STRAIN
                        AAI threshold used to identify strain heterogeneity (default: 0.9)
  -a, --alignment_file ALIGNMENT_FILE
                        produce file showing alignment of multi-copy genes and their AAI identity
  --ignore_thresholds   ignore model-specific score thresholds
  -e, --e_value E_VALUE
                        e-value cut off (default: 1e-10)
  -l, --length LENGTH   percent overlap between target and query (default: 0.7)
  -f, --file FILE       print results to file (default: stdout)
  --tab_table           print tab-separated values table
  -x, --extension EXTENSION
                        extension of bins (other files in directory are ignored) (default: fna)
  -t, --threads THREADS
                        number of threads (default: 1)
  --pplacer_threads PPLACER_THREADS
                        number of threads used by pplacer (memory usage increases linearly with additional threads) (default: 1)
  -q, --quiet           suppress console output
  --tmpdir TMPDIR       specify an alternative directory for temporary files

Example: checkm lineage_wf ./bins ./output

## 15. GTDB-Tk
========================================
/usr/local/env-execute: line 3: exec: gtbtk: not found

## 16. VIBRANT
========================================
/usr/local/env-execute: line 3: exec: vibrant.py: not found

## 17. VirSorter2
========================================
[2026-01-07 13:30 INFO] VirSorter 2.2.4
[2026-01-07 13:30 INFO] /usr/local/bin/virsorter --help
Usage: virsorter [OPTIONS] COMMAND [ARGS]...

  virsorter - workflow for identifying viral sequences

Options:
  --version   Show the version and exit.
  -h, --help  Show this message and exit.

Commands:
  config         subcommand for configuration management
  run            run virsorter main workflow
  setup          download reference files (~10GB) and install dependencies
  train-feature  subcommand for training feature of customized classifier
  train-model    subcommand for training customized classifier model

## 18. Cenote-Taker3
========================================
/usr/local/env-execute: line 3: exec: Cenote-Taker3: not found

## 19. Snakemake
========================================
usage: snakemake [-h] [--dry-run] [--profile PROFILE]
                 [--cache [RULE [RULE ...]]] [--snakefile FILE] [--cores [N]]
                 [--local-cores N] [--resources [NAME=INT [NAME=INT ...]]]
                 [--set-threads RULE=THREADS [RULE=THREADS ...]]
                 [--set-scatter NAME=SCATTERITEMS [NAME=SCATTERITEMS ...]]
                 [--default-resources [NAME=INT [NAME=INT ...]]]
                 [--preemption-default PREEMPTION_DEFAULT]
                 [--preemptible-rules PREEMPTIBLE_RULES [PREEMPTIBLE_RULES ...]]
                 [--config [KEY=VALUE [KEY=VALUE ...]]]
                 [--configfile FILE [FILE ...]]
                 [--envvars VARNAME [VARNAME ...]] [--directory DIR] [--touch]
                 [--keep-going] [--force] [--forceall]
                 [--forcerun [TARGET [TARGET ...]]]
                 [--prioritize TARGET [TARGET ...]]
                 [--batch RULE=BATCH/BATCHES] [--until TARGET [TARGET ...]]
                 [--omit-from TARGET [TARGET ...]] [--rerun-incomplete]
                 [--shadow-prefix DIR] [--scheduler [{ilp,greedy}]]
                 [--scheduler-ilp-solver {COIN_CMD}]
                 [--groups GROUPS [GROUPS ...]]
                 [--group-components GROUP_COMPONENTS [GROUP_COMPONENTS ...]]
                 [--report [FILE]] [--report-stylesheet CSSFILE]
                 [--edit-notebook TARGET] [--notebook-listen IP:PORT]
                 [--lint [{text,json}]] [--export-cwl FILE] [--list]
                 [--list-target-rules] [--dag] [--rulegraph] [--filegraph]
                 [--d3dag] [--summary] [--detailed-summary] [--archive FILE]
                 [--cleanup-metadata FILE [FILE ...]] [--cleanup-shadow]
                 [--skip-script-cleanup] [--unlock] [--list-version-changes]
                 [--list-code-changes] [--list-input-changes]
                 [--list-params-changes] [--list-untracked]
                 [--delete-all-output] [--delete-temp-output]
                 [--bash-completion] [--keep-incomplete] [--version]
                 [--reason] [--gui [PORT]] [--printshellcmds] [--debug-dag]
                 [--stats FILE] [--nocolor] [--quiet] [--print-compilation]
                 [--verbose] [--force-use-threads] [--allow-ambiguity]
                 [--nolock] [--ignore-incomplete]
                 [--max-inventory-time SECONDS] [--latency-wait SECONDS]
                 [--wait-for-files [FILE [FILE ...]]] [--notemp]
                 [--keep-remote] [--keep-target-files]
                 [--allowed-rules ALLOWED_RULES [ALLOWED_RULES ...]]
                 [--max-jobs-per-second MAX_JOBS_PER_SECOND]
                 [--max-status-checks-per-second MAX_STATUS_CHECKS_PER_SECOND]
                 [-T RESTART_TIMES] [--attempt ATTEMPT]
                 [--wrapper-prefix WRAPPER_PREFIX]
                 [--default-remote-provider {S3,GS,FTP,SFTP,S3Mocked,gfal,gridftp,iRODS,AzBlob}]
                 [--default-remote-prefix DEFAULT_REMOTE_PREFIX]
                 [--no-shared-fs] [--greediness GREEDINESS] [--no-hooks]
                 [--overwrite-shellcmd OVERWRITE_SHELLCMD] [--debug]
                 [--runtime-profile FILE] [--mode {0,1,2}]
                 [--show-failed-logs] [--log-handler-script FILE]
                 [--log-service {none,slack}]
                 [--cluster CMD | --cluster-sync CMD | --drmaa [ARGS]]
                 [--cluster-config FILE] [--immediate-submit]
                 [--jobscript SCRIPT] [--jobname NAME]
                 [--cluster-status CLUSTER_STATUS] [--drmaa-log-dir DIR]
                 [--kubernetes [NAMESPACE]] [--container-image IMAGE]
                 [--tibanna] [--tibanna-sfn TIBANNA_SFN]
                 [--precommand PRECOMMAND]
                 [--tibanna-config TIBANNA_CONFIG [TIBANNA_CONFIG ...]]
                 [--google-lifesciences]
                 [--google-lifesciences-regions GOOGLE_LIFESCIENCES_REGIONS [GOOGLE_LIFESCIENCES_REGIONS ...]]
                 [--google-lifesciences-location GOOGLE_LIFESCIENCES_LOCATION]
                 [--google-lifesciences-keep-cache] [--use-conda]
                 [--list-conda-envs] [--conda-prefix DIR]
                 [--conda-cleanup-envs]
                 [--conda-cleanup-pkgs [{tarballs,cache}]]
                 [--conda-create-envs-only] [--conda-frontend {conda,mamba}]
                 [--use-singularity] [--singularity-prefix DIR]
                 [--singularity-args ARGS] [--use-envmodules]
                 [target [target ...]]

Snakemake is a Python based language and execution environment for GNU Make-
like workflows.

optional arguments:
  -h, --help            show this help message and exit

EXECUTION:
  target                Targets to build. May be rules or files. (default:
                        None)
  --dry-run, --dryrun, -n
                        Do not execute anything, and display what would be
                        done. If you have a very large workflow, use --dry-run
                        --quiet to just print a summary of the DAG of jobs.
                        (default: False)
  --profile PROFILE     Name of profile to use for configuring Snakemake.
                        Snakemake will search for a corresponding folder in
                        /etc/xdg/snakemake and /root/.config/snakemake.
                        Alternatively, this can be an absolute or relative
                        path. The profile folder has to contain a file
                        'config.yaml'. This file can be used to set default
                        values for command line options in YAML format. For
                        example, '--cluster qsub' becomes 'cluster: qsub' in
                        the YAML file. Profiles can be obtained from
                        https://github.com/snakemake-profiles. (default: None)
  --cache [RULE [RULE ...]]
                        Store output files of given rules in a central cache
                        given by the environment variable
                        $SNAKEMAKE_OUTPUT_CACHE. Likewise, retrieve output
                        files of the given rules from this cache if they have
                        been created before (by anybody writing to the same
                        cache), instead of actually executing the rules.
                        Output files are identified by hashing all steps,
                        parameters and software stack (conda envs or
                        containers) needed to create them. (default: None)
  --snakefile FILE, -s FILE
                        The workflow definition in form of a
                        snakefile.Usually, you should not need to specify
                        this. By default, Snakemake will search for
                        'Snakefile', 'snakefile', 'workflow/Snakefile',
                        'workflow/snakefile' beneath the current working
                        directory, in this order. Only if you definitely want
                        a different layout, you need to use this parameter.
                        (default: None)
  --cores [N], --jobs [N], -j [N]
                        Use at most N CPU cores/jobs in parallel. If N is
                        omitted or 'all', the limit is set to the number of
                        available CPU cores. (default: None)
  --local-cores N       In cluster mode, use at most N cores of the host
                        machine in parallel (default: number of CPU cores of
                        the host). The cores are used to execute local rules.
                        This option is ignored when not in cluster mode.
                        (default: 64)
  --resources [NAME=INT [NAME=INT ...]], --res [NAME=INT [NAME=INT ...]]
                        Define additional resources that shall constrain the
                        scheduling analogously to threads (see above). A
                        resource is defined as a name and an integer value.
                        E.g. --resources mem_mb=1000. Rules can use resources
                        by defining the resource keyword, e.g. resources:
                        mem_mb=600. If now two rules require 600 of the
                        resource 'mem_mb' they won't be run in parallel by the
                        scheduler. (default: None)
  --set-threads RULE=THREADS [RULE=THREADS ...]
                        Overwrite thread usage of rules. This allows to fine-
                        tune workflow parallelization. In particular, this is
                        helpful to target certain cluster nodes by e.g.
                        shifting a rule to use more, or less threads than
                        defined in the workflow. Thereby, THREADS has to be a
                        positive integer, and RULE has to be the name of the
                        rule. (default: None)
  --set-scatter NAME=SCATTERITEMS [NAME=SCATTERITEMS ...]
                        Overwrite number of scatter items of scattergather
                        processes. This allows to fine-tune workflow
                        parallelization. Thereby, SCATTERITEMS has to be a
                        positive integer, and NAME has to be the name of the
                        scattergather process defined via a scattergather
                        directive in the workflow. (default: None)
  --default-resources [NAME=INT [NAME=INT ...]], --default-res [NAME=INT [NAME=INT ...]]
                        Define default values of resources for rules that do
                        not define their own values. In addition to plain
                        integers, python expressions over inputsize are
                        allowed (e.g. '2*input.size_mb').When specifying this
                        without any arguments (--default-resources), it
                        defines 'mem_mb=max(2*input.size_mb, 1000)'
                        'disk_mb=max(2*input.size_mb, 1000)', i.e., default
                        disk and mem usage is twice the input file size but at
                        least 1GB. (default: None)
  --preemption-default PREEMPTION_DEFAULT
                        A preemptible instance can be requested when using the
                        Google Life Sciences API. If you set a --preemption-
                        default,all rules will be subject to the default.
                        Specifically, this integer is the number of restart
                        attempts that will be made given that the instance is
                        killed unexpectedly. Note that preemptible instances
                        have a maximum running time of 24 hours. If you want
                        to set preemptible instances for only a subset of
                        rules, use --preemptible-rules instead. (default:
                        None)
  --preemptible-rules PREEMPTIBLE_RULES [PREEMPTIBLE_RULES ...]
                        A preemptible instance can be requested when using the
                        Google Life Sciences API. If you want to use these
                        instances for a subset of your rules, you can use
                        --preemptible-rules and then specify a list of rule
                        and integer pairs, where each integer indicates the
                        number of restarts to use for the rule's instance in
                        the case that the instance is terminated unexpectedly.
                        --preemptible-rules can be used in combination with
                        --preemption-default, and will take priority. Note
                        that preemptible instances have a maximum running time
                        of 24. If you want to apply a consistent number of
                        retries across all your rules, use --premption-default
                        instead. Example: snakemake --preemption-default 10
                        --preemptible-rules map_reads=3 call_variants=0
                        (default: None)
  --config [KEY=VALUE [KEY=VALUE ...]], -C [KEY=VALUE [KEY=VALUE ...]]
                        Set or overwrite values in the workflow config object.
                        The workflow config object is accessible as variable
                        config inside the workflow. Default values can be set
                        by providing a JSON file (see Documentation).
                        (default: None)
  --configfile FILE [FILE ...], --configfiles FILE [FILE ...]
                        Specify or overwrite the config file of the workflow
                        (see the docs). Values specified in JSON or YAML
                        format are available in the global config dictionary
                        inside the workflow. Multiple files overwrite each
                        other in the given order. (default: None)
  --envvars VARNAME [VARNAME ...]
                        Environment variables to pass to cloud jobs. (default:
                        None)
  --directory DIR, -d DIR
                        Specify working directory (relative paths in the
                        snakefile will use this as their origin). (default:
                        None)
  --touch, -t           Touch output files (mark them up to date without
                        really changing them) instead of running their
                        commands. This is used to pretend that the rules were
                        executed, in order to fool future invocations of
                        snakemake. Fails if a file does not yet exist. Note
                        that this will only touch files that would otherwise
                        be recreated by Snakemake (e.g. because their input
                        files are newer). For enforcing a touch, combine this
                        with --force, --forceall, or --forcerun. Note however
                        that you loose the provenance information when the
                        files have been created in realitiy. Hence, this
                        should be used only as a last resort. (default: False)
  --keep-going, -k      Go on with independent jobs if a job fails. (default:
                        False)
  --force, -f           Force the execution of the selected target or the
                        first rule regardless of already created output.
                        (default: False)
  --forceall, -F        Force the execution of the selected (or the first)
                        rule and all rules it is dependent on regardless of
                        already created output. (default: False)
  --forcerun [TARGET [TARGET ...]], -R [TARGET [TARGET ...]]
                        Force the re-execution or creation of the given rules
                        or files. Use this option if you changed a rule and
                        want to have all its output in your workflow updated.
                        (default: None)
  --prioritize TARGET [TARGET ...], -P TARGET [TARGET ...]
                        Tell the scheduler to assign creation of given targets
                        (and all their dependencies) highest priority.
                        (EXPERIMENTAL) (default: None)
  --batch RULE=BATCH/BATCHES
                        Only create the given BATCH of the input files of the
                        given RULE. This can be used to iteratively run parts
                        of very large workflows. Only the execution plan of
                        the relevant part of the workflow has to be
                        calculated, thereby speeding up DAG computation. It is
                        recommended to provide the most suitable rule for
                        batching when documenting a workflow. It should be
                        some aggregating rule that would be executed only
                        once, and has a large number of input files. For
                        example, it can be a rule that aggregates over
                        samples. (default: None)
  --until TARGET [TARGET ...], -U TARGET [TARGET ...]
                        Runs the pipeline until it reaches the specified rules
                        or files. Only runs jobs that are dependencies of the
                        specified rule or files, does not run sibling DAGs.
                        (default: None)
  --omit-from TARGET [TARGET ...], -O TARGET [TARGET ...]
                        Prevent the execution or creation of the given rules
                        or files as well as any rules or files that are
                        downstream of these targets in the DAG. Also runs jobs
                        in sibling DAGs that are independent of the rules or
                        files specified here. (default: None)
  --rerun-incomplete, --ri
                        Re-run all jobs the output of which is recognized as
                        incomplete. (default: False)
  --shadow-prefix DIR   Specify a directory in which the 'shadow' directory is
                        created. If not supplied, the value is set to the
                        '.snakemake' directory relative to the working
                        directory. (default: None)
  --scheduler [{ilp,greedy}]
                        Specifies if jobs are selected by a greedy algorithm
                        or by solving an ilp. The ilp scheduler aims to reduce
                        runtime and hdd usage by best possible use of
                        resources. (default: ilp)
  --scheduler-ilp-solver {COIN_CMD}
                        Specifies solver to be utilized when selecting ilp-
                        scheduler. (default: None)

GROUPING:
  --groups GROUPS [GROUPS ...]
                        Assign rules to groups (this overwrites any group
                        definitions from the workflow). (default: None)
  --group-components GROUP_COMPONENTS [GROUP_COMPONENTS ...]
                        Set the number of connected components a group is
                        allowed to span. By default, this is 1, but this flag
                        allows to extend this. This can be used to run e.g. 3
                        jobs of the same rule in the same group, although they
                        are not connected. It can be helpful for putting
                        together many small jobs or benefitting of shared
                        memory setups. (default: None)

REPORTS:
  --report [FILE]       Create an HTML report with results and statistics.
                        This can be either a .html file or a .zip file. In the
                        former case, all results are embedded into the .html
                        (this only works for small data). In the latter case,
                        results are stored along with a file report.html in
                        the zip archive. If no filename is given, an embedded
                        report.html is the default. (default: None)
  --report-stylesheet CSSFILE
                        Custom stylesheet to use for report. In particular,
                        this can be used for branding the report with e.g. a
                        custom logo, see docs. (default: None)

NOTEBOOKS:
  --edit-notebook TARGET
                        Interactively edit the notebook associated with the
                        rule used to generate the given target file. This will
                        start a local jupyter notebook server. Any changes to
                        the notebook should be saved, and the server has to be
                        stopped by closing the notebook and hitting the 'Quit'
                        button on the jupyter dashboard. Afterwards, the
                        updated notebook will be automatically stored in the
                        path defined in the rule. If the notebook is not yet
                        present, this will create an empty draft. (default:
                        None)
  --notebook-listen IP:PORT
                        The IP address and PORT the notebook server used for
                        editing the notebook (--edit-notebook) will listen on.
                        (default: localhost:8888)

UTILITIES:
  --lint [{text,json}]  Perform linting on the given workflow. This will print
                        snakemake specific suggestions to improve code quality
                        (work in progress, more lints to be added in the
                        future). If no argument is provided, plain text output
                        is used. (default: None)
  --export-cwl FILE     Compile workflow to CWL and store it in given FILE.
                        (default: None)
  --list, -l            Show available rules in given Snakefile. (default:
                        False)
  --list-target-rules, --lt
                        Show available target rules in given Snakefile.
                        (default: False)
  --dag                 Do not execute anything and print the directed acyclic
                        graph of jobs in the dot language. Recommended use on
                        Unix systems: snakemake --dag | dot | displayNote
                        print statements in your Snakefile may interfere with
                        visualization. (default: False)
  --rulegraph           Do not execute anything and print the dependency graph
                        of rules in the dot language. This will be less
                        crowded than above DAG of jobs, but also show less
                        information. Note that each rule is displayed once,
                        hence the displayed graph will be cyclic if a rule
                        appears in several steps of the workflow. Use this if
                        above option leads to a DAG that is too large.
                        Recommended use on Unix systems: snakemake --rulegraph
                        | dot | displayNote print statements in your Snakefile
                        may interfere with visualization. (default: False)
  --filegraph           Do not execute anything and print the dependency graph
                        of rules with their input and output files in the dot
                        language. This is an intermediate solution between
                        above DAG of jobs and the rule graph. Note that each
                        rule is displayed once, hence the displayed graph will
                        be cyclic if a rule appears in several steps of the
                        workflow. Use this if above option leads to a DAG that
                        is too large. Recommended use on Unix systems:
                        snakemake --filegraph | dot | displayNote print
                        statements in your Snakefile may interfere with
                        visualization. (default: False)
  --d3dag               Print the DAG in D3.js compatible JSON format.
                        (default: False)
  --summary, -S         Print a summary of all files created by the workflow.
                        The has the following columns: filename, modification
                        time, rule version, status, plan. Thereby rule version
                        contains the versionthe file was created with (see the
                        version keyword of rules), and status denotes whether
                        the file is missing, its input files are newer or if
                        version or implementation of the rule changed since
                        file creation. Finally the last column denotes whether
                        the file will be updated or created during the next
                        workflow execution. (default: False)
  --detailed-summary, -D
                        Print a summary of all files created by the workflow.
                        The has the following columns: filename, modification
                        time, rule version, input file(s), shell command,
                        status, plan. Thereby rule version contains the
                        version the file was created with (see the version
                        keyword of rules), and status denotes whether the file
                        is missing, its input files are newer or if version or
                        implementation of the rule changed since file
                        creation. The input file and shell command columns are
                        self explanatory. Finally the last column denotes
                        whether the file will be updated or created during the
                        next workflow execution. (default: False)
  --archive FILE        Archive the workflow into the given tar archive FILE.
                        The archive will be created such that the workflow can
                        be re-executed on a vanilla system. The function needs
                        conda and git to be installed. It will archive every
                        file that is under git version control. Note that it
                        is best practice to have the Snakefile, config files,
                        and scripts under version control. Hence, they will be
                        included in the archive. Further, it will add input
                        files that are not generated by by the workflow itself
                        and conda environments. Note that symlinks are
                        dereferenced. Supported formats are .tar, .tar.gz,
                        .tar.bz2 and .tar.xz. (default: None)
  --cleanup-metadata FILE [FILE ...], --cm FILE [FILE ...]
                        Cleanup the metadata of given files. That means that
                        snakemake removes any tracked version info, and any
                        marks that files are incomplete. (default: None)
  --cleanup-shadow      Cleanup old shadow directories which have not been
                        deleted due to failures or power loss. (default:
                        False)
  --skip-script-cleanup
                        Don't delete wrapper scripts used for execution
                        (default: False)
  --unlock              Remove a lock on the working directory. (default:
                        False)
  --list-version-changes, --lv
                        List all output files that have been created with a
                        different version (as determined by the version
                        keyword). (default: False)
  --list-code-changes, --lc
                        List all output files for which the rule body (run or
                        shell) have changed in the Snakefile. (default: False)
  --list-input-changes, --li
                        List all output files for which the defined input
                        files have changed in the Snakefile (e.g. new input
                        files were added in the rule definition or files were
                        renamed). For listing input file modification in the
                        filesystem, use --summary. (default: False)
  --list-params-changes, --lp
                        List all output files for which the defined params
                        have changed in the Snakefile. (default: False)
  --list-untracked, --lu
                        List all files in the working directory that are not
                        used in the workflow. This can be used e.g. for
                        identifying leftover files. Hidden files and
                        directories are ignored. (default: False)
  --delete-all-output   Remove all files generated by the workflow. Use
                        together with --dry-run to list files without actually
                        deleting anything. Note that this will not recurse
                        into subworkflows. Write-protected files are not
                        removed. Nevertheless, use with care! (default: False)
  --delete-temp-output  Remove all temporary files generated by the workflow.
                        Use together with --dry-run to list files without
                        actually deleting anything. Note that this will not
                        recurse into subworkflows. (default: False)
  --bash-completion     Output code to register bash completion for snakemake.
                        Put the following in your .bashrc (including the
                        accents): `snakemake --bash-completion` or issue it in
                        an open terminal session. (default: False)
  --keep-incomplete     Do not remove incomplete output files by failed jobs.
                        (default: False)
  --version, -v         show program's version number and exit

OUTPUT:
  --reason, -r          Print the reason for each executed rule. (default:
                        False)
  --gui [PORT]          Serve an HTML based user interface to the given
                        network and port e.g. 168.129.10.15:8000. By default
                        Snakemake is only available in the local network
                        (default port: 8000). To make Snakemake listen to all
                        ip addresses add the special host address 0.0.0.0 to
                        the url (0.0.0.0:8000). This is important if Snakemake
                        is used in a virtualised environment like Docker. If
                        possible, a browser window is opened. (default: None)
  --printshellcmds, -p  Print out the shell commands that will be executed.
                        (default: False)
  --debug-dag           Print candidate and selected jobs (including their
                        wildcards) while inferring DAG. This can help to debug
                        unexpected DAG topology or errors. (default: False)
  --stats FILE          Write stats about Snakefile execution in JSON format
                        to the given file. (default: None)
  --nocolor             Do not use a colored output. (default: False)
  --quiet, -q           Do not output any progress or rule information.
                        (default: False)
  --print-compilation   Print the python representation of the workflow.
                        (default: False)
  --verbose             Print debugging output. (default: False)

BEHAVIOR:
  --force-use-threads   Force threads rather than processes. Helpful if shared
                        memory (/dev/shm) is full or unavailable. (default:
                        False)
  --allow-ambiguity, -a
                        Don't check for ambiguous rules and simply use the
                        first if several can produce the same file. This
                        allows the user to prioritize rules by their order in
                        the snakefile. (default: False)
  --nolock              Do not lock the working directory (default: False)
  --ignore-incomplete, --ii
                        Do not check for incomplete output files. (default:
                        False)
  --max-inventory-time SECONDS
                        Spend at most SECONDS seconds to create a file
                        inventory for the working directory. The inventory
                        vastly speeds up file modification and existence
                        checks when computing which jobs need to be executed.
                        However, creating the inventory itself can be slow,
                        e.g. on network file systems. Hence, we do not spend
                        more than a given amount of time and fall back to
                        individual checks for the rest. (default: 20)
  --latency-wait SECONDS, --output-wait SECONDS, -w SECONDS
                        Wait given seconds if an output file of a job is not
                        present after the job finished. This helps if your
                        filesystem suffers from latency (default 5). (default:
                        5)
  --wait-for-files [FILE [FILE ...]]
                        Wait --latency-wait seconds for these files to be
                        present before executing the workflow. This option is
                        used internally to handle filesystem latency in
                        cluster environments. (default: None)
  --notemp, --nt        Ignore temp() declarations. This is useful when
                        running only a part of the workflow, since temp()
                        would lead to deletion of probably needed files by
                        other parts of the workflow. (default: False)
  --keep-remote         Keep local copies of remote input files. (default:
                        False)
  --keep-target-files   Do not adjust the paths of given target files relative
                        to the working directory. (default: False)
  --allowed-rules ALLOWED_RULES [ALLOWED_RULES ...]
                        Only consider given rules. If omitted, all rules in
                        Snakefile are used. Note that this is intended
                        primarily for internal use and may lead to unexpected
                        results otherwise. (default: None)
  --max-jobs-per-second MAX_JOBS_PER_SECOND
                        Maximal number of cluster/drmaa jobs per second,
                        default is 10, fractions allowed. (default: 10)
  --max-status-checks-per-second MAX_STATUS_CHECKS_PER_SECOND
                        Maximal number of job status checks per second,
                        default is 10, fractions allowed. (default: 10)
  -T RESTART_TIMES, --restart-times RESTART_TIMES
                        Number of times to restart failing jobs (defaults to
                        0). (default: 0)
  --attempt ATTEMPT     Internal use only: define the initial value of the
                        attempt parameter (default: 1). (default: 1)
  --wrapper-prefix WRAPPER_PREFIX
                        Prefix for URL created from wrapper directive
                        (default: https://github.com/snakemake/snakemake-
                        wrappers/raw/). Set this to a different URL to use
                        your fork or a local clone of the repository, e.g.,
                        use a git URL like
                        'git+file://path/to/your/local/clone@'. (default:
                        https://github.com/snakemake/snakemake-wrappers/raw/)
  --default-remote-provider {S3,GS,FTP,SFTP,S3Mocked,gfal,gridftp,iRODS,AzBlob}
                        Specify default remote provider to be used for all
                        input and output files that don't yet specify one.
                        (default: None)
  --default-remote-prefix DEFAULT_REMOTE_PREFIX
                        Specify prefix for default remote provider. E.g. a
                        bucket name. (default: )
  --no-shared-fs        Do not assume that jobs share a common file system.
                        When this flag is activated, Snakemake will assume
                        that the filesystem on a cluster node is not shared
                        with other nodes. For example, this will lead to
                        downloading remote files on each cluster node
                        separately. Further, it won't take special measures to
                        deal with filesystem latency issues. This option will
                        in most cases only make sense in combination with
                        --default-remote-provider. Further, when using
                        --cluster you will have to also provide --cluster-
                        status. Only activate this if you know what you are
                        doing. (default: False)
  --greediness GREEDINESS
                        Set the greediness of scheduling. This value between 0
                        and 1 determines how careful jobs are selected for
                        execution. The default value (1.0) provides the best
                        speed and still acceptable scheduling quality.
                        (default: None)
  --no-hooks            Do not invoke onstart, onsuccess or onerror hooks
                        after execution. (default: False)
  --overwrite-shellcmd OVERWRITE_SHELLCMD
                        Provide a shell command that shall be executed instead
                        of those given in the workflow. This is for debugging
                        purposes only. (default: None)
  --debug               Allow to debug rules with e.g. PDB. This flag allows
                        to set breakpoints in run blocks. (default: False)
  --runtime-profile FILE
                        Profile Snakemake and write the output to FILE. This
                        requires yappi to be installed. (default: None)
  --mode {0,1,2}        Set execution mode of Snakemake (internal use only).
                        (default: 0)
  --show-failed-logs    Automatically display logs of failed jobs. (default:
                        False)
  --log-handler-script FILE
                        Provide a custom script containing a function 'def
                        log_handler(msg):'. Snakemake will call this function
                        for every logging output (given as a dictionary
                        msg)allowing to e.g. send notifications in the form of
                        e.g. slack messages or emails. (default: None)
  --log-service {none,slack}
                        Set a specific messaging service for logging
                        output.Snakemake will notify the service on errors and
                        completed execution.Currently only slack is supported.
                        (default: None)

CLUSTER:
  --cluster CMD, -c CMD
                        Execute snakemake rules with the given submit command,
                        e.g. qsub. Snakemake compiles jobs into scripts that
                        are submitted to the cluster with the given command,
                        once all input files for a particular job are present.
                        The submit command can be decorated to make it aware
                        of certain job properties (name, rulename, input,
                        output, params, wildcards, log, threads and
                        dependencies (see the argument below)), e.g.: $
                        snakemake --cluster 'qsub -pe threaded {threads}'.
                        (default: None)
  --cluster-sync CMD    cluster submission command will block, returning the
                        remote exitstatus upon remote termination (for
                        example, this should be usedif the cluster command is
                        'qsub -sync y' (SGE) (default: None)
  --drmaa [ARGS]        Execute snakemake on a cluster accessed via DRMAA,
                        Snakemake compiles jobs into scripts that are
                        submitted to the cluster with the given command, once
                        all input files for a particular job are present. ARGS
                        can be used to specify options of the underlying
                        cluster system, thereby using the job properties name,
                        rulename, input, output, params, wildcards, log,
                        threads and dependencies, e.g.: --drmaa ' -pe threaded
                        {threads}'. Note that ARGS must be given in quotes and
                        with a leading whitespace. (default: None)
  --cluster-config FILE, -u FILE
                        A JSON or YAML file that defines the wildcards used in
                        'cluster'for specific rules, instead of having them
                        specified in the Snakefile. For example, for rule
                        'job' you may define: { 'job' : { 'time' : '24:00:00'
                        } } to specify the time for rule 'job'. You can
                        specify more than one file. The configuration files
                        are merged with later values overriding earlier ones.
                        This option is deprecated in favor of using --profile,
                        see docs. (default: [])
  --immediate-submit, --is
                        Immediately submit all jobs to the cluster instead of
                        waiting for present input files. This will fail,
                        unless you make the cluster aware of job dependencies,
                        e.g. via: $ snakemake --cluster 'sbatch --dependency
                        {dependencies}. Assuming that your submit script (here
                        sbatch) outputs the generated job id to the first
                        stdout line, {dependencies} will be filled with space
                        separated job ids this job depends on. (default:
                        False)
  --jobscript SCRIPT, --js SCRIPT
                        Provide a custom job script for submission to the
                        cluster. The default script resides as 'jobscript.sh'
                        in the installation directory. (default: None)
  --jobname NAME, --jn NAME
                        Provide a custom name for the jobscript that is
                        submitted to the cluster (see --cluster). NAME is
                        "snakejob.{name}.{jobid}.sh" per default. The wildcard
                        {jobid} has to be present in the name. (default:
                        snakejob.{name}.{jobid}.sh)
  --cluster-status CLUSTER_STATUS
                        Status command for cluster execution. This is only
                        considered in combination with the --cluster flag. If
                        provided, Snakemake will use the status command to
                        determine if a job has finished successfully or
                        failed. For this it is necessary that the submit
                        command provided to --cluster returns the cluster job
                        id. Then, the status command will be invoked with the
                        job id. Snakemake expects it to return 'success' if
                        the job was successfull, 'failed' if the job failed
                        and 'running' if the job still runs. (default: None)
  --drmaa-log-dir DIR   Specify a directory in which stdout and stderr files
                        of DRMAA jobs will be written. The value may be given
                        as a relative path, in which case Snakemake will use
                        the current invocation directory as the origin. If
                        given, this will override any given '-o' and/or '-e'
                        native specification. If not given, all DRMAA stdout
                        and stderr files are written to the current working
                        directory. (default: None)

KUBERNETES:
  --kubernetes [NAMESPACE]
                        Execute workflow in a kubernetes cluster (in the
                        cloud). NAMESPACE is the namespace you want to use for
                        your job (if nothing specified: 'default'). Usually,
                        this requires --default-remote-provider and --default-
                        remote-prefix to be set to a S3 or GS bucket where
                        your . data shall be stored. It is further advisable
                        to activate conda integration via --use-conda.
                        (default: None)
  --container-image IMAGE
                        Docker image to use, e.g., when submitting jobs to
                        kubernetes Defaults to
                        'https://hub.docker.com/r/snakemake/snakemake', tagged
                        with the same version as the currently running
                        Snakemake instance. Note that overwriting this value
                        is up to your responsibility. Any used image has to
                        contain a working snakemake installation that is
                        compatible with (or ideally the same as) the currently
                        running version. (default: None)

TIBANNA:
  --tibanna             Execute workflow on AWS cloud using Tibanna. This
                        requires --default-remote-prefix to be set to S3
                        bucket name and prefix (e.g.
                        'bucketname/subdirectory') where input is already
                        stored and output will be sent to. Using --tibanna
                        implies --default-resources is set as default.
                        Optionally, use --precommand to specify any
                        preparation command to run before snakemake command on
                        the cloud (inside snakemake container on Tibanna VM).
                        Also, --use-conda, --use-singularity, --config,
                        --configfile are supported and will be carried over.
                        (default: False)
  --tibanna-sfn TIBANNA_SFN
                        Name of Tibanna Unicorn step function (e.g.
                        tibanna_unicorn_monty).This works as serverless
                        scheduler/resource allocator and must be deployed
                        first using tibanna cli. (e.g. tibanna deploy_unicorn
                        --usergroup=monty --buckets=bucketname) (default:
                        None)
  --precommand PRECOMMAND
                        Any command to execute before snakemake command on AWS
                        cloud such as wget, git clone, unzip, etc. This is
                        used with --tibanna.Do not include input/output
                        download/upload commands - file transfer between S3
                        bucket and the run environment (container) is
                        automatically handled by Tibanna. (default: None)
  --tibanna-config TIBANNA_CONFIG [TIBANNA_CONFIG ...]
                        Additional tibanna config e.g. --tibanna-config
                        spot_instance=true subnet=<subnet_id> security
                        group=<security_group_id> (default: None)

GOOGLE_LIFE_SCIENCE:
  --google-lifesciences
                        Execute workflow on Google Cloud cloud using the
                        Google Life. Science API. This requires default
                        application credentials (json) to be created and
                        export to the environment to use Google Cloud Storage,
                        Compute Engine, and Life Sciences. The credential file
                        should be exported as GOOGLE_APPLICATION_CREDENTIALS
                        for snakemake to discover. Also, --use-conda, --use-
                        singularity, --config, --configfile are supported and
                        will be carried over. (default: False)
  --google-lifesciences-regions GOOGLE_LIFESCIENCES_REGIONS [GOOGLE_LIFESCIENCES_REGIONS ...]
                        Specify one or more valid instance regions (defaults
                        to US) (default: ['us-east1', 'us-west1', 'us-
                        central1'])
  --google-lifesciences-location GOOGLE_LIFESCIENCES_LOCATION
                        The Life Sciences API service used to schedule the
                        jobs. E.g., us-centra1 (Iowa) and europe-west2
                        (London) Watch the terminal output to see all options
                        found to be available. If not specified, defaults to
                        the first found with a matching prefix from regions
                        specified with --google-lifesciences-regions.
                        (default: None)
  --google-lifesciences-keep-cache
                        Cache workflows in your Google Cloud Storage Bucket
                        specified by --default-remote-prefix/{source}/{cache}.
                        Each workflow working directory is compressed to a
                        .tar.gz, named by the hash of the contents, and kept
                        in Google Cloud Storage. By default, the caches are
                        deleted at the shutdown step of the workflow.
                        (default: False)

CONDA:
  --use-conda           If defined in the rule, run job in a conda
                        environment. If this flag is not set, the conda
                        directive is ignored. (default: False)
  --list-conda-envs     List all conda environments and their location on
                        disk. (default: False)
  --conda-prefix DIR    Specify a directory in which the 'conda' and 'conda-
                        archive' directories are created. These are used to
                        store conda environments and their archives,
                        respectively. If not supplied, the value is set to the
                        '.snakemake' directory relative to the invocation
                        directory. If supplied, the `--use-conda` flag must
                        also be set. The value may be given as a relative
                        path, which will be extrapolated to the invocation
                        directory, or as an absolute path. (default: None)
  --conda-cleanup-envs  Cleanup unused conda environments. (default: False)
  --conda-cleanup-pkgs [{tarballs,cache}]
                        Cleanup conda packages after creating environments. In
                        case of 'tarballs' mode, will clean up all downloaded
                        package tarballs. In case of 'cache' mode, will
                        additionally clean up unused package caches. If mode
                        is omitted, will default to only cleaning up the
                        tarballs. (default: None)
  --conda-create-envs-only
                        If specified, only creates the job-specific conda
                        environments then exits. The `--use-conda` flag must
                        also be set. (default: False)
  --conda-frontend {conda,mamba}
                        Choose the conda frontend for installing environments.
                        Caution: mamba is much faster, but still in beta test.
                        (default: conda)

SINGULARITY:
  --use-singularity     If defined in the rule, run job within a singularity
                        container. If this flag is not set, the singularity
                        directive is ignored. (default: False)
  --singularity-prefix DIR
                        Specify a directory in which singularity images will
                        be stored.If not supplied, the value is set to the
                        '.snakemake' directory relative to the invocation
                        directory. If supplied, the `--use-singularity` flag
                        must also be set. The value may be given as a relative
                        path, which will be extrapolated to the invocation
                        directory, or as an absolute path. (default: None)
  --singularity-args ARGS
                        Pass additional args to singularity. (default: )

ENVIRONMENT MODULES:
  --use-envmodules      If defined in the rule, run job within the given
                        environment modules, loaded in the given order. This
                        can be combined with --use-conda and --use-
                        singularity, which will then be only used as a
                        fallback for rules which don't define environment
                        modules. (default: False)

## 20. Bowtie 2
========================================
Bowtie 2 version 2.5.4 by Ben Langmead (langmea@cs.jhu.edu, www.cs.jhu.edu/~langmea)
Usage: 
  bowtie2 [options]* -x <bt2-idx> {-1 <m1> -2 <m2> | -U <r> | --interleaved <i> | -b <bam>} [-S <sam>]

  <bt2-idx>  Index filename prefix (minus trailing .X.bt2).
             NOTE: Bowtie 1 and Bowtie 2 indexes are not compatible.
  <m1>       Files with #1 mates, paired with files in <m2>.
             Could be gzip'ed (extension: .gz) or bzip2'ed (extension: .bz2).
  <m2>       Files with #2 mates, paired with files in <m1>.
             Could be gzip'ed (extension: .gz) or bzip2'ed (extension: .bz2).
  <r>        Files with unpaired reads.
             Could be gzip'ed (extension: .gz) or bzip2'ed (extension: .bz2).
  <i>        Files with interleaved paired-end FASTQ/FASTA reads
             Could be gzip'ed (extension: .gz) or bzip2'ed (extension: .bz2).
  <bam>      Files are unaligned BAM sorted by read name.
  <sam>      File for SAM output (default: stdout)

  <m1>, <m2>, <r> can be comma-separated lists (no whitespace) and can be
  specified many times.  E.g. '-U file1.fq,file2.fq -U file3.fq'.

Options (defaults in parentheses):

 Input:
  -q                 query input files are FASTQ .fq/.fastq (default)
  --tab5             query input files are TAB5 .tab5
  --tab6             query input files are TAB6 .tab6
  --qseq             query input files are in Illumina's qseq format
  -f                 query input files are (multi-)FASTA .fa/.mfa
  -r                 query input files are raw one-sequence-per-line
  -F k:<int>,i:<int> query input files are continuous FASTA where reads
                     are substrings (k-mers) extracted from the FASTA file
                     and aligned at offsets 1, 1+i, 1+2i ... end of reference
  -c                 <m1>, <m2>, <r> are sequences themselves, not files
  -s/--skip <int>    skip the first <int> reads/pairs in the input (none)
  -u/--upto <int>    stop after first <int> reads/pairs (no limit)
  -5/--trim5 <int>   trim <int> bases from 5'/left end of reads (0)
  -3/--trim3 <int>   trim <int> bases from 3'/right end of reads (0)
  --trim-to [3:|5:]<int> trim reads exceeding <int> bases from either 3' or 5' end
                     If the read end is not specified then it defaults to 3 (0)
  --phred33          qualities are Phred+33 (default)
  --phred64          qualities are Phred+64
  --int-quals        qualities encoded as space-delimited integers

 Presets:                 Same as:
  For --end-to-end:
   --very-fast            -D 5 -R 1 -N 0 -L 22 -i S,0,2.50
   --fast                 -D 10 -R 2 -N 0 -L 22 -i S,0,2.50
   --sensitive            -D 15 -R 2 -N 0 -L 22 -i S,1,1.15 (default)
   --very-sensitive       -D 20 -R 3 -N 0 -L 20 -i S,1,0.50

  For --local:
   --very-fast-local      -D 5 -R 1 -N 0 -L 25 -i S,1,2.00
   --fast-local           -D 10 -R 2 -N 0 -L 22 -i S,1,1.75
   --sensitive-local      -D 15 -R 2 -N 0 -L 20 -i S,1,0.75 (default)
   --very-sensitive-local -D 20 -R 3 -N 0 -L 20 -i S,1,0.50

 Alignment:
  -N <int>           max # mismatches in seed alignment; can be 0 or 1 (0)
  -L <int>           length of seed substrings; must be >3, <32 (22)
  -i <func>          interval between seed substrings w/r/t read len (S,1,1.15)
  --n-ceil <func>    func for max # non-A/C/G/Ts permitted in aln (L,0,0.15)
  --dpad <int>       include <int> extra ref chars on sides of DP table (15)
  --gbar <int>       disallow gaps within <int> nucs of read extremes (4)
  --ignore-quals     treat all quality values as 30 on Phred scale (off)
  --nofw             do not align forward (original) version of read (off)
  --norc             do not align reverse-complement version of read (off)
  --no-1mm-upfront   do not allow 1 mismatch alignments before attempting to
                     scan for the optimal seeded alignments
  --end-to-end       entire read must align; no clipping (on)
   OR
  --local            local alignment; ends might be soft clipped (off)

 Scoring:
  --ma <int>         match bonus (0 for --end-to-end, 2 for --local) 
  --mp <int>         max penalty for mismatch; lower qual = lower penalty (6)
  --np <int>         penalty for non-A/C/G/Ts in read/ref (1)
  --rdg <int>,<int>  read gap open, extend penalties (5,3)
  --rfg <int>,<int>  reference gap open, extend penalties (5,3)
  --score-min <func> min acceptable alignment score w/r/t read length
                     (G,20,8 for local, L,-0.6,-0.6 for end-to-end)

 Reporting:
  (default)          look for multiple alignments, report best, with MAPQ
   OR
  -k <int>           report up to <int> alns per read; MAPQ not meaningful
   OR
  -a/--all           report all alignments; very slow, MAPQ not meaningful

 Effort:
  -D <int>           give up extending after <int> failed extends in a row (15)
  -R <int>           for reads w/ repetitive seeds, try <int> sets of seeds (2)

 Paired-end:
  -I/--minins <int>  minimum fragment length (0)
  -X/--maxins <int>  maximum fragment length (500)
  --fr/--rf/--ff     -1, -2 mates align fw/rev, rev/fw, fw/fw (--fr)
  --no-mixed         suppress unpaired alignments for paired reads
  --no-discordant    suppress discordant alignments for paired reads
  --dovetail         concordant when mates extend past each other
  --no-contain       not concordant when one mate alignment contains other
  --no-overlap       not concordant when mates overlap at all

 BAM:
  --align-paired-reads
                     Bowtie2 will, by default, attempt to align unpaired BAM reads.
                     Use this option to align paired-end reads instead.
  --preserve-tags    Preserve tags from the original BAM record by
                     appending them to the end of the corresponding SAM output.

 Output:
  -t/--time          print wall-clock time taken by search phases
  --un <path>        write unpaired reads that didn't align to <path>
  --al <path>        write unpaired reads that aligned at least once to <path>
  --un-conc <path>   write pairs that didn't align concordantly to <path>
  --al-conc <path>   write pairs that aligned concordantly at least once to <path>
    (Note: for --un, --al, --un-conc, or --al-conc, add '-gz' to the option name, e.g.
    --un-gz <path>, to gzip compress output, or add '-bz2' to bzip2 compress output.)
  --quiet            print nothing to stderr except serious errors
  --met-file <path>  send metrics to file at <path> (off)
  --met-stderr       send metrics to stderr (off)
  --met <int>        report internal counters & metrics every <int> secs (1)
  --no-unal          suppress SAM records for unaligned reads
  --no-head          suppress header lines, i.e. lines starting with @
  --no-sq            suppress @SQ header lines
  --rg-id <text>     set read group id, reflected in @RG line and RG:Z: opt field
  --rg <text>        add <text> ("lab:value") to @RG line of SAM header.
                     Note: @RG line only printed when --rg-id is set.
  --omit-sec-seq     put '*' in SEQ and QUAL fields for secondary alignments.
  --sam-no-qname-trunc
                     Suppress standard behavior of truncating readname at first whitespace 
                     at the expense of generating non-standard SAM.
  --xeq              Use '='/'X', instead of 'M,' to specify matches/mismatches in SAM record.
  --soft-clipped-unmapped-tlen
                     Exclude soft-clipped bases when reporting TLEN.
  --sam-append-comment
                     Append FASTA/FASTQ comment to SAM record.
  --sam-opt-config <config>
                     Use <config>, example '-MD,YP,-AS', to toggle SAM Optional fields.

 Performance:
  -p/--threads <int> number of alignment threads to launch (1)
  --reorder          force SAM output order to match order of input reads
  --mm               use memory-mapped I/O for index; many 'bowtie's can share

 Other:
  --qc-filter        filter out reads that are bad according to QSEQ filter
  --seed <int>       seed for random number generator (0)
  --non-deterministic
                     seed rand. gen. arbitrarily instead of using read attributes
  --version          print version information and quit
  -h/--help          print this usage message

## 21. FastANI
========================================
-----------------
fastANI is a fast alignment-free implementation for computing whole-genome Average Nucleotide Identity (ANI) between genomes
-----------------
Example usage:
$ fastANI -q genome1.fa -r genome2.fa -o output.txt
$ fastANI -q genome1.fa --rl genome_list.txt -o output.txt

SYNOPSIS
--------
fastANI [-h] [-r <value>] [--rl <value>] [-q <value>] [--ql <value>] [-k
        <value>] [-t <value>] [--fragLen <value>] [--minFraction <value>]
        [--maxRatioDiff <value>] [--visualize] [--matrix] [-o <value>] [-s] [-v]

OPTIONS
--------
-h, --help
     print this help page

-r, --ref <value>
     reference genome (fasta/fastq)[.gz]

--rl, --refList <value>
     a file containing list of reference genome files, one genome per line

-q, --query <value>
     query genome (fasta/fastq)[.gz]

--ql, --queryList <value>
     a file containing list of query genome files, one genome per line

-k, --kmer <value>
     kmer size <= 16 [default : 16]

-t, --threads <value>
     thread count for parallel execution [default : 1]

--fragLen <value>
     fragment length [default : 3,000]

--minFraction <value>
     minimum fraction of genome that must be shared for trusting ANI. If
     reference and query genome size differ, smaller one among the two is
     considered. [default : 0.2]

--maxRatioDiff <value>
     maximum difference between (Total Ref. Length/Total Occ. Hashes) and (Total
     Ref. Length/Total No. Hashes). [default : 10.0]

--visualize
     output mappings for visualization, can be enabled for single genome to
     single genome comparison only [disabled by default]

--matrix
     also output ANI values as lower triangular matrix (format inspired from
     phylip). If enabled, you should expect an output file with .matrix
     extension [disabled by default]

-o, --output <value>
     output file name

-s, --sanityCheck
     run sanity check

-v, --version
     show version


## 22. NGMLR
========================================
ngmlr 0.2.7 (build: Jul  8 2025 22:08:42, start: 2026-01-07.13:31:13)
Contact: fritz.sedlazeck@gmail.com, philipp.rescheneder@gmail.com

Usage: ngmlr [options] -r <reference> -q <reads> [-o <output>]

Input/Output:
    -r <file>,  --reference <file>
        (required)  Path to the reference genome (FASTA/Q, can be gzipped)
    -q <file>,  --query <file>
        Path to the read file (FASTA/Q) [/dev/stdin]
    -o <string>,  --output <string>
        Path to output file [none]
    --skip-write
        Don't write reference index to disk [false]
    --bam-fix
        Report reads with > 64k CIGAR operations as unmapped. Required to be compatibel to BAM format [false]
    --rg-id <string>
        Adds RG:Z:<string> to all alignments in SAM/BAM [none]
    --rg-sm <string>
        RG header: Sample [none]
    --rg-lb <string>
        RG header: Library [none]
    --rg-pl <string>
        RG header: Platform [none]
    --rg-ds <string>
        RG header: Description [none]
    --rg-dt <string>
        RG header: Date (format: YYYY-MM-DD) [none]
    --rg-pu <string>
        RG header: Platform unit [none]
    --rg-pi <string>
        RG header: Median insert size [none]
    --rg-pg <string>
        RG header: Programs [none]
    --rg-cn <string>
        RG header: sequencing center [none]
    --rg-fo <string>
        RG header: Flow order [none]
    --rg-ks <string>
        RG header: Key sequence [none]

General:
    -t <int>,  --threads <int>
        Number of threads [1]
    -x <pacbio, ont>,  --presets <pacbio, ont>
        Parameter presets for different sequencing technologies [pacbio]
    -i <0-1>,  --min-identity <0-1>
        Alignments with an identity lower than this threshold will be discarded [0.65]
    -R <int/float>,  --min-residues <int/float>
        Alignments containing less than <int> or (<float> * read length) residues will be discarded [0.25]
    --no-smallinv
        Don't detect small inversions [false]
    --no-lowqualitysplit
        Split alignments with poor quality [false]
    --verbose
        Debug output [false]
    --no-progress
        Don't print progress info while mapping [false]

Advanced:
    --match <float>
        Match score [2]
    --mismatch <float>
        Mismatch score [-5]
    --gap-open <float>
        Gap open score [-5]
    --gap-extend-max <float>
        Gap open extend max [-5]
    --gap-extend-min <float>
        Gap open extend min [-1]
    --gap-decay <float>
        Gap extend decay [0.15]
    -k <10-15>,  --kmer-length <10-15>
        K-mer length in bases [13]
    --kmer-skip <int>
        Number of k-mers to skip when building the lookup table from the reference [2]
    --bin-size <int>
        Sets the size of the grid used during candidate search [4]
    --max-segments <int>
        Max number of segments allowed for a read per kb [1]
    --subread-length <int>
        Length of fragments reads are split into [256]
    --subread-corridor <int>
        Length of corridor sub-reads are aligned with [40]


## 23. Sniffles2
========================================
usage: sniffles --input SORTED_INPUT.bam [--vcf OUTPUT.vcf] [--snf MERGEABLE_OUTPUT.snf] [--threads 4] [--mosaic]

Sniffles2: A fast structural variant (SV) caller for long-read sequencing data
 Version 2.2
 Contact: moritz.g.smolka@gmail.com

 Usage example A - Call SVs for a single sample:
    sniffles --input sorted_indexed_alignments.bam --vcf output.vcf

    ... OR, with CRAM input and bgzipped+tabix indexed VCF output:
      sniffles --input sample.cram --vcf output.vcf.gz

    ... OR, producing only a SNF file with SV candidates for later multi-sample calling:
      sniffles --input sample1.bam --snf sample1.snf

    ... OR, simultaneously producing a single-sample VCF and SNF file for later multi-sample calling:
      sniffles --input sample1.bam --vcf sample1.vcf.gz --snf sample1.snf

    ... OR, with additional options to specify tandem repeat annotations (for improved call accuracy), reference (for DEL sequences) and mosaic mode for detecting rare SVs:
      sniffles --input sample1.bam --vcf sample1.vcf.gz --tandem-repeats tandem_repeats.bed --reference genome.fa --mosaic

 Usage example B - Multi-sample calling:
    Step 1. Create .snf for each sample: sniffles --input sample1.bam --snf sample1.snf
    Step 2. Combined calling: sniffles --input sample1.snf sample2.snf ... sampleN.snf --vcf multisample.vcf

    ... OR, using a .tsv file containing a list of .snf files, and custom sample ids in an optional second column (one sample per line):
    Step 2. Combined calling: sniffles --input snf_files_list.tsv --vcf multisample.vcf

 Usage example C - Determine genotypes for a set of known SVs (force calling):
    sniffles --input sample.bam --genotype-vcf input_known_svs.vcf --vcf output_genotypes.vcf
    

 Use --help for full parameter/usage information
 

options:
  -h, --help                                                       show this help message and exit
  --version                                                        show program's version number and exit

Common parameters:
  -i IN [IN ...], --input IN [IN ...]                              For single-sample calling: A coordinate-sorted and indexed .bam/.cram (BAM/CRAM
                                                                   format) file containing aligned reads. - OR - For multi-sample calling: Multiple
                                                                   .snf files (generated before by running Sniffles2 for individual samples with
                                                                   --snf) (default: None)
  -v OUT.vcf, --vcf OUT.vcf                                        VCF output filename to write the called and refined SVs to. If the given filename
                                                                   ends with .gz, the VCF file will be automatically bgzipped and a .tbi index built
                                                                   for it. (default: None)
  --snf OUT.snf                                                    Sniffles2 file (.snf) output filename to store candidates for later multi-sample
                                                                   calling (default: None)
  --reference reference.fasta                                      (Optional) Reference sequence the reads were aligned against. To enable output of
                                                                   deletion SV sequences, this parameter must be set. (default: None)
  --tandem-repeats IN.bed                                          (Optional) Input .bed file containing tandem repeat annotations for the reference
                                                                   genome. (default: None)
  --phase                                                          Determine phase for SV calls (requires the input alignments to be phased) (default:
                                                                   False)
  -t N, --threads N                                                Number of parallel threads to use (speed-up for multi-core CPUs) (default: 4)

SV Filtering parameters:
  --minsupport auto                                                Minimum number of supporting reads for a SV to be reported (default: automatically
                                                                   choose based on coverage) (default: auto)
  --minsupport-auto-mult 0.1/0.025                                 Coverage based minimum support multiplier for germline mode (only for auto
                                                                   minsupport) (default: None)
  --minsvlen N                                                     Minimum SV length (in bp) (default: 50)
  --minsvlen-screen-ratio N                                        Minimum length for SV candidates (as fraction of --minsvlen) (default: 0.9)
  --mapq N                                                         Alignments with mapping quality lower than this value will be ignored (default: 20)
  --no-qc, --qc-output-all                                         Output all SV candidates, disregarding quality control steps. (default: False)
  --qc-stdev True                                                  Apply filtering based on SV start position and length standard deviation (default:
                                                                   True)
  --qc-stdev-abs-max N                                             Maximum standard deviation for SV length and size (in bp) (default: 500)
  --qc-strand False                                                Apply filtering based on strand support of SV calls (default: False)
  --qc-coverage N                                                  Minimum surrounding region coverage of SV calls (default: 1)
  --long-ins-length 2500                                           Insertion SVs longer than this value are considered as hard to detect based on the
                                                                   aligner and read length and subjected to more sensitive filtering. (default: 2500)
  --long-del-length 50000                                          Deletion SVs longer than this value are subjected to central coverage drop-based
                                                                   filtering (Not applicable for --mosaic) (default: 50000)
  --long-del-coverage 0.66                                         Long deletions with central coverage (in relation to upstream/downstream coverage)
                                                                   higher than this value will be filtered (Not applicable for --mosaic) (default:
                                                                   0.66)
  --long-dup-length 50000                                          Duplication SVs longer than this value are subjected to central coverage increase-
                                                                   based filtering (Not applicable for --mosaic) (default: 50000)
  --qc-bnd-filter-strand QC_BND_FILTER_STRAND                      Filter breakends that do not have support for both strands (default: True)
  --bnd-min-split-length BND_MIN_SPLIT_LENGTH                      Minimum length of read splits to be considered for breakends (default: 1000)
  --long-dup-coverage 1.33                                         Long duplications with central coverage (in relation to upstream/downstream
                                                                   coverage) lower than this value will be filtered (Not applicable for --mosaic)
                                                                   (default: 1.33)
  --max-splits-kb N                                                Additional number of splits per kilobase read sequence allowed before reads are
                                                                   ignored (default: 0.1)
  --max-splits-base N                                              Base number of splits allowed before reads are ignored (in addition to --max-
                                                                   splits-kb) (default: 3)
  --min-alignment-length N                                         Reads with alignments shorter than this length (in bp) will be ignored (default:
                                                                   1000)
  --phase-conflict-threshold F                                     Maximum fraction of conflicting reads permitted for SV phase information to be
                                                                   labelled as PASS (only for --phase) (default: 0.1)
  --detect-large-ins True                                          Infer insertions that are longer than most reads and therefore are spanned by few
                                                                   alignments only. (default: True)

SV Clustering parameters:
  --cluster-binsize N                                              Initial screening bin size in bp (default: 100)
  --cluster-r R                                                    Multiplier for SV start position standard deviation criterion in cluster merging
                                                                   (default: 2.5)
  --cluster-repeat-h H                                             Multiplier for mean SV length criterion for tandem repeat cluster merging (default:
                                                                   1.5)
  --cluster-repeat-h-max N                                         Max. merging distance based on SV length criterion for tandem repeat cluster
                                                                   merging (default: 1000)
  --cluster-merge-pos N                                            Max. merging distance for insertions and deletions on the same read and cluster in
                                                                   non-repeat regions (default: 150)
  --cluster-merge-len F                                            Max. size difference for merging SVs as fraction of SV length (default: 0.33)
  --cluster-merge-bnd N                                            Max. merging distance for breakend SV candidates. (default: 1000)

SV Genotyping parameters:
  --genotype-ploidy N                                              Sample ploidy (currently fixed at value 2) (default: 2)
  --genotype-error N                                               Estimated false positve rate for leads (relating to total coverage) (default: 0.05)
  --sample-id SAMPLE_ID                                            Custom ID for this sample, used for later multi-sample calling (stored in .snf)
                                                                   (default: None)
  --genotype-vcf IN.vcf                                            Determine the genotypes for all SVs in the given input .vcf file (forced calling).
                                                                   Re-genotyped .vcf will be written to the output file specified with --vcf.
                                                                   (default: None)

Multi-Sample Calling / Combine parameters:
  --combine-high-confidence F                                      Minimum fraction of samples in which a SV needs to have individually passed QC for
                                                                   it to be reported in combined output (a value of zero will report all SVs that pass
                                                                   QC in at least one of the input samples) (default: 0.0)
  --combine-low-confidence F                                       Minimum fraction of samples in which a SV needs to be present (failed QC) for it to
                                                                   be reported in combined output (default: 0.2)
  --combine-low-confidence-abs N                                   Minimum absolute number of samples in which a SV needs to be present (failed QC)
                                                                   for it to be reported in combined output (default: 2)
  --combine-null-min-coverage N                                    Minimum coverage for a sample genotype to be reported as 0/0 (sample genotypes with
                                                                   coverage below this threshold at the SV location will be output as ./.) (default:
                                                                   5)
  --combine-match N                                                Multiplier for maximum deviation of multiple SV's start/end position for them to be
                                                                   combined across samples. Given by max_dev=M*sqrt(min(SV_length_a,SV_length_b)),
                                                                   where M is this parameter. (default: 250)
  --combine-match-max N                                            Upper limit for the maximum deviation computed for --combine-match, in bp.
                                                                   (default: 1000)
  --combine-separate-intra                                         Disable combination of SVs within the same sample (default: False)
  --combine-output-filtered                                        Include low-confidence / mosaic SVs in multi-calling (default: False)
  --combine-pair-relabel                                           Override low-quality genotypes when combining 2 samples (may be used for e.g.
                                                                   tumor-normal comparisons) (default: False)
  --combine-pair-relabel-threshold COMBINE_PAIR_RELABEL_THRESHOLD  Genotype quality below which a genotype call will be relabeled (default: 20)
  --combine-close-handles                                          Close .SNF file handles after each use. May lower performance, but may be required
                                                                   when maximum number of file handles supported by OS is reached when merging many
                                                                   samples. (default: False)

SV Postprocessing, QC and output parameters:
  --output-rnames                                                  Output names of all supporting reads for each SV in the RNAMEs info field (default:
                                                                   False)
  --no-consensus                                                   Disable consensus sequence generation for insertion SV calls (may improve
                                                                   performance) (default: False)
  --no-sort                                                        Do not sort output VCF by genomic coordinates (may slightly improve performance)
                                                                   (default: False)
  --no-progress                                                    Disable progress display (default: False)
  --quiet                                                          Disable all logging, except errors (default: False)
  --max-del-seq-len N                                              Maximum deletion sequence length to be output. Deletion SVs longer than this value
                                                                   will be written to the output as symbolic SVs. (default: 50000)
  --symbolic                                                       Output all SVs as symbolic, including insertions and deletions, instead of
                                                                   reporting nucleotide sequences. (default: False)
  --allow-overwrite                                                Allow overwriting output files if already existing (default: False)

Mosaic calling mode parameters:
  --mosaic                                                         Set Sniffles run mode to detect rare, somatic and mosaic SVs (default: False)
  --mosaic-af-max F                                                Maximum allele frequency for which SVs are considered mosaic (default: 0.3)
  --mosaic-af-min F                                                Minimum allele frequency for mosaic SVs to be output (default: 0.05)
  --mosaic-qc-invdup-min-length N                                  Minimum SV length for mosaic inversion and duplication SVs (default: 500)
  --mosaic-qc-coverage-max-change-frac F                           Maximum relative coverage change across SV breakpoints (default: 0.1)
  --mosaic-qc-strand True                                          Apply filtering based on strand support of SV calls (default: True)
  --mosaic-include-germline                                        Report germline SVs as well in mosaic mode (default: False)

Developer parameters:
  --combine-consensus                                              Output the consensus genotype of all samples (default: False)
  --qc-coverage-max-change-frac F                                  Maximum relative coverage change across SV breakpoints (default: -1)

 Usage example A - Call SVs for a single sample:
    sniffles --input sorted_indexed_alignments.bam --vcf output.vcf

    ... OR, with CRAM input and bgzipped+tabix indexed VCF output:
      sniffles --input sample.cram --vcf output.vcf.gz

    ... OR, producing only a SNF file with SV candidates for later multi-sample calling:
      sniffles --input sample1.bam --snf sample1.snf

    ... OR, simultaneously producing a single-sample VCF and SNF file for later multi-sample calling:
      sniffles --input sample1.bam --vcf sample1.vcf.gz --snf sample1.snf

    ... OR, with additional options to specify tandem repeat annotations (for improved call accuracy), reference (for DEL sequences) and mosaic mode for detecting rare SVs:
      sniffles --input sample1.bam --vcf sample1.vcf.gz --tandem-repeats tandem_repeats.bed --reference genome.fa --mosaic

 Usage example B - Multi-sample calling:
    Step 1. Create .snf for each sample: sniffles --input sample1.bam --snf sample1.snf
    Step 2. Combined calling: sniffles --input sample1.snf sample2.snf ... sampleN.snf --vcf multisample.vcf

    ... OR, using a .tsv file containing a list of .snf files, and custom sample ids in an optional second column (one sample per line):
    Step 2. Combined calling: sniffles --input snf_files_list.tsv --vcf multisample.vcf

 Usage example C - Determine genotypes for a set of known SVs (force calling):
    sniffles --input sample.bam --genotype-vcf input_known_svs.vcf --vcf output_genotypes.vcf
    

## 24. iPHoP
========================================
/usr/local/env-execute: line 3: exec: iphop.py: not found

## 25. MMseqs2
========================================
MMseqs2 (Many against Many sequence searching) is an open-source software suite for very fast, 
parallelized protein sequence searches and clustering of huge protein sequence data sets.

Please cite: M. Steinegger and J. Soding. MMseqs2 enables sensitive protein sequence searching for the analysis of massive data sets. Nature Biotechnology, doi:10.1038/nbt.3988 (2017).

MMseqs2 Version: 14.7e284
© Martin Steinegger (martin.steinegger@snu.ac.kr)

usage: mmseqs <command> [<args>]

Easy workflows for plain text input/output
  easy-search       	Sensitive homology search
  easy-linsearch    	Fast, less sensitive homology search
  easy-cluster      	Slower, sensitive clustering
  easy-linclust     	Fast linear time cluster, less sensitive clustering
  easy-taxonomy     	Taxonomic classification
  easy-rbh          	Find reciprocal best hit

Main workflows for database input/output
  search            	Sensitive homology search
  linsearch         	Fast, less sensitive homology search
  map               	Map nearly identical sequences
  rbh               	Reciprocal best hit search
  linclust          	Fast, less sensitive clustering
  cluster           	Slower, sensitive clustering
  clusterupdate     	Update previous clustering with new sequences
  taxonomy          	Taxonomic classification

Input database creation
  databases         	List and download databases
  createdb          	Convert FASTA/Q file(s) to a sequence DB
  createindex       	Store precomputed index on disk to reduce search overhead
  createlinindex    	Create linsearch index
  convertmsa        	Convert Stockholm/PFAM MSA file to a MSA DB
  tsv2db            	Convert a TSV file to any DB
  tar2db            	Convert content of tar archives to any DB
  db2tar            	Archive contents of a DB to a tar archive
  msa2profile       	Convert a MSA DB to a profile DB

Handle databases on storage and memory
  compress          	Compress DB entries
  decompress        	Decompress DB entries
  rmdb              	Remove a DB
  mvdb              	Move a DB
  cpdb              	Copy a DB
  lndb              	Symlink a DB
  aliasdb           	Create relative symlink of DB to another name in the same folder
  unpackdb          	Unpack a DB into separate files
  touchdb           	Preload DB into memory (page cache)

Unite and intersect databases
  createsubdb       	Create a subset of a DB from list of DB keys
  concatdbs         	Concatenate two DBs, giving new IDs to entries from 2nd DB
  splitdb           	Split DB into subsets
  mergedbs          	Merge entries from multiple DBs
  subtractdbs       	Remove all entries from first DB occurring in second DB by key

Format conversion for downstream processing
  convertalis       	Convert alignment DB to BLAST-tab, SAM or custom format
  createtsv         	Convert result DB to tab-separated flat file
  convert2fasta     	Convert sequence DB to FASTA format
  result2flat       	Create flat file by adding FASTA headers to DB entries
  createseqfiledb   	Create a DB of unaligned FASTA entries
  taxonomyreport    	Create a taxonomy report in Kraken or Krona format

Sequence manipulation/transformation
  extractorfs       	Six-frame extraction of open reading frames
  extractframes     	Extract frames from a nucleotide sequence DB
  orftocontig       	Write ORF locations in alignment format
  reverseseq        	Reverse (without complement) sequences
  translatenucs     	Translate nucleotides to proteins
  translateaa       	Translate proteins to lexicographically lowest codons
  splitsequence     	Split sequences by length
  masksequence      	Soft mask sequence DB using tantan
  extractalignedregion	Extract aligned sequence region from query

Result manipulation 
  swapresults       	Transpose prefilter/alignment DB
  result2rbh        	Filter a merged result DB to retain only reciprocal best hits
  result2msa        	Compute MSA DB from a result DB
  result2dnamsa     	Compute MSA DB with out insertions in the query for DNA sequences
  result2stats      	Compute statistics for each entry in a DB
  filterresult      	Pairwise alignment result filter
  offsetalignment   	Offset alignment by ORF start position
  proteinaln2nucl   	Transform protein alignments to nucleotide alignments
  result2repseq     	Get representative sequences from result DB
  sortresult        	Sort a result DB in the same order as the prefilter or align module
  summarizealis     	Summarize alignment result to one row (uniq. cov., cov., avg. seq. id.)
  summarizeresult   	Extract annotations from alignment DB

Taxonomy assignment 
  createtaxdb       	Add taxonomic labels to sequence DB
  createbintaxonomy 	Create binary taxonomy from NCBI input
  createbintaxmapping	Create binary taxonomy mapping from tabular taxonomy mapping
  addtaxonomy       	Add taxonomic labels to result DB
  taxonomyreport    	Create a taxonomy report in Kraken or Krona format
  filtertaxdb       	Filter taxonomy result database
  filtertaxseqdb    	Filter taxonomy sequence database
  aggregatetax      	Aggregate multiple taxon labels to a single label
  aggregatetaxweights	Aggregate multiple taxon labels to a single label
  lcaalign          	Efficient gapped alignment for lca computation
  lca               	Compute the lowest common ancestor
  majoritylca       	Compute the lowest common ancestor using majority voting

Multi-hit search    
  multihitdb        	Create sequence DB for multi hit searches
  multihitsearch    	Search with a grouped set of sequences against another grouped set
  besthitperset     	For each set of sequences compute the best element and update p-value
  combinepvalperset 	For each set compute the combined p-value
  mergeresultsbyset 	Merge results from multiple ORFs back to their respective contig

Prefiltering        
  prefilter         	Double consecutive diagonal k-mer search
  ungappedprefilter 	Optimal diagonal score search
  kmermatcher       	Find bottom-m-hashed k-mer matches within sequence DB
  kmersearch        	Find bottom-m-hashed k-mer matches between target and query DB

Alignment           
  align             	Optimal gapped local alignment
  alignall          	Within-result all-vs-all gapped local alignment
  transitivealign   	Transfer alignments via transitivity
  rescorediagonal   	Compute sequence identity for diagonal
  alignbykmer       	Heuristic gapped local k-mer based alignment

Clustering          
  clust             	Cluster result by Set-Cover/Connected-Component/Greedy-Incremental
  clusthash         	Hash-based clustering of equal length sequences
  mergeclusters     	Merge multiple cascaded clustering steps

Profile databases   
  result2profile    	Compute profile DB from a result DB
  msa2result        	Convert a MSA DB to a profile DB
  msa2profile       	Convert a MSA DB to a profile DB
  sequence2profile  	Turn sequence into profile by adding context specific pseudo counts
  profile2pssm      	Convert a profile DB to a tab-separated PSSM file
  profile2consensus 	Extract consensus sequence DB from a profile DB
  profile2repseq    	Extract representative sequence DB from a profile DB
  convertprofiledb  	Convert a HH-suite HHM DB to a profile DB

Profile-profile databases
  tsv2exprofiledb   	Create a expandable profile db from TSV files
  convertca3m       	Convert a cA3M DB to a result DB
  expandaln         	Expand an alignment result based on another
  expand2profile    	Expand an alignment result based on another and create a profile

Utility modules to manipulate DBs
  view              	Print DB entries given in --id-list to stdout
  apply             	Execute given program on each DB entry
  filterdb          	DB filtering by given conditions
  swapdb            	Transpose DB with integer values in first column
  prefixid          	For each entry in a DB prepend the entry key to the entry itself
  suffixid          	For each entry in a DB append the entry key to the entry itself
  renamedbkeys      	Create a new DB with original keys renamed

Special-purpose utilities
  diffseqdbs        	Compute diff of two sequence DBs
  summarizetabs     	Extract annotations from HHblits BLAST-tab-formatted results
  gff2db            	Extract regions from a sequence database based on a GFF3 file
  maskbygff         	Mask out sequence regions in a sequence DB by features selected from a GFF3 file
  convertkb         	Convert UniProtKB data to a DB
  summarizeheaders  	Summarize FASTA headers of result DB
  nrtotaxmapping    	Create taxonomy mapping for NR database
  extractdomains    	Extract highest scoring alignment regions for each sequence from BLAST-tab file
  countkmer         	Count k-mers

Bash completion for modules and parameters can be installed by adding "source MMSEQS_HOME/util/bash-completion.sh" to your "$HOME/.bash_profile".
Include the location of the MMseqs2 binary in your "$PATH" environment variable.

## 26. LoVis4u
========================================
/usr/local/env-execute: line 3: exec: lovis4u.py: not found

========================================
Done! File saved to: /home/zczhao/GAgent/runtime/bio_tools/docs/bio_tools_help.md
