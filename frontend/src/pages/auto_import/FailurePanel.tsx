import { Button, Modal } from 'antd';
import { ExclamationOutlined } from '@ant-design/icons';

interface FailurePanelProps {
  error: string;
  onBack: () => void;
  onDiscard: () => void;
}

export default function FailurePanel(props: FailurePanelProps) {
  const { error, onBack, onDiscard } = props;

  function confirmDiscard() {
    Modal.confirm({
      title: '丢弃本批？',
      content: '本批所有预览数据将被清除，无法找回。',
      okText: '丢弃',
      okType: 'danger',
      cancelText: '取消',
      onOk: onDiscard,
    });
  }

  return (
    <div style={{ maxWidth: 720, margin: '0 auto' }}>
      <div style={{ color: 'rgba(0,0,0,0.45)', fontSize: 13, marginBottom: 6 }}>
        订单管理 · 自动导入 · 失败
      </div>
      <h1 style={{ fontSize: 22, fontWeight: 600, margin: '0 0 24px' }}>导入失败</h1>

      <div
        style={{
          background: '#fff',
          border: '1px solid #f0f0f0',
          borderRadius: 12,
          padding: '48px 32px',
          textAlign: 'center',
        }}
      >
        <div
          style={{
            width: 72,
            height: 72,
            borderRadius: '50%',
            background: '#fff1f0',
            border: '3px solid #ff4d4f',
            color: '#ff4d4f',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 36,
            fontWeight: 700,
            marginBottom: 18,
          }}
        >
          <ExclamationOutlined />
        </div>
        <div style={{ fontSize: 20, fontWeight: 600, marginBottom: 8 }}>
          导入失败 — 未写入任何订单
        </div>
        <div
          style={{
            fontSize: 13,
            color: 'rgba(0,0,0,0.65)',
            marginBottom: 24,
          }}
        >
          后端拒绝了本批提交，事务已回滚。请根据下方错误信息修正后重试。
        </div>

        <pre
          style={{
            background: '#fafafa',
            border: '1px solid #f0f0f0',
            borderRadius: 6,
            padding: '12px 16px',
            textAlign: 'left',
            fontFamily: 'monospace',
            fontSize: 12,
            color: 'rgba(0,0,0,0.88)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            marginBottom: 28,
            maxHeight: 240,
            overflowY: 'auto',
          }}
        >
          {error || '（无详细错误信息）'}
        </pre>

        <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
          <Button type="primary" onClick={onBack}>
            返回预览继续校对
          </Button>
          <Button onClick={confirmDiscard}>丢弃本批</Button>
        </div>
      </div>
    </div>
  );
}
