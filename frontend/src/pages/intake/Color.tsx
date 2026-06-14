import { useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import { Button, Input, Popover, Tooltip } from 'antd';
import { ArrowLeftOutlined, ArrowRightOutlined, CloseOutlined, CopyOutlined, PlusOutlined } from '@ant-design/icons';
import { COMMON_COLOR_NAMES, colorNameToSwatch } from './colorPalette';

interface DraftComponent {
  name: string;
  assembly_quantity: number;
}

interface ColorCell {
  component_name: string;
  color: string;
}

interface Variant {
  variant_name: string;
  color_cells: ColorCell[];
}

export interface FinalDraft {
  product_base_name: string;
  components: Array<{
    name: string;
    assembly_quantity: number;
    available_colors: string[];
  }>;
  plates: Array<any>;
  variants: Variant[];
}

export interface ColorModeProps {
  draft: {
    product_base_name: string;
    components: DraftComponent[];
    plates: Array<any>;
  };
  onBack: () => void;
  onProceedToPreview: (finalDraft: FinalDraft) => void;
}

const C = {
  primary: '#1677ff',
  primaryHover: '#4096ff',
  primarySoft: '#e6f4ff',
  red: '#ff4d4f',
  text: 'rgba(0,0,0,0.88)',
  text2: 'rgba(0,0,0,0.65)',
  text3: 'rgba(0,0,0,0.45)',
  text4: 'rgba(0,0,0,0.25)',
  border: '#d9d9d9',
  borderLight: '#f0f0f0',
};

function makeEmptyVariant(productBaseName: string, components: DraftComponent[], n: number): Variant {
  return {
    variant_name: `${productBaseName} - 配色 ${n}`,
    color_cells: components.map((c) => ({ component_name: c.name, color: '' })),
  };
}

export default function ColorMode(props: ColorModeProps) {
  const { draft, onBack, onProceedToPreview } = props;

  const [variants, setVariants] = useState<Variant[]>(() => [
    makeEmptyVariant(draft.product_base_name, draft.components, 1),
  ]);

  // ---------- derived state ----------
  // Set of colors used anywhere in any variant (dedupe, non-empty).
  const usedColors = useMemo(() => {
    const set = new Set<string>();
    for (const v of variants) {
      for (const cell of v.color_cells) {
        if (cell.color) set.add(cell.color);
      }
    }
    return Array.from(set);
  }, [variants]);

  // Variant-name validation: duplicate names are invalid.
  const duplicateNameIndices = useMemo(() => {
    const seen = new Map<string, number>();
    const dups = new Set<number>();
    variants.forEach((v, idx) => {
      const name = v.variant_name.trim();
      if (!name) return;
      if (seen.has(name)) {
        dups.add(idx); // mark the second/third occurrence as invalid
      } else {
        seen.set(name, idx);
      }
    });
    return dups;
  }, [variants]);

  const anyEmptyName = variants.some((v) => v.variant_name.trim() === '');
  const anyEmptyCell = variants.some((v) => v.color_cells.some((c) => !c.color));
  const hasDuplicateNames = duplicateNameIndices.size > 0;
  const canProceed = !anyEmptyCell && !anyEmptyName && !hasDuplicateNames;

  // ---------- mutators ----------

  function setCellColor(variantIdx: number, componentName: string, color: string) {
    setVariants((prev) => {
      const next = prev.map((v, i) => {
        if (i !== variantIdx) return v;
        return {
          ...v,
          color_cells: v.color_cells.map((c) =>
            c.component_name === componentName ? { ...c, color } : c,
          ),
        };
      });
      return next;
    });
  }

  function setVariantName(variantIdx: number, name: string) {
    setVariants((prev) =>
      prev.map((v, i) => (i === variantIdx ? { ...v, variant_name: name } : v)),
    );
  }

  function addVariant() {
    setVariants((prev) => {
      const n = prev.length + 1;
      return [...prev, makeEmptyVariant(draft.product_base_name, draft.components, n)];
    });
  }

  function duplicateVariant(variantIdx: number) {
    setVariants((prev) => {
      const src = prev[variantIdx];
      const copy: Variant = {
        variant_name: `${src.variant_name} - 副本`,
        color_cells: src.color_cells.map((c) => ({ ...c })),
      };
      const next = [...prev];
      next.splice(variantIdx + 1, 0, copy);
      return next;
    });
  }

  function deleteVariant(variantIdx: number) {
    setVariants((prev) => prev.filter((_, i) => i !== variantIdx));
  }

  // ---------- submit ----------

  function handleProceed() {
    if (!canProceed) return;
    // available_colors per component = union (dedupe) of colors that component takes across all variants.
    const components = draft.components.map((c) => {
      const colors = new Set<string>();
      for (const v of variants) {
        const cell = v.color_cells.find((cc) => cc.component_name === c.name);
        if (cell && cell.color) colors.add(cell.color);
      }
      return {
        name: c.name,
        assembly_quantity: c.assembly_quantity,
        available_colors: Array.from(colors),
      };
    });
    const finalDraft: FinalDraft = {
      product_base_name: draft.product_base_name,
      components,
      plates: draft.plates,
      variants,
    };
    onProceedToPreview(finalDraft);
  }

  // ---------- render ----------

  return (
    <div>
      <div style={styles.card}>
        <div style={styles.sectionTitle}>
          <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>配色变体</h2>
          <span style={{ fontSize: 12, color: C.text3 }}>
            为每个组件选择颜色 — 可一次配置多个变体，每个变体合并后作为独立产品条目写入 catalog
          </span>
        </div>

        <div style={{ overflowX: 'auto' }}>
          <table style={styles.matrixTbl}>
            <thead>
              <tr>
                <th style={{ ...styles.matrixTh, width: 200 }}>组件</th>
                <th style={{ ...styles.matrixTh, width: 70, textAlign: 'center', color: C.text3, fontWeight: 400 }}>
                  件数
                </th>
                {variants.map((v, idx) => (
                  <VariantHeadCell
                    key={`head-${idx}`}
                    variant={v}
                    canDelete={variants.length > 1}
                    duplicateName={duplicateNameIndices.has(idx)}
                    onChangeName={(name) => setVariantName(idx, name)}
                    onDuplicate={() => duplicateVariant(idx)}
                    onDelete={() => deleteVariant(idx)}
                  />
                ))}
                <th
                  rowSpan={draft.components.length + 1}
                  style={{
                    ...styles.matrixTh,
                    minWidth: 150,
                    background: 'transparent',
                    borderLeft: `1px dashed ${C.border}`,
                    verticalAlign: 'middle',
                    padding: 8,
                  }}
                >
                  <button onClick={addVariant} style={styles.addVariantBtn}>
                    <PlusOutlined style={{ fontSize: 14 }} />
                    <span>新增配色</span>
                  </button>
                </th>
              </tr>
            </thead>
            <tbody>
              {draft.components.map((comp) => (
                <tr key={comp.name}>
                  <td style={{ ...styles.matrixTd, fontWeight: 500, color: C.text }}>{comp.name}</td>
                  <td style={{ ...styles.matrixTd, color: C.text3, textAlign: 'center' }}>
                    ×{comp.assembly_quantity}
                  </td>
                  {variants.map((v, vIdx) => {
                    const cell = v.color_cells.find((c) => c.component_name === comp.name);
                    const color = cell?.color ?? '';
                    return (
                      <td
                        key={`cell-${vIdx}-${comp.name}`}
                        style={{
                          ...styles.matrixTd,
                          borderLeft: `1px solid ${C.borderLight}`,
                          background: 'rgba(230,244,255,0.25)',
                        }}
                      >
                        <ColorCellPicker
                          color={color}
                          usedColors={usedColors}
                          onPick={(c) => setCellColor(vIdx, comp.name, c)}
                        />
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div style={styles.colorSummary}>
          <span style={{ color: C.text3, flexShrink: 0 }}>
            本产品的可选颜色（自动汇总到 catalog）：
          </span>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {usedColors.length === 0 ? (
              <span style={{ color: C.text4, fontSize: 12 }}>（暂无 — 填齐 cell 后会自动汇总）</span>
            ) : (
              usedColors.map((c) => <SummaryChip key={c} color={c} />)
            )}
          </div>
        </div>
      </div>

      <div style={styles.footer}>
        <Button onClick={onBack} icon={<ArrowLeftOutlined />}>
          上一步：校对
        </Button>
        <Tooltip
          title={
            !canProceed
              ? anyEmptyCell
                ? '请填齐所有 cell 的颜色'
                : anyEmptyName
                  ? '变体名不可为空'
                  : '变体名不可重复'
              : undefined
          }
        >
          <Button type="primary" disabled={!canProceed} onClick={handleProceed}>
            下一步：合并 {variants.length} 个产品条目 <ArrowRightOutlined />
          </Button>
        </Tooltip>
      </div>
    </div>
  );
}

// ---------- subcomponents ----------

function VariantHeadCell(props: {
  variant: Variant;
  canDelete: boolean;
  duplicateName: boolean;
  onChangeName: (name: string) => void;
  onDuplicate: () => void;
  onDelete: () => void;
}) {
  return (
    <th
      style={{
        ...styles.matrixTh,
        minWidth: 200,
        background: C.primarySoft,
        borderLeft: `1px solid ${C.borderLight}`,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <Tooltip
          title={props.duplicateName ? '变体名不可重复 — 合并后会撞 catalog 主键' : undefined}
          open={props.duplicateName ? undefined : false}
        >
          <Input
            size="small"
            value={props.variant.variant_name}
            onChange={(e) => props.onChangeName(e.target.value)}
            style={{
              flex: 1,
              minWidth: 0,
              maxWidth: 140,
              borderColor: props.duplicateName ? C.red : undefined,
            }}
            status={props.duplicateName ? 'error' : undefined}
          />
        </Tooltip>
        <div style={{ display: 'flex', gap: 0, flexShrink: 0 }}>
          <Tooltip title="复制此列">
            <button onClick={props.onDuplicate} style={styles.variantActionBtn}>
              <CopyOutlined />
            </button>
          </Tooltip>
          {props.canDelete && (
            <Tooltip title="删除此变体">
              <button
                onClick={props.onDelete}
                style={{ ...styles.variantActionBtn, color: C.text3 }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLButtonElement).style.color = C.red;
                  (e.currentTarget as HTMLButtonElement).style.background = 'rgba(255,77,79,0.1)';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLButtonElement).style.color = C.text3;
                  (e.currentTarget as HTMLButtonElement).style.background = 'transparent';
                }}
              >
                <CloseOutlined />
              </button>
            </Tooltip>
          )}
        </div>
      </div>
    </th>
  );
}

function ColorCellPicker(props: {
  color: string;
  usedColors: string[];
  onPick: (c: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [customInput, setCustomInput] = useState('');

  function apply(c: string) {
    const cleaned = c.trim();
    if (!cleaned) return;
    props.onPick(cleaned);
    setOpen(false);
    setCustomInput('');
  }

  const content = (
    <div style={{ width: 320 }}>
      {props.usedColors.length > 0 && (
        <>
          <div style={styles.popoverSectionTitle}>本产品已用过的颜色</div>
          <div style={styles.popoverOptions}>
            {props.usedColors.map((c) => (
              <ColorChipButton key={`used-${c}`} color={c} onClick={() => apply(c)} />
            ))}
          </div>
          <div style={styles.popoverDivider} />
        </>
      )}
      <div style={styles.popoverSectionTitle}>常用颜色</div>
      <div style={styles.popoverOptions}>
        {COMMON_COLOR_NAMES.map((c) => (
          <ColorChipButton key={`common-${c}`} color={c} onClick={() => apply(c)} />
        ))}
      </div>
      <div style={styles.popoverDivider} />
      <div style={styles.popoverSectionTitle}>输入新颜色名</div>
      <div style={{ display: 'flex', gap: 6 }}>
        <Input
          size="small"
          placeholder="例如：丝绸粉、奶白"
          value={customInput}
          onChange={(e) => setCustomInput(e.target.value)}
          onPressEnter={() => apply(customInput)}
          style={{ flex: 1 }}
        />
        <Button size="small" type="primary" onClick={() => apply(customInput)}>
          添加
        </Button>
      </div>
    </div>
  );

  const empty = !props.color;
  const swatchHex = empty ? null : colorNameToSwatch(props.color);

  return (
    <Popover
      content={content}
      trigger="click"
      open={open}
      onOpenChange={setOpen}
      placement="bottomLeft"
      destroyOnHidden
    >
      <div
        role="button"
        style={{
          ...styles.colorCell,
          borderColor: open ? C.primary : C.borderLight,
          boxShadow: open ? `0 0 0 2px rgba(22,119,255,0.15)` : undefined,
        }}
      >
        <span style={swatchStyle(swatchHex)} />
        <span
          style={{
            flex: 1,
            fontSize: 13,
            color: empty ? C.text4 : C.text,
          }}
        >
          {empty ? '选择颜色' : props.color}
        </span>
        <span style={{ color: C.text3, fontSize: 9, flexShrink: 0 }}>▾</span>
      </div>
    </Popover>
  );
}

function ColorChipButton(props: { color: string; onClick: () => void }) {
  const hex = colorNameToSwatch(props.color);
  return (
    <button onClick={props.onClick} style={styles.popoverOption}>
      <span
        style={{
          width: 12,
          height: 12,
          borderRadius: 2,
          border: '1px solid rgba(0,0,0,0.15)',
          flexShrink: 0,
          background: hex ?? swatchStripeBg(),
        }}
      />
      <span>{props.color}</span>
    </button>
  );
}

function SummaryChip(props: { color: string }) {
  const hex = colorNameToSwatch(props.color);
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        padding: '2px 8px',
        background: '#fff',
        border: `1px solid ${C.borderLight}`,
        borderRadius: 10,
        fontSize: 12,
      }}
    >
      <span
        style={{
          width: 10,
          height: 10,
          borderRadius: 2,
          border: '1px solid rgba(0,0,0,0.15)',
          background: hex ?? swatchStripeBg(),
        }}
      />
      {props.color}
    </span>
  );
}

// ---------- swatch helpers ----------

function swatchStripeBg(): string {
  // 45° 灰白斜纹
  return 'repeating-linear-gradient(45deg, #f5f5f5, #f5f5f5 3px, #fff 3px, #fff 6px)';
}

function swatchStyle(hex: string | null): CSSProperties {
  return {
    width: 16,
    height: 16,
    borderRadius: 3,
    border: `1px solid ${hex ? 'rgba(0,0,0,0.15)' : C.borderLight}`,
    flexShrink: 0,
    background: hex ?? swatchStripeBg(),
  };
}

// ---------- styles ----------

const styles: Record<string, CSSProperties> = {
  card: {
    background: '#fff',
    borderRadius: 8,
    padding: '20px 24px',
    marginBottom: 16,
  },
  sectionTitle: {
    display: 'flex',
    alignItems: 'baseline',
    gap: 10,
    marginBottom: 14,
    flexWrap: 'wrap',
  },
  matrixTbl: {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: 13,
    minWidth: 700,
  },
  matrixTh: {
    background: '#fafafa',
    fontWeight: 500,
    color: C.text2,
    padding: '10px 12px',
    borderBottom: `1px solid ${C.borderLight}`,
    textAlign: 'left',
    verticalAlign: 'top',
    whiteSpace: 'nowrap',
  },
  matrixTd: {
    padding: '8px 12px',
    borderBottom: `1px solid ${C.borderLight}`,
    verticalAlign: 'middle',
  },
  addVariantBtn: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    width: '100%',
    height: '100%',
    minHeight: 36,
    gap: 2,
    background: 'transparent',
    border: `1px dashed ${C.border}`,
    borderRadius: 6,
    cursor: 'pointer',
    color: C.text3,
    fontFamily: 'inherit',
    fontSize: 13,
    padding: 8,
  },
  variantActionBtn: {
    background: 'transparent',
    border: 'none',
    cursor: 'pointer',
    padding: '2px 6px',
    fontSize: 12,
    color: C.text3,
    borderRadius: 4,
    fontFamily: 'inherit',
    height: 24,
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  colorCell: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    cursor: 'pointer',
    padding: '5px 8px 5px 6px',
    border: `1px solid ${C.borderLight}`,
    borderRadius: 4,
    background: '#fff',
    transition: 'all 0.15s',
    userSelect: 'none',
  },
  colorSummary: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    marginTop: 16,
    padding: '12px 16px',
    background: '#fafafa',
    borderRadius: 6,
    fontSize: 13,
    flexWrap: 'wrap',
  },
  popoverSectionTitle: {
    fontSize: 11,
    color: C.text3,
    marginBottom: 6,
  },
  popoverOptions: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 4,
    marginBottom: 4,
  },
  popoverOption: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    padding: '5px 10px',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 13,
    border: `1px solid ${C.borderLight}`,
    background: '#fff',
    fontFamily: 'inherit',
  },
  popoverDivider: {
    height: 1,
    background: C.borderLight,
    margin: '10px 0',
  },
  footer: {
    display: 'flex',
    justifyContent: 'space-between',
    padding: '4px 0',
    gap: 12,
  },
};
