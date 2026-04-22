"""
Bioinformatics Tool Wrappers
============================
Auto-generated wrappers for Docker-based bioinformatics tools.
"""

import subprocess
import os
from pathlib import Path
from typing import List, Optional, Union

def _run_docker_cmd(image: str, cmd_args: List[str], mounts: Optional[List[str]] = None, envs: Optional[List[str]] = None):
    """Internal helper to run docker commands."""
    if mounts is None:
        mounts = []
    if envs is None:
        envs = []

    # Base command
    docker_cmd = ["docker", "run", "--rm"]
    
    # Add mounts (vols)
    for mount in mounts:
        docker_cmd.extend(["-v", mount])
        
    # Add envs
    for env in envs:
        docker_cmd.extend(["-e", env])
        
    # Add image
    docker_cmd.append(image)
    
    # Add tool arguments
    docker_cmd.extend(cmd_args)
    
    print(f"Executing: {' '.join(docker_cmd)}")
    
    try:
        result = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {e.stderr}")
        raise e


def run_trim_galore(args: List[str], data_dir: str = "/data"):
    """
    Run Trim Galore! using staphb/trim-galore:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "staphb/trim-galore:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_snakemake(args: List[str], data_dir: str = "/data"):
    """
    Run Snakemake using snakemake/snakemake:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "snakemake/snakemake:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_htstream(args: List[str], data_dir: str = "/data"):
    """
    Run HTStream using quay.io/biocontainers/htstream:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "quay.io/biocontainers/htstream:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_seqkit(args: List[str], data_dir: str = "/data"):
    """
    Run SeqKit using staphb/seqkit:2.8.0.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "staphb/seqkit:2.8.0"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_bwa_burrows_wheeler_aligner(args: List[str], data_dir: str = "/data"):
    """
    Run BWA (Burrows-Wheeler Aligner) using staphb/bwa:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "staphb/bwa:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_blast(args: List[str], data_dir: str = "/data"):
    """
    Run BLAST+ using biocontainers/blast:2.2.31.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "biocontainers/blast:2.2.31"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_seqtk(args: List[str], data_dir: str = "/data"):
    """
    Run Seqtk using staphb/seqtk:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "staphb/seqtk:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_minimap2(args: List[str], data_dir: str = "/data"):
    """
    Run Minimap2 using staphb/minimap2:2.26.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "staphb/minimap2:2.26"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_nextflow(args: List[str], data_dir: str = "/data"):
    """
    Run Nextflow using nextflow/nextflow:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "nextflow/nextflow:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_bowtie_2(args: List[str], data_dir: str = "/data"):
    """
    Run Bowtie 2 using staphb/bowtie2:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "staphb/bowtie2:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_nanoplot(args: List[str], data_dir: str = "/data"):
    """
    Run NanoPlot using staphb/nanoplot:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "staphb/nanoplot:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_sniffles2(args: List[str], data_dir: str = "/data"):
    """
    Run Sniffles2 using staphb/sniffles:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "staphb/sniffles:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_samtools(args: List[str], data_dir: str = "/data"):
    """
    Run Samtools using staphb/samtools:1.21.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "staphb/samtools:1.21"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_mmseqs2(args: List[str], data_dir: str = "/data"):
    """
    Run MMseqs2 using staphb/mmseqs2:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "staphb/mmseqs2:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_ngmlr(args: List[str], data_dir: str = "/data"):
    """
    Run NGMLR using staphb/ngmlr:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "staphb/ngmlr:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_checkm(args: List[str], data_dir: str = "/data"):
    """
    Run CheckM using staphb/checkm:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "staphb/checkm:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_das_tool(args: List[str], data_dir: str = "/data"):
    """
    Run DAS Tool using staphb/das_tool:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "staphb/das_tool:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_maxbin2(args: List[str], data_dir: str = "/data"):
    """
    Run MaxBin2 using nanozoo/maxbin2:2.2.7--e1577a7.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "nanozoo/maxbin2:2.2.7--e1577a7"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_concoct(args: List[str], data_dir: str = "/data"):
    """
    Run CONCOCT using staphb/concoct:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "staphb/concoct:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_metabat2(args: List[str], data_dir: str = "/data"):
    """
    Run MetaBAT2 using quay.io/biocontainers/metabat2:2.15--h988d1d8_2.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "quay.io/biocontainers/metabat2:2.15--h988d1d8_2"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_flye(args: List[str], data_dir: str = "/data"):
    """
    Run Flye using staphb/flye:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "staphb/flye:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_megahit(args: List[str], data_dir: str = "/data"):
    """
    Run MEGAHIT using voutcn/megahit:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "voutcn/megahit:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_bakta(args: List[str], data_dir: str = "/data"):
    """
    Run Bakta using staphb/bakta:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "staphb/bakta:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_prodigal(args: List[str], data_dir: str = "/data"):
    """
    Run Prodigal using staphb/prodigal:2.6.3.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "staphb/prodigal:2.6.3"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_fastani(args: List[str], data_dir: str = "/data"):
    """
    Run FastANI using staphb/fastani:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "staphb/fastani:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_gtdb_tk(args: List[str], data_dir: str = "/data"):
    """
    Run GTDB-Tk using quay.io/biocontainers/gtdbtk:2.3.2--pyhdfd78af_0.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "quay.io/biocontainers/gtdbtk:2.3.2--pyhdfd78af_0"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_vibrant(args: List[str], data_dir: str = "/data"):
    """
    Run VIBRANT using quay.io/biocontainers/vibrant:1.2.1--pyhdfd78af_0.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "quay.io/biocontainers/vibrant:1.2.1--pyhdfd78af_0"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_genomad(args: List[str], data_dir: str = "/data"):
    """
    Run geNomad using quay.io/biocontainers/genomad:1.7.6--pyhdfd78af_0.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "quay.io/biocontainers/genomad:1.7.6--pyhdfd78af_0"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_iphop(args: List[str], data_dir: str = "/data"):
    """
    Run iPHoP using quay.io/biocontainers/iphop:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "quay.io/biocontainers/iphop:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_virsorter2(args: List[str], data_dir: str = "/data"):
    """
    Run VirSorter2 using jiarong/virsorter:latest.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "jiarong/virsorter:latest"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)

def run_checkv(args: List[str], data_dir: str = "/data"):
    """
    Run CheckV using quay.io/biocontainers/checkv:1.0.1--pyhdfd78af_0.
    
    Args:
        args: List of arguments to pass to the tool command.
        data_dir: Local path to mount to /data in the container.
    """
    image = "quay.io/biocontainers/checkv:1.0.1--pyhdfd78af_0"
    mounts = [f"{os.path.abspath(data_dir)}:/data"]
    
    return _run_docker_cmd(image, args, mounts=mounts)
