import React, { useMemo } from 'react';
import { Button, Tooltip, Typography } from 'antd';
import type { NodeCatalog, NodeTypeSpec } from '@api/pipelines';
import { useLanguageStore } from '@/i18n/language';
import { useT } from '@/i18n/useT';
import { pick } from './graphModel';

const { Text } = Typography;

interface Props {
  catalog: NodeCatalog;
  disabled?: boolean;
  onAdd: (spec: NodeTypeSpec) => void;
}

const NodePalette: React.FC<Props> = ({ catalog, disabled, onAdd }) => {
  const t = useT();
  const language = useLanguageStore((state) => state.language);
  const groups = useMemo(
    () =>
      catalog.categories
        .map((category) => ({ category, specs: catalog.node_types.filter((spec) => spec.category === category.id) }))
        .filter((group) => group.specs.length),
    [catalog],
  );
  return (
    <div className="pl-palette">
      <Text strong>{t('pipelines.paletteTitle')}</Text>
      <Text type="secondary" className="pl-hint">
        {t('pipelines.paletteHint')}
      </Text>
      {groups.map(({ category, specs }) => (
        <div key={category.id} className="pl-palette-group">
          <div className="pl-palette-category">{pick(category.title, language)}</div>
          {specs.map((spec) => (
            <Tooltip
              key={spec.type}
              placement="right"
              title={
                <>
                  <strong>{pick(spec.title, language)}</strong>
                  <div>{pick(spec.description, language)}</div>
                </>
              }
            >
              <Button size="small" block disabled={disabled} className="pl-palette-item" onClick={() => onAdd(spec)}>
                {pick(spec.title, language)}
              </Button>
            </Tooltip>
          ))}
        </div>
      ))}
    </div>
  );
};

export default NodePalette;
