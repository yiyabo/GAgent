import { describe, expect, it } from 'vitest';

import {
  collectInlineImageKeys,
  filterInlinedGalleryItems,
  type ArtifactGalleryItem,
} from './artifactGallery';

const item = (path: string, display_name?: string): ArtifactGalleryItem => ({
  path,
  display_name,
  mime_family: 'image',
});

describe('collectInlineImageKeys', () => {
  it('collects basenames and stems from inline image refs', () => {
    const keys = collectInlineImageKeys(
      '前文\n![柱状图](deliverables/latest/image_tabular/group_bar.png)\n后文',
    );
    expect(keys.has('group_bar.png')).toBe(true);
    expect(keys.has('group_bar')).toBe(true);
  });

  it('returns an empty set for text without images', () => {
    expect(collectInlineImageKeys('纯文字回复').size).toBe(0);
    expect(collectInlineImageKeys(null).size).toBe(0);
  });
});

describe('filterInlinedGalleryItems', () => {
  const markdown =
    '分析如下：\n\n![组间比较](deliverables/latest/image_tabular/egfr_group_comparison.png)\n\n如上图所示……';

  it('drops gallery items already inlined in the reply', () => {
    const items = [
      item('deliverables/latest/image_tabular/egfr_group_comparison.png'),
      item('_scratch/run_1/deliverables/egfr_group_comparison.png'),
      item('results/run_2/figures/sensitivity_forest.png'),
    ];
    const filtered = filterInlinedGalleryItems(items, markdown);
    expect(filtered.map((i) => i.path)).toEqual(['results/run_2/figures/sensitivity_forest.png']);
  });

  it('dedupes cross-format variants sharing a stem (inline png hides gallery svg)', () => {
    const items = [item('deliverables/latest/image_tabular/egfr_group_comparison.svg')];
    expect(filterInlinedGalleryItems(items, markdown)).toEqual([]);
  });

  it('matches by display_name when present', () => {
    const items = [item('results/_scratch/run_9/x_9f2ab.png', 'egfr_group_comparison')];
    expect(filterInlinedGalleryItems(items, markdown)).toEqual([]);
  });

  it('keeps everything when nothing is inlined', () => {
    const items = [item('results/run_2/figures/sensitivity_forest.png')];
    expect(filterInlinedGalleryItems(items, '没有图片的回复')).toEqual(items);
    expect(filterInlinedGalleryItems(items, null)).toEqual(items);
  });
});
