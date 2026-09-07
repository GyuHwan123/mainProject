const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
const ALLOWED_EXTENSIONS = new Set(['pdf', 'png', 'jpg', 'jpeg', 'webp', 'bmp', 'tif', 'tiff', 'docx', 'xlsx', 'xlsm', 'txt', 'md', 'csv']);
const MIME_BY_EXTENSION = {
  pdf: ['application/pdf'], png: ['image/png'], jpg: ['image/jpeg'], jpeg: ['image/jpeg'], webp: ['image/webp'],
  bmp: ['image/bmp', 'image/x-ms-bmp'], tif: ['image/tiff'], tiff: ['image/tiff'],
  docx: ['application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/zip'],
  xlsx: ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'application/zip'],
  xlsm: ['application/vnd.ms-excel.sheet.macroenabled.12', 'application/zip'],
  txt: ['text/plain'], md: ['text/plain', 'text/markdown'], csv: ['text/plain', 'text/csv', 'application/csv', 'application/vnd.ms-excel'],
};

const startsWith = (bytes, signature) => signature.every((value, index) => bytes[index] === value);
const ascii = (bytes, start, length) => String.fromCharCode(...bytes.slice(start, start + length));

export async function validateFileBeforeUpload(file) {
  const extension = String(file?.name || '').split('.').pop()?.toLowerCase() || '';
  if (!ALLOWED_EXTENSIONS.has(extension)) throw new Error('지원하지 않는 파일 형식입니다.');
  if (!file.size) throw new Error('빈 파일은 업로드할 수 없습니다.');
  if (file.size > MAX_UPLOAD_BYTES) throw new Error('파일은 최대 50MB까지 업로드할 수 있습니다.');
  const mime = String(file.type || '').split(';')[0].toLowerCase();
  if (mime && mime !== 'application/octet-stream' && !MIME_BY_EXTENSION[extension].includes(mime)) {
    throw new Error('파일 확장자와 MIME 형식이 일치하지 않습니다.');
  }
  const head = new Uint8Array(await file.slice(0, 16).arrayBuffer());
  const signatureValid = {
    pdf: ascii(head, 0, 5) === '%PDF-', png: startsWith(head, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    jpg: startsWith(head, [0xff, 0xd8, 0xff]), jpeg: startsWith(head, [0xff, 0xd8, 0xff]),
    webp: ascii(head, 0, 4) === 'RIFF' && ascii(head, 8, 4) === 'WEBP', bmp: ascii(head, 0, 2) === 'BM',
    tif: startsWith(head, [0x49, 0x49, 0x2a, 0x00]) || startsWith(head, [0x4d, 0x4d, 0x00, 0x2a]),
    tiff: startsWith(head, [0x49, 0x49, 0x2a, 0x00]) || startsWith(head, [0x4d, 0x4d, 0x00, 0x2a]),
    docx: startsWith(head, [0x50, 0x4b]), xlsx: startsWith(head, [0x50, 0x4b]), xlsm: startsWith(head, [0x50, 0x4b]),
  }[extension];
  if (signatureValid === false) throw new Error('파일 확장자와 실제 파일 형식이 다르거나 파일이 손상되었습니다.');
  if (['txt', 'md', 'csv'].includes(extension) && head.includes(0)) throw new Error('텍스트 파일에 바이너리 데이터가 포함되어 있습니다.');
  return true;
}

export async function validateFilesBeforeUpload(files) {
  for (const file of files) await validateFileBeforeUpload(file);
  return files;
}
