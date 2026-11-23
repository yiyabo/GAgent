import { describe, expect, it } from 'vitest';
import { TEXT_EXTS } from './artifactPreview';

describe('TEXT_EXTS', () => {
  it('previews common sequence and annotation files as text', () => {
    for (const ext of ['fasta', 'fa', 'fna', 'faa', 'fastq', 'gb', 'gbk', 'gff', 'gff3', 'vcf', 'nwk']) {
      expect(TEXT_EXTS.has(ext)).toBe(true);
    }
  });
});
