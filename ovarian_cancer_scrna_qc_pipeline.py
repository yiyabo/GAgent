#!/usr/bin/env python3
"""
Ovarian Cancer Single-Cell RNA-seq Quality Control Pipeline
Re-implementation from scratch (not based on samplecontrol.R)
"""

import os
import sys
import argparse
import scanpy as sc
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

def load_sample(sample_dir, sample_name):
    """Load a single 10x sample and add sample metadata"""
    print(f"Loading sample: {sample_name} from {sample_dir}")
    adata = sc.read_10x_mtx(sample_dir, var_names='gene_symbols', cache=False)
    adata.obs['sample'] = sample_name
    adata.obs['sample_type'] = 'cancer' if sample_name.startswith('cancer') else 'normal'
    print(f"  Loaded {adata.n_obs} cells, {adata.n_vars} genes")
    return adata

def compute_qc_metrics(adata):
    """Compute standard QC metrics for each cell"""
    print("Computing QC metrics...")
    
    # Total UMI counts per cell
    adata.obs['total_counts'] = adata.X.sum(axis=1).A1 if hasattr(adata.X, 'A1') else adata.X.sum(axis=1)
    
    # Number of detected genes per cell
    adata.obs['n_genes_by_counts'] = (adata.X > 0).sum(axis=1).A1 if hasattr(adata.X, 'A1') else (adata.X > 0).sum(axis=1)
    
    # Mitochondrial genes (starting with MT- or mt-)
    mt_pattern = '^MT-|^mt-'
    adata.var['mt'] = adata.var_names.str.match(mt_pattern, case=False)
    sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], percent_top=None, log1p=False, inplace=True)
    
    # Ribosomal genes (starting with RPS or RPL)
    ribo_pattern = '^RPS|^RPL|^rps|^rpl'
    adata.var['ribo'] = adata.var_names.str.match(ribo_pattern, case=False)
    sc.pp.calculate_qc_metrics(adata, qc_vars=['ribo'], percent_top=None, log1p=False, inplace=True)
    
    print(f"  Computed metrics for {adata.n_obs} cells")
    return adata

def apply_filtering(adata, min_counts=500, max_counts=50000, 
                    min_genes=200, max_genes=6000,
                    max_mt_pct=20, max_ribo_pct=100):
    """Apply QC filtering thresholds"""
    print(f"\nApplying QC filters:")
    print(f"  min_counts: {min_counts}")
    print(f"  max_counts: {max_counts}")
    print(f"  min_genes: {min_genes}")
    print(f"  max_genes: {max_genes}")
    print(f"  max_mt_pct: {max_mt_pct}")
    print(f"  max_ribo_pct: {max_ribo_pct}")
    
    # Create boolean mask for cells passing all filters
    qc_pass = (
        (adata.obs['total_counts'] >= min_counts) &
        (adata.obs['total_counts'] <= max_counts) &
        (adata.obs['n_genes_by_counts'] >= min_genes) &
        (adata.obs['n_genes_by_counts'] <= max_genes) &
        (adata.obs['pct_counts_mt'] <= max_mt_pct) &
        (adata.obs['pct_counts_ribo'] <= max_ribo_pct)
    )
    
    adata.obs['qc_pass'] = qc_pass
    
    n_before = adata.n_obs
    n_after = qc_pass.sum()
    n_removed = n_before - n_after
    pct_removed = (n_removed / n_before) * 100
    
    print(f"\nFiltering results:")
    print(f"  Cells before filtering: {n_before}")
    print(f"  Cells after filtering: {n_after}")
    print(f"  Cells removed: {n_removed} ({pct_removed:.2f}%)")
    
    return adata

def generate_qc_plots(adata, output_dir):
    """Generate QC visualization plots"""
    print("\nGenerating QC plots...")
    
    # Set up plot directory
    plot_dir = Path(output_dir) / 'plots'
    plot_dir.mkdir(exist_ok=True)
    
    # 1. Total counts distribution
    plt.figure(figsize=(10, 6))
    sns.histplot(adata.obs['total_counts'], bins=100, kde=True)
    plt.axvline(adata.obs['total_counts'].quantile(0.05), color='r', linestyle='--', label='5th percentile')
    plt.axvline(adata.obs['total_counts'].quantile(0.95), color='r', linestyle='--', label='95th percentile')
    plt.xlabel('Total UMI Counts')
    plt.ylabel('Number of Cells')
    plt.title('Distribution of Total UMI Counts per Cell')
    plt.legend()
    plt.savefig(plot_dir / 'total_counts_distribution.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    # 2. Number of genes distribution
    plt.figure(figsize=(10, 6))
    sns.histplot(adata.obs['n_genes_by_counts'], bins=100, kde=True)
    plt.axvline(adata.obs['n_genes_by_counts'].quantile(0.05), color='r', linestyle='--', label='5th percentile')
    plt.axvline(adata.obs['n_genes_by_counts'].quantile(0.95), color='r', linestyle='--', label='95th percentile')
    plt.xlabel('Number of Detected Genes')
    plt.ylabel('Number of Cells')
    plt.title('Distribution of Detected Genes per Cell')
    plt.legend()
    plt.savefig(plot_dir / 'n_genes_distribution.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    # 3. Mitochondrial percentage distribution
    plt.figure(figsize=(10, 6))
    sns.histplot(adata.obs['pct_counts_mt'], bins=100, kde=True)
    plt.axvline(adata.obs['pct_counts_mt'].quantile(0.95), color='r', linestyle='--', label='95th percentile')
    plt.xlabel('Mitochondrial Gene Percentage (%)')
    plt.ylabel('Number of Cells')
    plt.title('Distribution of Mitochondrial Gene Expression')
    plt.legend()
    plt.savefig(plot_dir / 'mito_pct_distribution.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    # 4. Ribosomal percentage distribution
    plt.figure(figsize=(10, 6))
    sns.histplot(adata.obs['pct_counts_ribo'], bins=100, kde=True)
    plt.xlabel('Ribosomal Gene Percentage (%)')
    plt.ylabel('Number of Cells')
    plt.title('Distribution of Ribosomal Gene Expression')
    plt.savefig(plot_dir / 'ribo_pct_distribution.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    # 5. Scatter plot: genes vs counts
    plt.figure(figsize=(10, 6))
    plt.scatter(adata.obs['total_counts'], adata.obs['n_genes_by_counts'], 
                alpha=0.5, s=10, c=adata.obs['pct_counts_mt'], cmap='viridis')
    plt.colorbar(label='Mitochondrial %')
    plt.xlabel('Total UMI Counts')
    plt.ylabel('Number of Detected Genes')
    plt.title('Genes vs Counts (colored by Mitochondrial %)')
    plt.savefig(plot_dir / 'genes_vs_counts.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    # 6. Sample composition
    plt.figure(figsize=(12, 6))
    sample_counts = adata.obs['sample'].value_counts()
    sample_counts.plot(kind='bar')
    plt.xlabel('Sample')
    plt.ylabel('Number of Cells')
    plt.title('Cell Counts per Sample')
    plt.xticks(rotation=45, ha='right')
    plt.tight_layout()
    plt.savefig(plot_dir / 'sample_composition.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    # 7. Sample type composition
    plt.figure(figsize=(8, 6))
    type_counts = adata.obs['sample_type'].value_counts()
    type_counts.plot(kind='bar')
    plt.xlabel('Sample Type')
    plt.ylabel('Number of Cells')
    plt.title('Cell Counts by Sample Type')
    plt.tight_layout()
    plt.savefig(plot_dir / 'sample_type_composition.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    print(f"  Generated 7 QC plots in {plot_dir}")

def save_qc_report(adata, output_dir, thresholds):
    """Save QC summary report"""
    print("\nGenerating QC report...")
    
    report_path = Path(output_dir) / 'qc_report.txt'
    
    with open(report_path, 'w') as f:
        f.write("=" * 80 + "\n")
        f.write("OVARIAN CANCER scRNA-seq QUALITY CONTROL REPORT\n")
        f.write("=" * 80 + "\n\n")
        
        f.write(f"Data source: {thresholds['input_dir']}\n")
        f.write(f"Output directory: {output_dir}\n")
        f.write(f"Timestamp: {pd.Timestamp.now()}\n\n")
        
        f.write("QC FILTERING THRESHOLDS:\n")
        f.write("-" * 80 + "\n")
        f.write(f"  Minimum total counts: {thresholds['min_counts']}\n")
        f.write(f"  Maximum total counts: {thresholds['max_counts']}\n")
        f.write(f"  Minimum genes: {thresholds['min_genes']}\n")
        f.write(f"  Maximum genes: {thresholds['max_genes']}\n")
        f.write(f"  Maximum mitochondrial %: {thresholds['max_mt_pct']}\n")
        f.write(f"  Maximum ribosomal %: {thresholds['max_ribo_pct']}\n\n")
        
        f.write("SAMPLES PROCESSED:\n")
        f.write("-" * 80 + "\n")
        for sample in sorted(adata.obs['sample'].unique()):
            n_cells = (adata.obs['sample'] == sample).sum()
            n_pass = ((adata.obs['sample'] == sample) & adata.obs['qc_pass']).sum()
            pct_pass = (n_pass / n_cells) * 100
            f.write(f"  {sample}: {n_cells} cells total, {n_pass} passed QC ({pct_pass:.1f}%)\n")
        f.write("\n")
        
        f.write("OVERALL QC SUMMARY:\n")
        f.write("-" * 80 + "\n")
        n_before = adata.n_obs
        n_after = adata.obs['qc_pass'].sum()
        n_removed = n_before - n_after
        pct_removed = (n_removed / n_before) * 100
        
        f.write(f"  Total cells before filtering: {n_before}\n")
        f.write(f"  Total cells after filtering: {n_after}\n")
        f.write(f"  Cells removed: {n_removed} ({pct_removed:.2f}%)\n\n")
        
        f.write("QC METRIC STATISTICS (all cells):\n")
        f.write("-" * 80 + "\n")
        metrics = ['total_counts', 'n_genes_by_counts', 'pct_counts_mt', 'pct_counts_ribo']
        for metric in metrics:
            if metric in adata.obs.columns:
                f.write(f"\n  {metric}:\n")
                f.write(f"    Mean: {adata.obs[metric].mean():.2f}\n")
                f.write(f"    Median: {adata.obs[metric].median():.2f}\n")
                f.write(f"    Min: {adata.obs[metric].min():.2f}\n")
                f.write(f"    Max: {adata.obs[metric].max():.2f}\n")
                f.write(f"    5th percentile: {adata.obs[metric].quantile(0.05):.2f}\n")
                f.write(f"    95th percentile: {adata.obs[metric].quantile(0.95):.2f}\n")
        
        f.write("\n" + "=" * 80 + "\n")
    
    print(f"  Saved QC report to {report_path}")

def main():
    parser = argparse.ArgumentParser(description='Ovarian Cancer scRNA-seq QC Pipeline')
    parser.add_argument('--input_dir', required=True, help='Input directory containing 10x sample directories')
    parser.add_argument('--output_dir', required=True, help='Output directory for QC results')
    parser.add_argument('--min_counts', type=int, default=500, help='Minimum total counts threshold')
    parser.add_argument('--max_counts', type=int, default=50000, help='Maximum total counts threshold')
    parser.add_argument('--min_genes', type=int, default=200, help='Minimum genes threshold')
    parser.add_argument('--max_genes', type=int, default=6000, help='Maximum genes threshold')
    parser.add_argument('--max_mt_pct', type=float, default=20.0, help='Maximum mitochondrial percentage')
    parser.add_argument('--max_ribo_pct', type=float, default=100.0, help='Maximum ribosomal percentage')
    parser.add_argument('--save_filtered', action='store_true', default=True, help='Save filtered AnnData object')
    
    args = parser.parse_args()
    
    print("=" * 80)
    print("OVARIAN CANCER scRNA-seq QUALITY CONTROL PIPELINE")
    print("=" * 80)
    print(f"\nInput directory: {args.input_dir}")
    print(f"Output directory: {args.output_dir}\n")
    
    # Create output directory
    output_path = Path(args.output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Get sample directories
    sample_dirs = [d for d in Path(args.input_dir).iterdir() if d.is_dir()]
    sample_dirs = sorted(sample_dirs, key=lambda x: x.name)
    
    print(f"Found {len(sample_dirs)} sample directories:")
    for d in sample_dirs:
        print(f"  - {d.name}")
    print()
    
    # Load all samples
    all_adatas = []
    for sample_dir in sample_dirs:
        try:
            adata = load_sample(str(sample_dir), sample_dir.name)
            all_adatas.append(adata)
        except Exception as e:
            print(f"ERROR loading {sample_dir.name}: {e}")
    
    if not all_adatas:
        print("ERROR: No samples loaded successfully")
        sys.exit(1)
    
    # Concatenate all samples
    print("\nConcatenating all samples...")
    adata_combined = sc.AnnData.concatenate(*all_adatas, join='inner', batch_key='sample', batch_categories=[d.name for d in sample_dirs])
    print(f"Combined dataset: {adata_combined.n_obs} cells, {adata_combined.n_vars} genes\n")
    
    # Compute QC metrics
    adata_combined = compute_qc_metrics(adata_combined)
    
    # Apply filtering
    filtering_params = {
        'min_counts': args.min_counts,
        'max_counts': args.max_counts,
        'min_genes': args.min_genes,
        'max_genes': args.max_genes,
        'max_mt_pct': args.max_mt_pct,
        'max_ribo_pct': args.max_ribo_pct
    }
    
    thresholds = {
        'min_counts': args.min_counts,
        'max_counts': args.max_counts,
        'min_genes': args.min_genes,
        'max_genes': args.max_genes,
        'max_mt_pct': args.max_mt_pct,
        'max_ribo_pct': args.max_ribo_pct,
        'input_dir': args.input_dir
    }

    adata_combined = apply_filtering(adata_combined, **filtering_params)
    
    # Save raw QC metrics
    raw_metrics_path = output_path / 'raw_qc_metrics.csv'
    adata_combined.obs.to_csv(raw_metrics_path)
    print(f"\nSaved raw QC metrics to {raw_metrics_path}")
    
    # Generate QC plots
    generate_qc_plots(adata_combined, str(output_path))
    
    # Save QC report
    save_qc_report(adata_combined, str(output_path), thresholds)
    
    # Save filtered data
    if args.save_filtered:
        filtered_adata = adata_combined[adata_combined.obs['qc_pass']].copy()
        filtered_path = output_path / 'filtered_adata.h5ad'
        filtered_adata.write_h5ad(filtered_path)
        print(f"\nSaved filtered AnnData object to {filtered_path}")
        
        # Save filtered QC metrics
        filtered_metrics_path = output_path / 'filtered_qc_metrics.csv'
        filtered_adata.obs.to_csv(filtered_metrics_path)
        print(f"Saved filtered QC metrics to {filtered_metrics_path}")
    
    print("\n" + "=" * 80)
    print("QC PIPELINE COMPLETED SUCCESSFULLY")
    print("=" * 80)

if __name__ == '__main__':
    main()
