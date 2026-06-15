import { useRef, useState } from 'react';
import type { CSSProperties, DragEvent, ChangeEvent } from 'react';
import { Alert, Button, Input, Tooltip, message } from 'antd';
import {
  InboxOutlined,
  LoadingOutlined,
  CloseOutlined,
  ArrowRightOutlined,
} from '@ant-design/icons';
import { api } from '../../api/client';

// Shape returned by POST /api/intake/upload (one element of `images`)
export interface UploadedImage {
  image_id: string;
  filename: string;
  suggested_class: 'assembly' | 'produce';
  width?: number;
  height?: number;
}

export interface UploadModeProps {
  providerConfigured: boolean;
  productBaseName: string;
  onProductBaseNameChange: (v: string) => void;
  componentHints: string;
  onComponentHintsChange: (v: string) => void;
  // Lifted state — owned by Intake.tsx so that 取消 / 返回 navigations preserve uploads.
  assemblyImages: UploadedImage[];
  setAssemblyImages: React.Dispatch<React.SetStateAction<UploadedImage[]>>;
  produceImages: UploadedImage[];
  setProduceImages: React.Dispatch<React.SetStateAction<UploadedImage[]>>;
  sessionId: string | null;
  setSessionId: React.Dispatch<React.SetStateAction<string | null>>;
  uploadingCount: number;
  setUploadingCount: React.Dispatch<React.SetStateAction<number>>;
  totalCount: number;
  setTotalCount: React.Dispatch<React.SetStateAction<number>>;
  onProceedToRecognize: (
    sessionId: string,
    assemblyIds: string[],
    produceIds: string[],
  ) => void;
}

const ACCEPTED_MIME = ['image/png', 'image/jpeg', 'image/webp'];

// ----- color palette (mirrors docs/ux/prd-005-intake/_shared.css) -----
const C = {
  primary: '#1677ff',
  primarySoft: '#e6f4ff',
  orange: '#fa8c16',
  assemblyBg: '#f5faff',
  assemblyBorder: '#cde3ff',
  produceBg: '#fffaf0',
  produceBorder: '#ffe4b8',
  borderLight: '#f0f0f0',
  border: '#d9d9d9',
  text3: 'rgba(0,0,0,0.45)',
  text4: 'rgba(0,0,0,0.25)',
};

// Trim filename to <=8 visible chars before extension (mock spec: "trim 后 8 字 + ellipsis")
function shortName(filename: string): string {
  const dot = filename.lastIndexOf('.');
  const stem = dot > 0 ? filename.slice(0, dot) : filename;
  const ext = dot > 0 ? filename.slice(dot) : '';
  if (stem.length <= 8) return filename;
  return stem.slice(0, 8) + '…' + ext;
}

export default function UploadMode(props: UploadModeProps) {
  const {
    providerConfigured,
    productBaseName,
    onProductBaseNameChange,
    componentHints,
    onComponentHintsChange,
    assemblyImages,
    setAssemblyImages,
    produceImages,
    setProduceImages,
    sessionId,
    setSessionId,
    uploadingCount,
    setUploadingCount,
    totalCount,
    setTotalCount,
    onProceedToRecognize,
  } = props;

  // Pure UI transient — fine to keep local; cleared on remount.
  const [dragOverCol, setDragOverCol] = useState<'assembly' | 'produce' | null>(null);

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const isEmpty =
    assemblyImages.length === 0 && produceImages.length === 0 && uploadingCount === 0;

  // ----- file selection & upload -----

  function pickFiles() {
    fileInputRef.current?.click();
  }

  function filterAndUpload(files: File[]) {
    const accepted: File[] = [];
    for (const f of files) {
      if (ACCEPTED_MIME.includes(f.type)) {
        accepted.push(f);
      } else {
        message.warning(`仅支持图片文件（JPG / PNG / WebP）— 已忽略：${f.name}`);
      }
    }
    if (accepted.length === 0) return;
    void doUpload(accepted);
  }

  async function doUpload(files: File[]) {
    setUploadingCount((c) => c + files.length);
    setTotalCount((c) => c + files.length);
    try {
      const res = (await api.intake.upload(files, sessionId)) as {
        ok?: boolean;
        session_id?: string;
        images?: UploadedImage[];
        error?: string;
      };
      if (res?.ok === false) {
        throw new Error(res.error || '上传失败');
      }
      if (sessionId === null && res?.session_id) {
        setSessionId(res.session_id);
      }
      const images: UploadedImage[] = Array.isArray(res?.images) ? res.images : [];
      const newAssembly = images.filter((i) => i.suggested_class === 'assembly');
      const newProduce = images.filter((i) => i.suggested_class === 'produce');
      if (newAssembly.length > 0) {
        setAssemblyImages((prev) => [...prev, ...newAssembly]);
      }
      if (newProduce.length > 0) {
        setProduceImages((prev) => [...prev, ...newProduce]);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : '上传失败';
      message.error(msg);
    } finally {
      setUploadingCount((c) => Math.max(0, c - files.length));
    }
  }

  function handleFileInputChange(e: ChangeEvent<HTMLInputElement>) {
    const files = e.target.files ? Array.from(e.target.files) : [];
    e.target.value = ''; // allow re-select same file
    filterAndUpload(files);
  }

  function handleDropZone(e: DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    const files = e.dataTransfer.files ? Array.from(e.dataTransfer.files) : [];
    filterAndUpload(files);
  }

  function handleDragOverZone(e: DragEvent) {
    e.preventDefault();
  }

  // ----- thumbnail removal -----

  function removeImage(imageId: string, from: 'assembly' | 'produce') {
    if (from === 'assembly') {
      setAssemblyImages((prev) => prev.filter((i) => i.image_id !== imageId));
    } else {
      setProduceImages((prev) => prev.filter((i) => i.image_id !== imageId));
    }
  }

  // ----- cross-column DnD -----

  function onThumbDragStart(
    e: DragEvent,
    imageId: string,
    from: 'assembly' | 'produce',
  ) {
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', JSON.stringify({ imageId, from }));
  }

  function onColumnDragOver(e: DragEvent, col: 'assembly' | 'produce') {
    // only react to inner-thumb drags (which set text/plain)
    if (Array.from(e.dataTransfer.types).includes('text/plain')) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      setDragOverCol(col);
    }
  }

  function onColumnDragLeave(col: 'assembly' | 'produce') {
    if (dragOverCol === col) setDragOverCol(null);
  }

  function onColumnDrop(e: DragEvent, target: 'assembly' | 'produce') {
    setDragOverCol(null);
    const raw = e.dataTransfer.getData('text/plain');
    if (!raw) return;
    let payload: { imageId: string; from: 'assembly' | 'produce' };
    try {
      payload = JSON.parse(raw);
    } catch {
      return;
    }
    if (!payload.imageId || !payload.from || payload.from === target) return;
    e.preventDefault();

    const src = payload.from === 'assembly' ? assemblyImages : produceImages;
    const img = src.find((i) => i.image_id === payload.imageId);
    if (!img) return;

    if (payload.from === 'assembly') {
      setAssemblyImages((prev) => prev.filter((i) => i.image_id !== payload.imageId));
    } else {
      setProduceImages((prev) => prev.filter((i) => i.image_id !== payload.imageId));
    }
    if (target === 'assembly') {
      setAssemblyImages((prev) => [...prev, img]);
    } else {
      setProduceImages((prev) => [...prev, img]);
    }
  }

  // ----- branch 1: no-api-key (entire page disabled + alert) -----
  if (!providerConfigured) {
    return (
      <div>
        <Alert
          type="error"
          showIcon
          message="未检测到 LLM 提供商 API key"
          description={
            <span>
              请在项目根目录 <code>.env</code> 文件中配置 <code>DEEPSEEK_API_KEY</code>
              ，参见 <code>.env.example</code>。配置后重启后端服务。
            </span>
          }
          style={{ marginBottom: 16 }}
        />
        <div style={{ opacity: 0.5, pointerEvents: 'none' }}>
          <TopFormRow
            productBaseName={productBaseName}
            onProductBaseNameChange={onProductBaseNameChange}
            componentHints={componentHints}
            onComponentHintsChange={onComponentHintsChange}
            disabled
          />
          <div
            style={{
              ...styles.dropzoneBig,
              cursor: 'not-allowed',
            }}
          >
            <InboxOutlined style={{ fontSize: 48, color: C.text4 }} />
            <div style={{ marginTop: 12, fontSize: 16, color: C.text3 }}>
              拖入截图，或点击选择文件（配置 API key 后启用）
            </div>
          </div>
        </div>
      </div>
    );
  }

  const canStart =
    assemblyImages.length > 0 &&
    produceImages.length > 0 &&
    uploadingCount === 0;

  const startDisabledReason =
    uploadingCount > 0
      ? '等待全部图片上传完成'
      : assemblyImages.length === 0 && produceImages.length === 0
        ? '需至少 1 张组装图 / 打印盘截图'
        : assemblyImages.length === 0
          ? '需至少 1 张组装图'
          : produceImages.length === 0
            ? '需至少 1 张打印盘截图'
            : '';

  return (
    <div>
      <input
        ref={fileInputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        multiple
        style={{ display: 'none' }}
        onChange={handleFileInputChange}
      />

      {/* Top form row + (when populated) mini dropzone */}
      <div style={styles.card}>
        <TopFormRow
          productBaseName={productBaseName}
          onProductBaseNameChange={onProductBaseNameChange}
          componentHints={componentHints}
          onComponentHintsChange={onComponentHintsChange}
        />
        {!isEmpty && (
          <div
            style={styles.dropzoneMini}
            onClick={pickFiles}
            onDrop={handleDropZone}
            onDragOver={handleDragOverZone}
          >
            {uploadingCount > 0 && (
              <span style={{ color: C.primary, fontWeight: 500, marginRight: 8 }}>
                上传中 {totalCount - uploadingCount} / {totalCount}
              </span>
            )}
            <b>+ 继续追加截图</b>
            <span style={{ color: C.text3, marginLeft: 6 }}>，或点击选择文件</span>
          </div>
        )}
      </div>

      {/* Main area */}
      <div style={styles.card}>
        {isEmpty ? (
          <div
            style={styles.dropzoneBig}
            onClick={pickFiles}
            onDrop={handleDropZone}
            onDragOver={handleDragOverZone}
          >
            <InboxOutlined style={{ fontSize: 48, color: C.text4 }} />
            <div style={{ marginTop: 12, fontSize: 16, fontWeight: 500 }}>
              拖入截图，或点击选择文件
            </div>
            <div style={{ marginTop: 6, fontSize: 13, color: C.text3 }}>
              支持 JPG / PNG / WebP，单张 ≤ 10MB
            </div>
          </div>
        ) : (
          <div style={styles.split}>
            <Column
              kind="assembly"
              images={assemblyImages}
              dragOver={dragOverCol === 'assembly'}
              uploadingCount={uploadingCount}
              onDragOver={(e) => onColumnDragOver(e, 'assembly')}
              onDragLeave={() => onColumnDragLeave('assembly')}
              onDrop={(e) => onColumnDrop(e, 'assembly')}
              onThumbDragStart={onThumbDragStart}
              onRemove={(id) => removeImage(id, 'assembly')}
            />
            <Column
              kind="produce"
              images={produceImages}
              dragOver={dragOverCol === 'produce'}
              uploadingCount={uploadingCount}
              onDragOver={(e) => onColumnDragOver(e, 'produce')}
              onDragLeave={() => onColumnDragLeave('produce')}
              onDrop={(e) => onColumnDrop(e, 'produce')}
              onThumbDragStart={onThumbDragStart}
              onRemove={(id) => removeImage(id, 'produce')}
            />
          </div>
        )}
      </div>

      {/* Footer */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'flex-end',
          alignItems: 'center',
          gap: 12,
        }}
      >
        {!canStart && startDisabledReason && (
          <span style={{ fontSize: 12, color: C.text3 }}>{startDisabledReason}</span>
        )}
        <Tooltip
          title={!canStart ? startDisabledReason : undefined}
          placement="topLeft"
        >
          <Button
            type="primary"
            disabled={!canStart}
            onClick={() => {
              if (!canStart || !sessionId) return;
              onProceedToRecognize(
                sessionId,
                assemblyImages.map((i) => i.image_id),
                produceImages.map((i) => i.image_id),
              );
            }}
          >
            开始识别 <ArrowRightOutlined />
          </Button>
        </Tooltip>
      </div>
    </div>
  );
}

// ---------- subcomponents ----------

function TopFormRow(p: {
  productBaseName: string;
  onProductBaseNameChange: (v: string) => void;
  componentHints: string;
  onComponentHintsChange: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          marginBottom: 12,
        }}
      >
        <label style={{ color: 'rgba(0,0,0,0.65)', width: 88, flexShrink: 0 }}>
          产品基名
        </label>
        <Input
          value={p.productBaseName}
          onChange={(e) => p.onProductBaseNameChange(e.target.value)}
          placeholder="如：床头柜（识别后可自动推断填入，也可现在手填）"
          style={{ width: 280 }}
          disabled={p.disabled}
        />
      </div>
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 12,
        }}
      >
        <label
          style={{
            color: 'rgba(0,0,0,0.65)',
            width: 88,
            flexShrink: 0,
            paddingTop: 6,
            lineHeight: '14px',
          }}
        >
          组件粒度<br />
          <span style={{ fontSize: 11, color: 'rgba(0,0,0,0.45)' }}>（可选）</span>
        </label>
        <Input.TextArea
          value={p.componentHints}
          onChange={(e) => p.onComponentHintsChange(e.target.value)}
          placeholder={
            '可选 — 告诉 LLM 你想分成几个组件、分别叫什么。空着则让 LLM 自由推断（容易过细）。\n' +
            '例：3 个组件：底柜、抽屉、抽屉把手'
          }
          autoSize={{ minRows: 2, maxRows: 4 }}
          style={{ flex: 1, maxWidth: 640 }}
          disabled={p.disabled}
        />
      </div>
    </div>
  );
}

function Column(p: {
  kind: 'assembly' | 'produce';
  images: UploadedImage[];
  dragOver: boolean;
  uploadingCount: number;
  onDragOver: (e: DragEvent) => void;
  onDragLeave: () => void;
  onDrop: (e: DragEvent) => void;
  onThumbDragStart: (
    e: DragEvent,
    imageId: string,
    from: 'assembly' | 'produce',
  ) => void;
  onRemove: (imageId: string) => void;
}) {
  const isAssembly = p.kind === 'assembly';
  const bg = isAssembly ? C.assemblyBg : C.produceBg;
  const borderColor = p.dragOver
    ? C.primary
    : isAssembly
      ? C.assemblyBorder
      : C.produceBorder;
  const dotColor = isAssembly ? C.primary : C.orange;
  const textColor = isAssembly ? C.primary : C.orange;
  const label = isAssembly ? '组装图' : '打印盘';
  const hint = isAssembly
    ? '产品多角度装配示意 / 爆炸图，用于推断 BOM'
    : '拓竹切片软件每个打印盘的预览，用于推断每盘件数与耗时';

  return (
    <div
      onDragOver={p.onDragOver}
      onDragLeave={p.onDragLeave}
      onDrop={p.onDrop}
      style={{
        background: bg,
        border: `1px solid ${borderColor}`,
        borderRadius: 8,
        padding: 16,
        boxShadow: p.dragOver ? `0 0 0 2px ${C.primarySoft}` : undefined,
        transition: 'border-color 0.15s, box-shadow 0.15s',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          flexWrap: 'wrap',
          paddingBottom: 12,
          marginBottom: 12,
          borderBottom: `1px dashed ${isAssembly ? C.assemblyBorder : C.produceBorder}`,
          position: 'sticky',
          top: 0,
          zIndex: 1,
          background: bg,
        }}
      >
        <span
          style={{
            display: 'inline-block',
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: dotColor,
          }}
        />
        <span style={{ fontWeight: 600, color: textColor }}>{label}</span>
        <span style={{ color: C.text3, fontSize: 13 }}>{p.images.length} 张</span>
        <span style={{ marginLeft: 'auto', fontSize: 12, color: C.text3 }}>{hint}</span>
      </div>

      {p.images.length === 0 ? (
        <div style={styles.colEmpty}>
          <InboxOutlined style={{ fontSize: 32, opacity: 0.4, marginBottom: 8 }} />
          <div style={{ fontSize: 13, color: C.text3 }}>
            拖入或追加 {label}截图
          </div>
          <div style={{ fontSize: 12, color: C.text4, marginTop: 4 }}>
            系统会按图像特征自动归类到此栏
          </div>
        </div>
      ) : (
        <div style={styles.thumbGrid}>
          {p.images.map((img) => (
            <Thumb
              key={img.image_id}
              image={img}
              from={p.kind}
              onDragStart={p.onThumbDragStart}
              onRemove={p.onRemove}
            />
          ))}
          {/* render placeholders representing in-flight uploads so user sees progress */}
          {p.uploadingCount > 0 && p.kind === 'assembly' && <UploadingPlaceholders />}
        </div>
      )}
    </div>
  );
}

function Thumb(p: {
  image: UploadedImage;
  from: 'assembly' | 'produce';
  onDragStart: (
    e: DragEvent,
    imageId: string,
    from: 'assembly' | 'produce',
  ) => void;
  onRemove: (imageId: string) => void;
}) {
  const [hover, setHover] = useState(false);
  return (
    <div
      draggable
      onDragStart={(e) => p.onDragStart(e, p.image.image_id, p.from)}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        position: 'relative',
        border: `1px solid ${C.borderLight}`,
        borderRadius: 4,
        background: '#fff',
        overflow: 'hidden',
        cursor: 'grab',
        userSelect: 'none',
      }}
    >
      <div
        style={{
          width: 64,
          height: 64,
          background: 'linear-gradient(135deg, #f0f0f0 0%, #e5e7eb 100%)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: C.text3,
          fontSize: 10,
        }}
      >
        {p.from === 'produce' ? '盘' : '装'}
      </div>
      {hover && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            p.onRemove(p.image.image_id);
          }}
          style={{
            position: 'absolute',
            top: 2,
            right: 2,
            background: 'rgba(0,0,0,0.55)',
            color: '#fff',
            border: 'none',
            width: 18,
            height: 18,
            borderRadius: 3,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 0,
          }}
        >
          <CloseOutlined style={{ fontSize: 10 }} />
        </button>
      )}
      <div
        style={{
          padding: '3px 6px',
          fontSize: 10,
          color: C.text3,
          borderTop: `1px solid ${C.borderLight}`,
          background: '#fff',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          maxWidth: 64,
        }}
        title={p.image.filename}
      >
        {shortName(p.image.filename)}
      </div>
    </div>
  );
}

function UploadingPlaceholders() {
  // Render a single visual placeholder showing in-flight upload (count is in mini dropzone progress text)
  return (
    <div
      style={{
        position: 'relative',
        border: `1px dashed ${C.border}`,
        borderRadius: 4,
        background: 'rgba(255,255,255,0.6)',
        width: 64,
        height: 80,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: C.primary,
      }}
    >
      <LoadingOutlined spin style={{ fontSize: 20 }} />
    </div>
  );
}

// ----- styles -----

const styles: Record<string, CSSProperties> = {
  card: {
    background: '#fff',
    borderRadius: 8,
    padding: '16px 20px',
    marginBottom: 16,
  },
  dropzoneBig: {
    minHeight: 360,
    border: `2px dashed ${C.border}`,
    borderRadius: 8,
    padding: '60px 24px',
    textAlign: 'center',
    cursor: 'pointer',
    background: '#fafafa',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
  },
  dropzoneMini: {
    marginTop: 12,
    border: `1px dashed ${C.border}`,
    borderRadius: 6,
    padding: '10px 16px',
    textAlign: 'center',
    color: 'rgba(0,0,0,0.65)',
    fontSize: 13,
    cursor: 'pointer',
    background: '#fafafa',
  },
  split: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: 16,
  },
  thumbGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(64px, 1fr))',
    gap: 10,
    maxHeight: 380,
    overflowY: 'auto',
    paddingRight: 4,
  },
  colEmpty: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '40px 16px',
    textAlign: 'center',
    color: C.text3,
    border: `1px dashed ${C.border}`,
    borderRadius: 6,
    background: 'rgba(255,255,255,0.5)',
    minHeight: 200,
  },
};
