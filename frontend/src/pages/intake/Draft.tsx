import { useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import {
  Alert,
  Button,
  Drawer,
  Input,
  InputNumber,
  Select,
  Table,
  Tooltip,
  Typography,
} from 'antd';
import {
  ArrowRightOutlined,
  CloseOutlined,
  EyeOutlined,
  PictureOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import { formatDuration, parseDuration } from './durationFormat';

// ---------- types ----------

export interface DraftComponent {
  name: string;
  assembly_quantity: number;
}

export interface DraftPlate {
  plate_name: string;
  component_name: string;
  quantity_per_plate: number;
  duration_minutes: number;
  source_image_id: string;
}

export interface DraftConflict {
  kind: 'component' | 'plate' | 'product';
  name: string;
  existing_name: string;
}

export interface DraftPayload {
  product_base_name: string;
  components: DraftComponent[];
  plates: DraftPlate[];
}

export interface DraftModeProps {
  draft: DraftPayload;
  conflicts: DraftConflict[];
  sessionId: string;
  assemblyImageIds: string[];
  onBack: () => void;
  onProceedToColor: (editedDraft: DraftPayload) => void;
}

// 内部 plate 行：除 duration_minutes 外，额外维护 duration 字符串（含未提交的非法输入）
interface PlateRow extends DraftPlate {
  duration_text: string;
  duration_valid: boolean;
}

const C = {
  primary: '#1677ff',
  red: '#ff4d4f',
  conflictBg: '#fff2f0',
  criticalBg: '#fafdff',
  criticalBorder: '#bfdfff',
  text3: 'rgba(0,0,0,0.45)',
  text2: 'rgba(0,0,0,0.65)',
  border: '#d9d9d9',
  borderLight: '#f0f0f0',
};

// ----- helpers -----

function makePlateRow(p: DraftPlate): PlateRow {
  return {
    ...p,
    duration_text: formatDuration(p.duration_minutes),
    duration_valid: true,
  };
}

function stripRowMeta(row: PlateRow): DraftPlate {
  const { duration_text: _t, duration_valid: _v, ...rest } = row;
  return rest;
}

// 当用户在顶部改了产品基名 oldBase -> newBase，把以 oldBase- 开头的名字替换前缀。
function renamePrefix(name: string, oldBase: string, newBase: string): string {
  if (!oldBase) return name;
  if (name === oldBase) return newBase;
  if (name.startsWith(oldBase + '-')) {
    return newBase + name.slice(oldBase.length);
  }
  return name;
}

// ----- component -----

export default function DraftMode(props: DraftModeProps) {
  const { draft, conflicts, sessionId, onBack, onProceedToColor } = props;

  const [baseName, setBaseName] = useState<string>(draft.product_base_name || '');
  const [components, setComponents] = useState<DraftComponent[]>(() =>
    draft.components.map((c) => ({ ...c })),
  );
  // 哪些组件 index 用户手改过名 - 自动重命名跳过它
  const [dirtyComponents, setDirtyComponents] = useState<Set<number>>(() => new Set());
  const [plateRows, setPlateRows] = useState<PlateRow[]>(() =>
    draft.plates.map(makePlateRow),
  );
  // 哪些 plate index 用户手改过盘号
  const [dirtyPlates, setDirtyPlates] = useState<Set<number>>(() => new Set());

  // drawer state
  const [assemblyDrawerImageId, setAssemblyDrawerImageId] = useState<string | null>(null);
  const [plateDrawerIndex, setPlateDrawerIndex] = useState<number | null>(null);

  // ---------- derived: conflict lookup sets ----------

  const componentConflictSet = useMemo(() => {
    const s = new Set<string>();
    for (const c of conflicts) if (c.kind === 'component') s.add(c.name);
    return s;
  }, [conflicts]);

  const plateConflictSet = useMemo(() => {
    const s = new Set<string>();
    for (const c of conflicts) if (c.kind === 'plate') s.add(c.name);
    return s;
  }, [conflicts]);

  const productConflictName = useMemo(() => {
    for (const c of conflicts) if (c.kind === 'product') return c.name;
    return null;
  }, [conflicts]);

  // 由当前 components 推导可用「所属组件」下拉项 (会随用户编辑同步)
  const componentNameOptions = useMemo(
    () => components.map((c) => ({ label: c.name, value: c.name })),
    [components],
  );

  // ---------- mutations ----------

  function changeBaseName(next: string) {
    const old = baseName;
    setBaseName(next);
    // 同步 BOM 中未 dirty 的组件名
    setComponents((prev) =>
      prev.map((c, i) =>
        dirtyComponents.has(i) ? c : { ...c, name: renamePrefix(c.name, old, next) },
      ),
    );
    // 同步打印盘中未 dirty 的盘号 + 所属组件名（component_name 直接跟随 BOM 改名）
    setPlateRows((prev) =>
      prev.map((row, i) => ({
        ...row,
        plate_name: dirtyPlates.has(i) ? row.plate_name : renamePrefix(row.plate_name, old, next),
        component_name: renamePrefix(row.component_name, old, next),
      })),
    );
  }

  function updateComponentName(idx: number, next: string) {
    const oldName = components[idx]?.name ?? '';
    setComponents((prev) => prev.map((c, i) => (i === idx ? { ...c, name: next } : c)));
    setDirtyComponents((prev) => {
      const ns = new Set(prev);
      ns.add(idx);
      return ns;
    });
    // 联动：所有引用 oldName 的打印盘行同步 component_name
    if (oldName && oldName !== next) {
      setPlateRows((prev) =>
        prev.map((row) =>
          row.component_name === oldName ? { ...row, component_name: next } : row,
        ),
      );
    }
  }

  function updateComponentQty(idx: number, q: number | null) {
    setComponents((prev) =>
      prev.map((c, i) => (i === idx ? { ...c, assembly_quantity: q ?? 0 } : c)),
    );
  }

  function addComponent() {
    setComponents((prev) => [...prev, { name: '', assembly_quantity: 1 }]);
  }

  function removeComponent(idx: number) {
    setComponents((prev) => prev.filter((_, i) => i !== idx));
    // 调整 dirty 集合的索引
    setDirtyComponents((prev) => {
      const ns = new Set<number>();
      for (const v of prev) {
        if (v < idx) ns.add(v);
        else if (v > idx) ns.add(v - 1);
      }
      return ns;
    });
  }

  function updatePlateField<K extends keyof PlateRow>(idx: number, key: K, value: PlateRow[K]) {
    setPlateRows((prev) => prev.map((r, i) => (i === idx ? { ...r, [key]: value } : r)));
  }

  function updatePlateName(idx: number, next: string) {
    updatePlateField(idx, 'plate_name', next);
    setDirtyPlates((prev) => {
      const ns = new Set(prev);
      ns.add(idx);
      return ns;
    });
  }

  function updatePlateDurationText(idx: number, text: string) {
    const parsed = parseDuration(text);
    setPlateRows((prev) =>
      prev.map((r, i) =>
        i === idx
          ? {
              ...r,
              duration_text: text,
              duration_valid: parsed !== null,
              duration_minutes: parsed !== null ? parsed : r.duration_minutes,
            }
          : r,
      ),
    );
  }

  function addPlate() {
    const firstComp = components[0]?.name ?? '';
    setPlateRows((prev) => [
      ...prev,
      {
        plate_name: '',
        component_name: firstComp,
        quantity_per_plate: 1,
        duration_minutes: 0,
        source_image_id: '',
        duration_text: '0m',
        duration_valid: true,
      },
    ]);
  }

  function removePlate(idx: number) {
    setPlateRows((prev) => prev.filter((_, i) => i !== idx));
    setDirtyPlates((prev) => {
      const ns = new Set<number>();
      for (const v of prev) {
        if (v < idx) ns.add(v);
        else if (v > idx) ns.add(v - 1);
      }
      return ns;
    });
  }

  // ---------- validation ----------

  const hasUnresolvedConflict =
    components.some((c) => componentConflictSet.has(c.name)) ||
    plateRows.some((r) => plateConflictSet.has(r.plate_name)) ||
    (productConflictName !== null && baseName === productConflictName);
  const hasInvalidDuration = plateRows.some((r) => !r.duration_valid);
  const hasNonPositiveQty =
    components.some((c) => !(c.assembly_quantity > 0)) ||
    plateRows.some((r) => !(r.quantity_per_plate > 0));
  const bomEmpty = components.length === 0;

  const nextDisabledReason = bomEmpty
    ? '组件清单不能为空'
    : hasUnresolvedConflict
      ? '撞名未解决 — 请改名或返回上一步'
      : hasInvalidDuration
        ? '存在不合法的耗时格式'
        : hasNonPositiveQty
          ? '件数必须大于 0'
          : '';

  function handleNext() {
    if (nextDisabledReason) return;
    onProceedToColor({
      product_base_name: baseName,
      components: components.map((c) => ({ ...c })),
      plates: plateRows.map(stripRowMeta),
    });
  }

  // ---------- render ----------

  return (
    <div>
      {conflicts.length > 0 && (
        <Alert
          type="error"
          showIcon
          message={`检测到 ${conflicts.length} 处与现有目录重名 — 请改名或确认合并`}
          description="合并到 catalog 时同名条目会冲突。改名能避免误覆盖；保留同名会在合并前再次确认。"
          style={{ marginBottom: 16 }}
        />
      )}

      {/* 产品基名 */}
      <div style={styles.card}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <label style={{ color: C.text2, width: 88, flexShrink: 0 }}>产品基名</label>
          <Input
            value={baseName}
            onChange={(e) => changeBaseName(e.target.value)}
            style={{ width: 280 }}
            placeholder="如：床头柜"
          />
          <span style={{ fontSize: 12, color: C.text3 }}>
            改产品基名时未手改过的组件名 / 盘号会同步更新前缀
          </span>
        </div>
      </div>

      {/* BOM */}
      <div style={styles.card}>
        <SectionTitle
          title="组件清单 (BOM)"
          desc="装配一套产品所需各组件及数量 · 高亮字段为关键校对项"
        />

        {/* assembly 缩略图横排 */}
        {props.assemblyImageIds.length > 0 && (
          <div style={styles.thumbRow}>
            {props.assemblyImageIds.map((id) => (
              <AssemblyThumb
                key={id}
                sessionId={sessionId}
                imageId={id}
                onClick={() => setAssemblyDrawerImageId(id)}
              />
            ))}
          </div>
        )}

        <Table<DraftComponent & { __idx: number }>
          rowKey="__idx"
          size="small"
          pagination={false}
          dataSource={components.map((c, i) => ({ ...c, __idx: i }))}
          rowClassName={(row) =>
            componentConflictSet.has(row.name) ? 'intake-conflict-row' : ''
          }
          onRow={(row) =>
            componentConflictSet.has(row.name)
              ? { style: { background: C.conflictBg } }
              : {}
          }
          columns={[
            {
              title: '组件名',
              key: 'name',
              width: 320,
              render: (_: unknown, row) => {
                const isConflict = componentConflictSet.has(row.name);
                return (
                  <div>
                    <Input
                      value={row.name}
                      onChange={(e) => updateComponentName(row.__idx, e.target.value)}
                      style={{
                        width: 220,
                        ...(isConflict ? { borderColor: C.red, color: C.red } : null),
                      }}
                    />
                    {isConflict && (
                      <div style={{ marginTop: 4 }}>
                        <Typography.Text type="danger" style={{ fontSize: 11 }}>
                          目录中已存在同名「{row.name}」
                        </Typography.Text>
                      </div>
                    )}
                  </div>
                );
              },
            },
            {
              title: '装配件数',
              key: 'qty',
              width: 120,
              render: (_: unknown, row) => (
                <InputNumber
                  min={1}
                  value={row.assembly_quantity}
                  onChange={(v) => updateComponentQty(row.__idx, v as number | null)}
                  style={{ width: 80, ...styles.criticalInput }}
                />
              ),
            },
            {
              title: '',
              key: 'remove',
              width: 48,
              render: (_: unknown, row) => (
                <Button
                  type="text"
                  size="small"
                  icon={<CloseOutlined />}
                  onClick={() => removeComponent(row.__idx)}
                  title="删除组件"
                />
              ),
            },
          ]}
        />
        <div style={styles.tblAdd}>
          <Button
            type="dashed"
            block
            icon={<PlusOutlined />}
            onClick={addComponent}
          >
            增加组件
          </Button>
        </div>
      </div>

      {/* 打印盘 */}
      <div style={styles.card}>
        <SectionTitle
          title="打印盘清单"
          desc="每张打印盘的产量、耗时和所属组件 · 默认盘号「组件名-件数」拼接"
        />
        <Table<PlateRow & { __idx: number }>
          rowKey="__idx"
          size="small"
          pagination={false}
          dataSource={plateRows.map((r, i) => ({ ...r, __idx: i }))}
          onRow={(row) =>
            plateConflictSet.has(row.plate_name)
              ? { style: { background: C.conflictBg } }
              : {}
          }
          columns={[
            {
              title: '盘号',
              key: 'plate_name',
              width: 260,
              render: (_: unknown, row) => {
                const isConflict = plateConflictSet.has(row.plate_name);
                return (
                  <div>
                    <Input
                      value={row.plate_name}
                      onChange={(e) => updatePlateName(row.__idx, e.target.value)}
                      style={{
                        width: 200,
                        ...(isConflict ? { borderColor: C.red, color: C.red } : null),
                      }}
                    />
                    {isConflict && (
                      <div style={{ marginTop: 4 }}>
                        <Typography.Text type="danger" style={{ fontSize: 11 }}>
                          目录中已存在同盘号「{row.plate_name}」
                        </Typography.Text>
                      </div>
                    )}
                  </div>
                );
              },
            },
            {
              title: '所属组件',
              key: 'component_name',
              width: 200,
              render: (_: unknown, row) => (
                <Select
                  value={row.component_name || undefined}
                  options={componentNameOptions}
                  onChange={(v) => updatePlateField(row.__idx, 'component_name', v)}
                  style={{ width: 180 }}
                  placeholder="选择所属组件"
                />
              ),
            },
            {
              title: '单盘件数',
              key: 'qty',
              width: 120,
              render: (_: unknown, row) => (
                <InputNumber
                  min={1}
                  value={row.quantity_per_plate}
                  onChange={(v) =>
                    updatePlateField(row.__idx, 'quantity_per_plate', (v as number) ?? 0)
                  }
                  style={{ width: 80, ...styles.criticalInput }}
                />
              ),
            },
            {
              title: '耗时',
              key: 'duration',
              width: 140,
              render: (_: unknown, row) => {
                const invalid = !row.duration_valid;
                const input = (
                  <Input
                    value={row.duration_text}
                    onChange={(e) => updatePlateDurationText(row.__idx, e.target.value)}
                    style={{
                      width: 100,
                      ...styles.criticalInput,
                      ...(invalid ? { borderColor: C.red, color: C.red } : null),
                    }}
                  />
                );
                if (invalid) {
                  return (
                    <Tooltip title="格式应为 2h43m / 17m45s / 30m">{input}</Tooltip>
                  );
                }
                return input;
              },
            },
            {
              title: '原图复核',
              key: 'preview',
              width: 96,
              render: (_: unknown, row) => (
                <Button
                  size="small"
                  icon={<EyeOutlined />}
                  onClick={() => setPlateDrawerIndex(row.__idx)}
                  disabled={!row.source_image_id}
                  title={row.source_image_id ? '查看原图' : '无原图引用'}
                />
              ),
            },
            {
              title: '',
              key: 'remove',
              width: 48,
              render: (_: unknown, row) => (
                <Button
                  type="text"
                  size="small"
                  icon={<CloseOutlined />}
                  onClick={() => removePlate(row.__idx)}
                  title="删除打印盘"
                />
              ),
            },
          ]}
        />
        <div style={styles.tblAdd}>
          <Button type="dashed" block icon={<PlusOutlined />} onClick={addPlate}>
            增加打印盘
          </Button>
        </div>
      </div>

      {/* footer */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: 12,
        }}
      >
        <Button onClick={onBack}>← 上一步：调整截图</Button>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          {nextDisabledReason && (
            <span style={{ fontSize: 12, color: C.text3 }}>{nextDisabledReason}</span>
          )}
          <Tooltip title={nextDisabledReason || undefined} placement="topLeft">
            <Button type="primary" disabled={!!nextDisabledReason} onClick={handleNext}>
              下一步：填写颜色 <ArrowRightOutlined />
            </Button>
          </Tooltip>
        </div>
      </div>

      {/* assembly drawer */}
      <Drawer
        title={
          assemblyDrawerImageId
            ? `组装图原图 · ${assemblyDrawerImageId.slice(0, 8)}…`
            : '组装图原图'
        }
        placement="right"
        width="min(900px, 90vw)"
        open={assemblyDrawerImageId !== null}
        onClose={() => setAssemblyDrawerImageId(null)}
      >
        <ImagePreview sessionId={sessionId} imageId={assemblyDrawerImageId ?? ''} />
      </Drawer>

      {/* plate drawer */}
      <Drawer
        title={
          plateDrawerIndex !== null && plateRows[plateDrawerIndex]
            ? `原图复核 · ${plateRows[plateDrawerIndex].plate_name}`
            : '原图复核'
        }
        placement="right"
        width="min(900px, 90vw)"
        open={plateDrawerIndex !== null}
        onClose={() => setPlateDrawerIndex(null)}
      >
        {plateDrawerIndex !== null && plateRows[plateDrawerIndex] && (
          <div>
            <ImagePreview sessionId={sessionId} imageId={plateRows[plateDrawerIndex].source_image_id} />
            <MetaRow label="image_id" value={plateRows[plateDrawerIndex].source_image_id || '（无）'} />
            <MetaRow
              label="LLM 识别件数"
              value={String(plateRows[plateDrawerIndex].quantity_per_plate)}
            />
            <MetaRow
              label="LLM 识别耗时"
              value={plateRows[plateDrawerIndex].duration_text}
            />
            <MetaRow
              label="所属组件"
              value={plateRows[plateDrawerIndex].component_name || '（未选择）'}
            />
          </div>
        )}
      </Drawer>
    </div>
  );
}

// ---------- subcomponents ----------

function SectionTitle(p: { title: string; desc: string }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>{p.title}</h3>
        <span style={{ fontSize: 12, color: C.text3 }}>{p.desc}</span>
      </div>
    </div>
  );
}

function AssemblyThumb(p: { sessionId: string; imageId: string; onClick: () => void }) {
  const [errored, setErrored] = useState(false);
  return (
    <div
      onClick={p.onClick}
      style={{
        width: 280,
        border: `1px solid ${C.borderLight}`,
        borderRadius: 4,
        background: '#fff',
        cursor: 'zoom-in',
        overflow: 'hidden',
        transition: 'all 0.15s',
      }}
      title={`点击查看大图 · ${p.imageId}`}
    >
      <div
        style={{
          width: '100%',
          height: 200,
          background: '#1e1e1e',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          position: 'relative',
        }}
      >
        {p.sessionId && p.imageId && !errored ? (
          <img
            src={`/api/intake/session-image/${p.sessionId}/${p.imageId}`}
            alt={p.imageId}
            onError={() => setErrored(true)}
            style={{
              maxWidth: '100%',
              maxHeight: '100%',
              objectFit: 'contain',
            }}
          />
        ) : (
          <div style={{ color: '#aaa', fontSize: 11, textAlign: 'center' }}>
            <PictureOutlined style={{ fontSize: 24, opacity: 0.5, display: 'block', marginBottom: 4 }} />
            {errored ? '加载失败' : '无图'}
          </div>
        )}
        <span
          style={{
            position: 'absolute',
            bottom: 4,
            right: 4,
            fontSize: 11,
            background: 'rgba(0,0,0,0.55)',
            color: '#fff',
            padding: '1px 6px',
            borderRadius: 2,
          }}
        >
          🔍 点击放大
        </span>
      </div>
    </div>
  );
}

function ImagePreview(p: { sessionId: string; imageId: string }) {
  const [errored, setErrored] = useState(false);

  if (!p.imageId || !p.sessionId || errored) {
    return (
      <div
        style={{
          background: '#1e1e1e',
          borderRadius: 8,
          padding: 16,
          minHeight: 320,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          marginBottom: 16,
          color: '#aaa',
          fontSize: 12,
          textAlign: 'center',
        }}
      >
        <div>
          <PictureOutlined style={{ fontSize: 48, marginBottom: 12, display: 'block' }} />
          {p.imageId ? '原图加载失败或会话已过期' : '无关联图片'}
          {p.imageId && (
            <>
              <br />
              <span style={{ fontSize: 11 }}>image_id: {p.imageId}</span>
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        background: '#1e1e1e',
        borderRadius: 8,
        padding: 16,
        minHeight: 320,
        marginBottom: 16,
        textAlign: 'center',
      }}
    >
      <img
        src={`/api/intake/session-image/${p.sessionId}/${p.imageId}`}
        alt={p.imageId}
        onError={() => setErrored(true)}
        style={{
          maxWidth: '100%',
          maxHeight: '80vh',   // 填满 drawer 高度方向，长截图允许滚动查看
          objectFit: 'contain',
          borderRadius: 4,
        }}
      />
    </div>
  );
}

function MetaRow(p: { label: string; value: string }) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        padding: '10px 0',
        borderBottom: `1px solid ${C.borderLight}`,
        fontSize: 13,
      }}
    >
      <span style={{ color: C.text3 }}>{p.label}</span>
      <span style={{ color: 'rgba(0,0,0,0.85)', fontWeight: 500, wordBreak: 'break-all' }}>
        {p.value}
      </span>
    </div>
  );
}

// ---------- styles ----------

const styles: Record<string, CSSProperties> = {
  card: {
    background: '#fff',
    borderRadius: 8,
    padding: '16px 20px',
    marginBottom: 16,
  },
  thumbRow: {
    display: 'flex',
    gap: 10,
    margin: '0 0 16px 0',
    flexWrap: 'wrap',
  },
  tblAdd: {
    marginTop: 12,
  },
  criticalInput: {
    borderColor: C.criticalBorder,
    background: C.criticalBg,
    fontWeight: 600,
    color: C.primary,
  },
};
