import type { AutoImportScanResponse } from '../../api/client';

interface XianyuTabProps {
  onScan: (batchId: string, scan: AutoImportScanResponse) => void;
  otherInProgress: boolean;
}

export default function XianyuTab(_props: XianyuTabProps) {
  return <div style={{ padding: 24, color: '#999' }}>闲鱼标签页（Task 4.2 实现中）</div>;
}
