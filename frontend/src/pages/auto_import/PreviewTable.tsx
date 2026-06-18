import type { AutoImportScanResponse, AutoImportCommitItem } from '../../api/client';

interface PreviewTableProps {
  batch: AutoImportScanResponse;
  sourcePlatform: 'xhs' | 'xianyu';
  onCommit: (items: AutoImportCommitItem[]) => void;
  onCancel: () => void;
}

export default function PreviewTable(_props: PreviewTableProps) {
  return <div style={{ padding: 24, color: '#999' }}>预览校对表（Task 4.3 实现中）</div>;
}
