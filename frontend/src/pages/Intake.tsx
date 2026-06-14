import { useEffect, useState } from 'react';
import { Steps, Typography } from 'antd';
import { api } from '../api/client';
import UploadMode from './intake/Upload';
import RecognizingMode from './intake/Recognizing';
import IntakeError from './intake/IntakeError';
import DraftMode from './intake/Draft';

type IntakeMode =
  | { kind: 'upload' }
  | {
      kind: 'recognizing';
      sessionId: string;
      assemblyImageIds: string[];
      produceImageIds: string[];
      productBaseName: string;
    }
  | {
      kind: 'draft';
      draft: any;
      conflicts: any[];
      sessionId: string;
      assemblyImageIds: string[];
      produceImageIds: string[];
    }
  | { kind: 'color'; draft: any; variants: any[] }
  | { kind: 'previewing'; finalDraft: any }
  | { kind: 'merging' }
  | { kind: 'success'; stats: any; backupPath: string; timingMs: Record<string, number> }
  | {
      kind: 'error';
      variant: 'recognize' | 'merge';
      errorKind: string;
      error: string;
      rawPreview?: string;
      // 失败前的上下文：用于「重试」回到上一步重新触发
      recognizeContext?: {
        sessionId: string;
        assemblyImageIds: string[];
        produceImageIds: string[];
        productBaseName: string;
      };
    };

const STEP_ITEMS = [
  { title: '上传截图' },
  { title: '识别' },
  { title: '校对' },
  { title: '颜色' },
  { title: '合并' },
];

function stepIndex(kind: IntakeMode['kind']): number {
  switch (kind) {
    case 'upload':
      return 0;
    case 'recognizing':
      return 1;
    case 'draft':
      return 2;
    case 'color':
      return 3;
    case 'previewing':
    case 'merging':
    case 'success':
    case 'error':
      return 4;
  }
}

function pageTitle(kind: IntakeMode['kind'], variant?: 'recognize' | 'merge'): string {
  switch (kind) {
    case 'upload':
      return '产品录入';
    case 'recognizing':
      return '产品录入 · 识别中';
    case 'draft':
      return '产品录入 · 草稿校对';
    case 'color':
      return '产品录入 · 填写颜色';
    case 'previewing':
      return '产品录入 · 合并到 catalog';
    case 'merging':
      return '产品录入 · 合并到 catalog';
    case 'success':
      return '产品录入 · 完成';
    case 'error':
      return variant === 'merge' ? '产品录入 · 合并失败' : '产品录入 · 识别失败';
  }
}

export default function Intake() {
  const [mode, setMode] = useState<IntakeMode>({ kind: 'upload' });
  const [providerConfigured, setProviderConfigured] = useState<boolean>(false);
  const [productBaseName, setProductBaseName] = useState<string>('');

  useEffect(() => {
    api.intake
      .providerStatus()
      .then((s: any) => setProviderConfigured(Boolean(s?.configured)))
      .catch(() => setProviderConfigured(false));
  }, []);

  useEffect(() => {
    document.title = pageTitle(mode.kind, mode.kind === 'error' ? mode.variant : undefined);
  }, [mode]);

  const current = stepIndex(mode.kind);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <h2 style={{ margin: 0 }}>产品录入</h2>
        <div style={{ minWidth: 480, maxWidth: 640, flex: '0 1 auto' }}>
          <Steps size="small" current={current} items={STEP_ITEMS} />
        </div>
      </div>
      <div style={{ marginBottom: 16 }}>
        <Typography.Text type="secondary">
          拖入拓竹切片软件的截图，系统会自动识别 BOM 与打印盘信息并合并到 catalog.yaml
        </Typography.Text>
      </div>

      {mode.kind === 'upload' && (
        <UploadMode
          providerConfigured={providerConfigured}
          productBaseName={productBaseName}
          onProductBaseNameChange={setProductBaseName}
          onProceedToRecognize={(sessionId, assemblyImageIds, produceImageIds) =>
            setMode({
              kind: 'recognizing',
              sessionId,
              assemblyImageIds,
              produceImageIds,
              productBaseName,
            })
          }
        />
      )}
      {mode.kind === 'recognizing' && (
        <RecognizingMode
          assemblyCount={mode.assemblyImageIds.length}
          produceCount={mode.produceImageIds.length}
          productBaseName={mode.productBaseName}
          sessionId={mode.sessionId}
          assemblyImageIds={mode.assemblyImageIds}
          produceImageIds={mode.produceImageIds}
          onCancel={() => setMode({ kind: 'upload' })}
          onSuccess={(draft, conflicts) =>
            setMode({
              kind: 'draft',
              draft,
              conflicts,
              sessionId: mode.sessionId,
              assemblyImageIds: mode.assemblyImageIds,
              produceImageIds: mode.produceImageIds,
            })
          }
          onError={(errorKind, error, rawPreview) =>
            setMode({
              kind: 'error',
              variant: 'recognize',
              errorKind,
              error,
              rawPreview,
              recognizeContext: {
                sessionId: mode.sessionId,
                assemblyImageIds: mode.assemblyImageIds,
                produceImageIds: mode.produceImageIds,
                productBaseName: mode.productBaseName,
              },
            })
          }
        />
      )}
      {mode.kind === 'draft' && (
        <DraftMode
          draft={mode.draft}
          conflicts={mode.conflicts || []}
          sessionId={mode.sessionId}
          assemblyImageIds={mode.assemblyImageIds}
          onBack={() => setMode({ kind: 'upload' })}
          onProceedToColor={(editedDraft) =>
            setMode({ kind: 'color', draft: editedDraft, variants: [] })
          }
        />
      )}
      {mode.kind === 'color' && (
        <div>color mode placeholder — to be implemented by subsequent tasks</div>
      )}
      {mode.kind === 'previewing' && (
        <div>previewing mode placeholder — to be implemented by subsequent tasks</div>
      )}
      {mode.kind === 'merging' && (
        <div>merging mode placeholder — to be implemented by subsequent tasks</div>
      )}
      {mode.kind === 'success' && (
        <div>success mode placeholder — to be implemented by subsequent tasks</div>
      )}
      {mode.kind === 'error' && (
        <IntakeError
          variant={mode.variant}
          errorKind={mode.errorKind}
          error={mode.error}
          rawPreview={mode.rawPreview}
          onRetry={() => {
            if (mode.variant === 'recognize') {
              if (mode.recognizeContext) {
                setMode({ kind: 'recognizing', ...mode.recognizeContext });
              } else {
                setMode({ kind: 'upload' });
              }
            } else {
              // merge 失败重试：回到 color 步骤（T10 实现细化）。
              setMode({ kind: 'upload' });
            }
          }}
          onBack={() => {
            if (mode.variant === 'recognize') {
              setMode({ kind: 'upload' });
            } else {
              // merge 失败返回：回到 color 步骤（T10 实现细化）。
              setMode({ kind: 'upload' });
            }
          }}
        />
      )}
    </div>
  );
}

// Re-exported so subsequent task placeholders can import the union type.
export type { IntakeMode };
