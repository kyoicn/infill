interface FailurePanelProps {
  error: string;
  onBack: () => void;
  onDiscard: () => void;
}

export default function FailurePanel(_props: FailurePanelProps) {
  return <div style={{ padding: 24, color: '#999' }}>导入失败页（Task 4.3 实现中）</div>;
}
