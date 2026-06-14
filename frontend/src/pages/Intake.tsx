import { useEffect, useState } from 'react';
import { Spin, Steps, Typography } from 'antd';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import UploadMode from './intake/Upload';
import RecognizingMode from './intake/Recognizing';
import ColorMode from './intake/Color';
import IntakeError from './intake/IntakeError';
import DraftMode from './intake/Draft';
import PreviewMode from './intake/Preview';
import type { FinalDraft } from './intake/Preview';
import SuccessMode from './intake/Success';

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
  | {
      kind: 'previewing';
      finalDraft: FinalDraft;
      sessionId: string;
      // 失败回 color mode 需要 — 透传上游 draft/variants 上下文
      colorContext?: { draft: any; variants: any[] };
    }
  | { kind: 'merging' }
  | {
      kind: 'success';
      stats: { components_added: number; plates_added: number; products_added: number };
      backupPath: string;
      timingMs: Record<string, number>;
      variantNames: string[];
    }
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
      // CUJ-5 merge 失败时携带：用于「返回上一步」回到 color，以及在错误详情里显示 backup_path
      mergeContext?: {
        finalDraft: FinalDraft;
        sessionId: string;
        colorContext?: { draft: any; variants: any[] };
      };
      mergeDetails?: any;
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
  const navigate = useNavigate();
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
        <ColorMode
          draft={mode.draft}
          onBack={() => setMode({ kind: 'draft', draft: mode.draft, conflicts: [] })}
          onProceedToPreview={(finalDraft) => setMode({ kind: 'previewing', finalDraft })}
        />
      )}
      {mode.kind === 'previewing' && (
        <PreviewMode
          finalDraft={mode.finalDraft}
          sessionId={mode.sessionId}
          onBack={() => {
            if (mode.colorContext) {
              setMode({ kind: 'color', ...mode.colorContext });
            } else {
              setMode({ kind: 'upload' });
            }
          }}
          onMerging={() => setMode({ kind: 'merging' })}
          onSuccess={(stats, backupPath, timingMs) =>
            setMode({
              kind: 'success',
              stats,
              backupPath,
              timingMs,
              variantNames: mode.finalDraft.variants.map((v) => v.variant_name),
            })
          }
          onError={(errorKind, error, details) =>
            setMode({
              kind: 'error',
              variant: 'merge',
              errorKind,
              error,
              mergeContext: {
                finalDraft: mode.finalDraft,
                sessionId: mode.sessionId,
                colorContext: mode.colorContext,
              },
              mergeDetails: details,
            })
          }
        />
      )}
      {mode.kind === 'merging' && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            minHeight: 360,
          }}
        >
          <Spin size="large" tip="正在合并到 catalog.yaml..." />
        </div>
      )}
      {mode.kind === 'success' && (
        <SuccessMode
          stats={mode.stats}
          backupPath={mode.backupPath}
          timingMs={mode.timingMs}
          variantNames={mode.variantNames}
          onContinue={() => {
            setMode({ kind: 'upload' });
            setProductBaseName('');
          }}
          onGotoProducts={() => navigate('/products')}
        />
      )}
      {mode.kind === 'error' && (
        <IntakeError
          variant={mode.variant}
          errorKind={mode.errorKind}
          error={mode.error}
          rawPreview={mode.rawPreview}
          details={mode.mergeDetails}
          onRetry={() => {
            if (mode.variant === 'recognize') {
              if (mode.recognizeContext) {
                setMode({ kind: 'recognizing', ...mode.recognizeContext });
              } else {
                setMode({ kind: 'upload' });
              }
            } else {
              // merge variant 在错误页直接走 onBack 按钮（无独立 retry），此分支用作兜底回到 color。
              if (mode.mergeContext?.colorContext) {
                setMode({ kind: 'color', ...mode.mergeContext.colorContext });
              } else {
                setMode({ kind: 'upload' });
              }
            }
          }}
          onBack={() => {
            if (mode.variant === 'recognize') {
              setMode({ kind: 'upload' });
            } else {
              // merge 失败「返回上一步调整」：回到 color mode 让用户改名 / 改颜色。
              if (mode.mergeContext?.colorContext) {
                setMode({ kind: 'color', ...mode.mergeContext.colorContext });
              } else {
                setMode({ kind: 'upload' });
              }
            }
          }}
        />
      )}
    </div>
  );
}

// Re-exported so subsequent task placeholders can import the union type.
export type { IntakeMode };
