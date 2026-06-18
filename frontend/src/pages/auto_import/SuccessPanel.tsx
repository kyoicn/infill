import type { AutoImportCommitResponse } from '../../api/client';

interface SuccessPanelProps {
  response: AutoImportCommitResponse;
  sourcePlatform: 'xhs' | 'xianyu';
}

export default function SuccessPanel(_props: SuccessPanelProps) {
  return <div style={{ padding: 24, color: '#999' }}>导入成功页（Task 4.3 实现中）</div>;
}
