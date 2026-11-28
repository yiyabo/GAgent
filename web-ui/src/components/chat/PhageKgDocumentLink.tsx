import React from 'react';
import { Alert, Modal, Spin, Typography } from 'antd';
import { phageKgApi, type PhageKgDocument } from '@/api/phageKg';
import { useT } from '@/i18n/useT';

export const PHAGE_KG_DOC_HREF_PREFIX = '#phage-kg-doc:';

const DOC_ID = /(^|[^\w/#:-])(doc-[0-9a-f]{32})(?:-chunk-\d+)?(?![\w-])/g;

const CITATION_ONLY_CODE = /^`[\s[\](),;，；、]*doc-[0-9a-f]{32}(?:-chunk-\d+)?(?:[\s[\](),;，；、]+doc-[0-9a-f]{32}(?:-chunk-\d+)?)*[\s[\](),;，；、]*`$/;

const SHORT_DOC_ID = /(^|[^\w/#:-])(doc-([0-9a-f]{6,31})(?:…|\.\.\.))/g;

const linkify = (text: string, fullIds: string[]): string =>
  text
    .replace(DOC_ID, (_m, lead: string, id: string) => `${lead}[${id}](${PHAGE_KG_DOC_HREF_PREFIX}${id})`)
    .replace(SHORT_DOC_ID, (match, lead: string, short: string, prefix: string) => {
      const matches = fullIds.filter((id) => id.startsWith(`doc-${prefix}`));
      return matches.length === 1 ? `${lead}[${short}](${PHAGE_KG_DOC_HREF_PREFIX}${matches[0]})` : match;
    });

/**
 * Turn knowledge-graph citations (doc-<32 hex>) into links the renderer opens in place. Code is left alone,
 * except an inline code span holding nothing but citations (models often write `[doc-...]`). An abbreviated
 * id (doc-247721a3…) links when it matches exactly one full id cited in the same message.
 */
export const linkifyPhageKgCitations = (content: string): string => {
  const fullIds = Array.from(new Set(content.match(/doc-[0-9a-f]{32}/g) ?? []));
  return content
    .split(/(```[\s\S]*?```|`[^`\n]*`|\]\([^)]*\))/)
    .map((part, index) => {
      if (index % 2 === 0) return linkify(part, fullIds);
      return CITATION_ONLY_CODE.test(part) ? linkify(part.slice(1, -1), fullIds) : part;
    })
    .join('');
};

export const PhageKgDocumentLink: React.FC<{ documentId: string; children: React.ReactNode }> = ({
  documentId,
  children,
}) => {
  const t = useT();
  const [open, setOpen] = React.useState(false);
  const [doc, setDoc] = React.useState<PhageKgDocument | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const openDocument = (event: React.MouseEvent) => {
    event.preventDefault();
    setOpen(true);
    if (doc || error) return;
    phageKgApi
      .getDocument(documentId)
      .then(setDoc)
      .catch((err: any) => setError(err?.response?.data?.detail || err?.message || t('phageKgDocument.failed')));
  };

  return (
    <>
      <Typography.Link href={`${PHAGE_KG_DOC_HREF_PREFIX}${documentId}`} onClick={openDocument}>
        {children}
      </Typography.Link>
      <Modal
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        width={860}
        title={doc?.title || doc?.file_path || documentId}
      >
        {error ? (
          <Alert type="error" showIcon message={t('phageKgDocument.failed')} description={error} />
        ) : !doc ? (
          <Spin tip={t('phageKgDocument.loading')}>
            <div style={{ minHeight: 120 }} />
          </Spin>
        ) : (
          <>
            <Typography.Paragraph type="secondary">
              {[doc.document_id, doc.file_path, doc.graph_version].filter(Boolean).join(' · ')}
            </Typography.Paragraph>
            <div style={{ whiteSpace: 'pre-wrap', maxHeight: '65vh', overflowY: 'auto', fontSize: 13 }}>
              {doc.content}
            </div>
            {doc.content_truncated && (
              <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>
                {t('phageKgDocument.truncated')}
              </Typography.Paragraph>
            )}
          </>
        )}
      </Modal>
    </>
  );
};
