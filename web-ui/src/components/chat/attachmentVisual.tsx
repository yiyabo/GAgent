import React from 'react';
import { FileImageOutlined, FileTextOutlined, FileZipOutlined, PaperClipOutlined } from '@ant-design/icons';

export interface AttachmentVisual {
  icon: React.ReactNode;
  color?: string;
}

const IMAGE_RE = /\.(png|jpe?g|gif|webp|bmp|tiff?)$/;
const ARCHIVE_RE = /\.(zip|tar|tgz|tar\.gz|tar\.bz2|tbz2?)$/;
const DOCUMENT_RE = /\.(pdf|docx?|txt|md|rtf|csv|xlsx?)$/;

export function attachmentVisual(name: string, options: { fileType?: string; category?: string; image?: boolean } = {}): AttachmentVisual {
  const lower = (name || '').toLowerCase();
  if (options.image || options.fileType?.startsWith('image/') || IMAGE_RE.test(lower)) {
    return { icon: <FileImageOutlined />, color: 'blue' };
  }
  if (options.category === 'archive' || ARCHIVE_RE.test(lower)) return { icon: <FileZipOutlined />, color: 'gold' };
  if (options.category === 'document' || options.category === 'project' || DOCUMENT_RE.test(lower)) {
    return { icon: <FileTextOutlined />, color: 'geekblue' };
  }
  return { icon: <PaperClipOutlined /> };
}
