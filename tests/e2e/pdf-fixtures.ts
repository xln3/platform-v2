import { createHash } from 'node:crypto';

function createSinglePagePdf(mediaBox: readonly [number, number, number, number]): string {
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    `<< /Type /Page /Parent 2 0 R /MediaBox [${mediaBox.join(' ')}] /Resources << >> /Contents 4 0 R >>`,
    '<< /Length 0 >>\nstream\n\nendstream',
  ];
  let pdf = '%PDF-1.4\n';
  const offsets = [0];
  objects.forEach((object, index) => {
    offsets.push(pdf.length);
    pdf += `${index + 1} 0 obj\n${object}\nendobj\n`;
  });
  const xref = pdf.length;
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  pdf += offsets
    .slice(1)
    .map((offset) => `${String(offset).padStart(10, '0')} 00000 n \n`)
    .join('');
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  return pdf;
}

export const oversizedPagePdf = createSinglePagePdf([0, 0, 20_000, 20_000]);
export const oversizedPagePdfIntegrity = {
  byteSize: new TextEncoder().encode(oversizedPagePdf).byteLength,
  sha256: createHash('sha256').update(oversizedPagePdf).digest('hex'),
} as const;
