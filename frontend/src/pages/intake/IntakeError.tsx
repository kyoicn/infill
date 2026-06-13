import { Button } from 'antd';
import { CloseCircleOutlined } from '@ant-design/icons';
import { MERGE_ERROR_LABELS, RECOGNIZE_ERROR_LABELS } from './errorMessages';

interface ErrorModeProps {
  variant: 'recognize' | 'merge';
  errorKind: string;
  error: string;
  rawPreview?: string;
  onRetry: () => void;
  onBack: () => void;
}

export default function IntakeError(props: ErrorModeProps) {
  const { variant, errorKind, error, rawPreview, onRetry, onBack } = props;

  const labels = variant === 'recognize' ? RECOGNIZE_ERROR_LABELS : MERGE_ERROR_LABELS;
  const kindLabel = labels[errorKind] || errorKind;

  const title = variant === 'recognize' ? 'LLM 识别失败' : '合并失败 — 已自动回滚';
  const subtitle =
    variant === 'recognize'
      ? '已上传的图片仍保留在上一步，可调整后重试。'
      : '草稿仍保留在上一步，可调整后重试。';

  return (
    <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 24 }}>
      <div
        style={{
          width: 640,
          background: '#fff',
          borderRadius: 8,
          padding: '64px 40px',
          boxShadow: '0 2px 16px rgba(0,0,0,0.04)',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
        }}
      >
        <CloseCircleOutlined style={{ fontSize: 48, color: '#ff4d4f', marginBottom: 16 }} />

        <div style={{ fontSize: 24, fontWeight: 600, color: 'rgba(0,0,0,0.88)', marginBottom: 8 }}>
          {title}
        </div>
        <div
          style={{
            fontSize: 14,
            color: 'rgba(0,0,0,0.45)',
            marginBottom: 28,
            textAlign: 'center',
            maxWidth: 480,
          }}
        >
          {subtitle}
        </div>

        <div
          style={{
            background: '#fafafa',
            borderRadius: 4,
            padding: 12,
            borderLeft: '3px solid #ff4d4f',
            fontFamily: '"SF Mono", Menlo, Consolas, monospace',
            fontSize: 12,
            color: 'rgba(0,0,0,0.65)',
            marginBottom: 28,
            width: '100%',
            maxWidth: 520,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}
        >
          <div>错误类型：{kindLabel}</div>
          <div>原始信息：{error}</div>
          {rawPreview ? <div>LLM 返回片段：{rawPreview}</div> : null}
        </div>

        <div style={{ display: 'flex', gap: 12 }}>
          <Button onClick={onBack}>返回上一步</Button>
          <Button type="primary" onClick={onRetry}>
            重试
          </Button>
        </div>
      </div>
    </div>
  );
}

export type { ErrorModeProps };
