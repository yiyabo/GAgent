import type { ChatMessage, UploadedFile } from '@/types';

export type MessageAttachment = NonNullable<NonNullable<ChatMessage['metadata']>['attachments']>[number];

/** The form an attachment takes on a sent message and in the chat request. */
export function toMessageAttachment(file: UploadedFile): MessageAttachment {
  const name = file.original_name || file.file_name;
  const isImage = Boolean(file.file_type?.startsWith('image/') || /\.(png|jpe?g|gif|webp|bmp|tiff?)$/i.test(name));
  return {
    type: isImage ? 'image' : 'file',
    path: file.file_path,
    name,
    ...(file.extracted_path ? { extracted_path: file.extracted_path } : {}),
  };
}
