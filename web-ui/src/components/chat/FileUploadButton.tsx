import React from 'react';
import { Button, message, Tooltip, Upload } from 'antd';
import { PaperClipOutlined } from '@ant-design/icons';
import { useChatStore } from '@store/chat';

interface FileUploadButtonProps {
  size?: 'small' | 'middle' | 'large';
}

const ALLOWED_EXTENSIONS = [
  '.pdf',
  '.doc',
  '.docx',
  '.txt',
  '.md',
  '.rtf',
  '.csv',
  '.jpg',
  '.jpeg',
  '.png',
  '.gif',
  '.webp',
  '.bmp',
  '.tif',
  '.tiff',
  '.zip',
  '.tar',
  '.tar.gz',
  '.tgz',
  '.tar.bz2',
  '.tbz',
  '.tbz2',
  '.gz',
  '.h5',
  '.hdf5',
  '.hdf',
  '.hd5',
  '.pdb',
  '.dcm',
  '.nii',
  '.nii.gz',
  '.npz',
  '.npy',
  // Bioinformatics file formats
  '.fasta',
  '.fa',
  '.fna',
  '.faa',
  '.ffn',
  '.frn',
  '.fastq',
  '.fq',
  '.fastq.gz',
  '.fq.gz',
  '.gff',
  '.gff3',
  '.gtf',
  '.vcf',
  '.vcf.gz',
  '.sam',
  '.bam',
  '.bed',
  '.bed.gz',
  '.genbank',
  '.gb',
  '.gbk',
  '.embl',
  '.phy',
  '.phylip',
  '.nwk',
  '.newick',
  '.aln',
  '.clustal',
];

const ALLOWED_MIME_TYPES = new Set([
  'application/pdf',
  'application/msword',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'text/plain',
  'text/markdown',
  'text/csv',
  'application/rtf',
  'application/zip',
  'application/x-zip-compressed',
  'application/x-tar',
  'application/gzip',
  'application/x-gzip',
  'application/octet-stream',  // Binary bio files (BAM, etc.)
]);

const FileUploadButton: React.FC<FileUploadButtonProps> = ({ size = 'middle' }) => {
  const { uploadFile, currentSession } = useChatStore();
  const [uploading, setUploading] = React.useState(false);

  const handleUpload = async (file: File) => {
    if (!currentSession) {
      message.error('Please create or select a session first.');
      return false;
    }

    setUploading(true);
    try {
      await uploadFile(file);
      message.success(`File ${file.name} uploaded successfully.`);
      return false; // Prevent default upload behavior.
    } catch (error: any) {
      message.error(`Upload failed: ${error.message || 'Unknown error'}`);
      return false;
    } finally {
      setUploading(false);
    }
  };

  const beforeUpload = (file: File) => {
    const fileName = file.name.toLowerCase();
    const isAllowed =
      file.type.startsWith('image/') ||
      ALLOWED_MIME_TYPES.has(file.type) ||
      ALLOWED_EXTENSIONS.some((ext) => fileName.endsWith(ext));

    if (!isAllowed) {
      message.error('Unsupported file type.');
      return Upload.LIST_IGNORE;
    }

    // Execute upload
    handleUpload(file);
    return false; // Prevent default upload
  };

  const tooltip = 'Upload file';
  const acceptList = [
    'image/*',
    'application/pdf',
    ...ALLOWED_EXTENSIONS,
  ].join(',');

  return (
    <Upload
      beforeUpload={beforeUpload}
      showUploadList={false}
      accept={acceptList}
      multiple={false}
    >
      <Tooltip title={tooltip}>
        <Button
          type="text"
          size={size}
          icon={<PaperClipOutlined />}
          loading={uploading}
          disabled={!currentSession}
        />
      </Tooltip>
    </Upload>
  );
};

export default FileUploadButton;
