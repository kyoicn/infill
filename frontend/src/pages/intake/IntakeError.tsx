import { useState } from 'react';
import { Button, Input, Modal, message } from 'antd';
import { CloseCircleOutlined } from '@ant-design/icons';
import { api } from '../../api/client';
import { ERROR_SUGGESTIONS, MERGE_ERROR_LABELS, RECOGNIZE_ERROR_LABELS } from './errorMessages';

interface ErrorModeProps {
  variant: 'recognize' | 'merge';
  errorKind: string;
  error: string;
  rawPreview?: string;
  // CUJ-5 失败时后端 details 里可能含 backup_path / 冲突列表
  details?: any;
  onRetry: () => void;
  onBack: () => void;
}

export default function IntakeError(props: ErrorModeProps) {
  const { variant, errorKind, error, rawPreview, details, onRetry, onBack } = props;

  const [logsOpen, setLogsOpen] = useState(false);
  const [logsLoading, setLogsLoading] = useState(false);
  const [logsText, setLogsText] = useState<string>('');

  const labels = variant === 'recognize' ? RECOGNIZE_ERROR_LABELS : MERGE_ERROR_LABELS;
  const kindLabel = labels[errorKind] || errorKind;

  const title = variant === 'recognize' ? 'LLM 识别失败' : '合并失败 — 已自动回滚';
  const subtitle =
    variant === 'recognize'
      ? '已上传的图片仍保留在上一步，可调整后重试。'
      : '草稿仍保留在上一步，可调整后重试。';

  const backupPath =
    variant === 'merge'
      ? (details && typeof details === 'object' && details.backup_path) || ''
      : '';
  const suggestion =
    variant === 'merge' ? ERROR_SUGGESTIONS[errorKind] || '检查后端日志或调整草稿后重试' : '';

  async function openLogs() {
    setLogsOpen(true);
    setLogsLoading(true);
    setLogsText('');
    try {
      const res = (await api.intake.recentLogs(100)) as { ok?: boolean; logs?: string; error?: string };
      if (res?.ok === false) {
        setLogsText(`[加载失败] ${res.error || '后端拒绝请求'}`);
      } else {
        setLogsText(typeof res?.logs === 'string' ? res.logs : JSON.stringify(res, null, 2));
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setLogsText(`[加载失败] ${msg}`);
      message.error('加载日志失败');
    } finally {
      setLogsLoading(false);
    }
  }

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
            lineHeight: 1.8,
          }}
        >
          <div>错误类型：{kindLabel}</div>
          <div>原始信息：{error}</div>
          {variant === 'merge' ? (
            <>
              <div>已回滚至：{backupPath || '（无备份）'}</div>
              <div>建议：{suggestion}</div>
            </>
          ) : (
            rawPreview ? <div>LLM 返回片段：{rawPreview}</div> : null
          )}
        </div>

        <div style={{ display: 'flex', gap: 12 }}>
          {variant === 'merge' ? (
            <>
              <Button onClick={openLogs}>查看后端日志</Button>
              <Button type="primary" onClick={onBack}>
                返回上一步调整
              </Button>
            </>
          ) : (
            <>
              <Button onClick={onBack}>返回上一步</Button>
              <Button type="primary" onClick={onRetry}>
                重试
              </Button>
            </>
          )}
        </div>
      </div>

      <Modal
        title="后端最近 100 行日志"
        open={logsOpen}
        onCancel={() => setLogsOpen(false)}
        footer={[
          <Button key="close" onClick={() => setLogsOpen(false)}>
            关闭
          </Button>,
        ]}
        width={720}
      >
        <Input.TextArea
          readOnly
          value={logsLoading ? '加载中…' : logsText}
          autoSize={{ minRows: 18, maxRows: 24 }}
          style={{
            fontFamily: '"SF Mono", Menlo, Consolas, monospace',
            fontSize: 12,
          }}
        />
      </Modal>
    </div>
  );
}

export type { ErrorModeProps };
