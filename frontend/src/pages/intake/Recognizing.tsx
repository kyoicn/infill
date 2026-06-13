import { useEffect, useRef, useState } from 'react';
import { Button, Progress, Steps, Typography } from 'antd';
import { api } from '../../api/client';

interface RecognizingModeProps {
  assemblyCount: number;
  produceCount: number;
  productBaseName: string;
  sessionId: string;
  assemblyImageIds: string[];
  produceImageIds: string[];
  onCancel: () => void;
  onSuccess: (draft: any, conflicts: any[]) => void;
  onError: (errorKind: string, error: string, rawPreview?: string) => void;
}

const TIMEOUT_MS = 90_000;

export default function RecognizingMode(props: RecognizingModeProps) {
  const {
    assemblyCount,
    produceCount,
    productBaseName,
    sessionId,
    assemblyImageIds,
    produceImageIds,
    onCancel,
    onSuccess,
    onError,
  } = props;

  const totalCount = assemblyCount + produceCount;

  // 进度条从 30% 渐进到 95%（不回退、不到 100% — 完成时直接跳转）。
  const [percent, setPercent] = useState(30);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    controllerRef.current = controller;

    const timeoutId = window.setTimeout(() => {
      controller.abort();
    }, TIMEOUT_MS);

    // 进度条线性推进：90 秒内从 30% 走到 95%。
    const startedAt = Date.now();
    const tickId = window.setInterval(() => {
      const elapsed = Date.now() - startedAt;
      const next = Math.min(95, 30 + (elapsed / TIMEOUT_MS) * 65);
      setPercent(next);
    }, 400);

    api.intake
      .recognize(
        {
          session_id: sessionId,
          assembly_image_ids: assemblyImageIds,
          produce_image_ids: produceImageIds,
          product_base_name: productBaseName,
        },
        controller.signal,
      )
      .then((res: any) => {
        if (res?.ok === true) {
          onSuccess(res.draft, res.conflicts ?? []);
        } else {
          onError(res?.error_kind ?? 'parse_failed', res?.error ?? '未知错误', res?.raw_response_preview);
        }
      })
      .catch((err: any) => {
        if (controller.signal.aborted) {
          // 超时分支
          onError('timeout', '连接超时 — 90 秒未收到响应');
        } else {
          onError('network', err?.message ?? String(err));
        }
      });

    return () => {
      window.clearTimeout(timeoutId);
      window.clearInterval(tickId);
      // 注意：cleanup 不主动 abort — 让 in-flight 调用自然完成。
      // 主动取消通过「取消」按钮触发。
    };
    // 仅挂载时触发一次；props 在 mode 切换时整体重建组件。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleCancel = () => {
    controllerRef.current?.abort();
    onCancel();
  };

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
        <div style={{ fontSize: 24, fontWeight: 600, color: 'rgba(0,0,0,0.88)', marginBottom: 8 }}>
          正在识别 {totalCount} 张图片…
        </div>
        <Typography.Text type="secondary" style={{ fontSize: 14, marginBottom: 32 }}>
          完成后会自动跳转到草稿预览页
        </Typography.Text>

        <div
          style={{
            fontSize: 12,
            color: 'rgba(0,0,0,0.45)',
            fontFamily: '"SF Mono", Menlo, Consolas, monospace',
            marginBottom: 24,
            display: 'flex',
            gap: 16,
          }}
        >
          <span>产品基名 {productBaseName || '（未填）'}</span>
          <span>·</span>
          <span>组装图 {assemblyCount} 张</span>
          <span>·</span>
          <span>打印盘 {produceCount} 张</span>
        </div>

        <div style={{ width: 480, marginBottom: 32 }}>
          <Steps
            size="small"
            current={1}
            items={[
              { title: '上传图片' },
              { title: '调用 LLM 识别' },
              { title: '解析返回数据' },
            ]}
          />
        </div>

        <div style={{ width: 360, marginBottom: 12 }}>
          <Progress
            percent={Math.round(percent)}
            showInfo={false}
            strokeColor={{ from: '#1677ff', to: '#4096ff' }}
            strokeLinecap="round"
          />
        </div>
        <Typography.Text type="secondary" style={{ fontSize: 13, marginBottom: 32 }}>
          约 30 秒，请稍候…
        </Typography.Text>

        <Button onClick={handleCancel} style={{ marginBottom: 16 }}>
          取消
        </Button>

        <div
          style={{
            fontSize: 12,
            color: 'rgba(0,0,0,0.45)',
            maxWidth: 480,
            textAlign: 'center',
            lineHeight: 1.6,
          }}
        >
          识别期间请勿关闭页面。如果耗时超过 2 分钟仍未完成，可能是网络问题或 LLM 服务异常，可点击取消后重试。
        </div>
      </div>
    </div>
  );
}

export type { RecognizingModeProps };
