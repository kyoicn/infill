import { useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import { Button, Tag, Tooltip, Typography } from 'antd';
import { ArrowLeftOutlined, CheckOutlined } from '@ant-design/icons';
import { api } from '../../api/client';

// ---------- types (mirror backend schemas_intake.FinalDraft) ----------

export interface PreviewComponent {
  name: string;
  assembly_quantity: number;
  available_colors: string[];
}

export interface PreviewPlate {
  plate_name: string;
  component_name: string;
  quantity_per_plate: number;
  duration_minutes: number;
}

export interface PreviewColorCell {
  component_name: string;
  color: string;
}

export interface PreviewVariant {
  variant_name: string;
  color_cells: PreviewColorCell[];
}

export interface FinalDraft {
  product_base_name: string;
  components: PreviewComponent[];
  plates: PreviewPlate[];
  variants: PreviewVariant[];
}

export interface PreviewModeProps {
  finalDraft: FinalDraft;
  sessionId: string;
  onBack: () => void;
  onMerging: () => void;
  onSuccess: (
    stats: { components_added: number; plates_added: number; products_added: number; new_skus: string[] },
    backupPath: string,
    timingMs: Record<string, number>,
  ) => void;
  onError: (errorKind: string, error: string, details?: any) => void;
}

// ---------- duration formatter ----------

function formatDuration(totalMinutes: number): string {
  const h = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h${m}m`;
}

// ---------- minimal YAML serializer ----------
//
// 仅支持 dict / list / str / int — 与 catalog.yaml 结构契合（参见 data/catalog.yaml.example）。
// 中文字符不转义；只在字符串包含 YAML 危险字符时才加双引号。
// 输出对齐后端 expand_to_yaml_structures（CUJ-5 设计），保持「预览 == 实际写入内容」。

type YamlValue = string | number | boolean | YamlValue[] | { [k: string]: YamlValue };

function needsQuote(s: string): boolean {
  if (s === '') return true;
  if (/^[-:?!&*|>%@`]/.test(s)) return true;
  if (/[:#]\s/.test(s)) return true;
  if (/^\s|\s$/.test(s)) return true;
  if (/^(true|false|null|yes|no|on|off)$/i.test(s)) return true;
  if (/^-?\d+(\.\d+)?$/.test(s)) return true;
  return false;
}

function yamlScalar(v: string | number | boolean): string {
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  return needsQuote(v) ? `"${v.replace(/"/g, '\\"')}"` : v;
}

function yamlInlineList(arr: (string | number | boolean)[]): string {
  return `[${arr.map(yamlScalar).join(', ')}]`;
}

// dump value at given indent (number of leading spaces for keys/items).
// Top-level call: indent=0.
function dumpDict(obj: { [k: string]: YamlValue }, indent: number): string {
  const pad = ' '.repeat(indent);
  const lines: string[] = [];
  for (const [k, v] of Object.entries(obj)) {
    if (Array.isArray(v)) {
      lines.push(`${pad}${k}:`);
      lines.push(dumpList(v, indent + 2));
    } else if (typeof v === 'object' && v !== null) {
      lines.push(`${pad}${k}:`);
      lines.push(dumpDict(v as { [k: string]: YamlValue }, indent + 2));
    } else {
      lines.push(`${pad}${k}: ${yamlScalar(v as string | number | boolean)}`);
    }
  }
  return lines.join('\n');
}

function dumpList(arr: YamlValue[], indent: number): string {
  const pad = ' '.repeat(indent);
  const lines: string[] = [];
  for (const item of arr) {
    if (Array.isArray(item)) {
      // inline list of scalars (common case: 可选颜色)
      if (item.every((x) => typeof x === 'string' || typeof x === 'number' || typeof x === 'boolean')) {
        lines.push(`${pad}- ${yamlInlineList(item as (string | number | boolean)[])}`);
      } else {
        lines.push(`${pad}-`);
        lines.push(dumpList(item, indent + 2));
      }
    } else if (typeof item === 'object' && item !== null) {
      const entries = Object.entries(item as { [k: string]: YamlValue });
      if (entries.length === 0) {
        lines.push(`${pad}- {}`);
        continue;
      }
      const [firstK, firstV] = entries[0];
      // first key on the dash line; subsequent keys aligned to indent+2
      if (Array.isArray(firstV)) {
        if (firstV.every((x) => typeof x === 'string' || typeof x === 'number' || typeof x === 'boolean')) {
          lines.push(`${pad}- ${firstK}: ${yamlInlineList(firstV as (string | number | boolean)[])}`);
        } else {
          lines.push(`${pad}- ${firstK}:`);
          lines.push(dumpList(firstV, indent + 4));
        }
      } else if (typeof firstV === 'object' && firstV !== null) {
        lines.push(`${pad}- ${firstK}:`);
        lines.push(dumpDict(firstV as { [k: string]: YamlValue }, indent + 4));
      } else {
        lines.push(`${pad}- ${firstK}: ${yamlScalar(firstV as string | number | boolean)}`);
      }
      const restPad = ' '.repeat(indent + 2);
      for (const [k, v] of entries.slice(1)) {
        if (Array.isArray(v)) {
          if (v.every((x) => typeof x === 'string' || typeof x === 'number' || typeof x === 'boolean')) {
            lines.push(`${restPad}${k}: ${yamlInlineList(v as (string | number | boolean)[])}`);
          } else {
            lines.push(`${restPad}${k}:`);
            lines.push(dumpList(v, indent + 4));
          }
        } else if (typeof v === 'object' && v !== null) {
          lines.push(`${restPad}${k}:`);
          lines.push(dumpDict(v as { [k: string]: YamlValue }, indent + 4));
        } else {
          lines.push(`${restPad}${k}: ${yamlScalar(v as string | number | boolean)}`);
        }
      }
    } else {
      lines.push(`${pad}- ${yamlScalar(item as string | number | boolean)}`);
    }
  }
  return lines.join('\n');
}

// expand FinalDraft → YAML string（与后端 expand_to_yaml_structures 同形）
function expandDraftToYaml(draft: FinalDraft): string {
  // 组件：每条 {名称, 可选颜色: [...]}（可选颜色 = 该组件在所有变体出现过的色名 dedupe）
  const componentsBlock: YamlValue[] = draft.components.map((c) => {
    const colors = collectComponentColors(c.name, draft.variants);
    const entry: { [k: string]: YamlValue } = { 名称: c.name };
    if (colors.length > 0) {
      entry['可选颜色'] = colors;
    }
    return entry;
  });

  // 打印盘：每条 {盘号, 组件, 数量, 耗时分钟}
  const platesBlock: YamlValue[] = draft.plates.map((p) => ({
    盘号: p.plate_name,
    组件: p.component_name,
    数量: p.quantity_per_plate,
    耗时分钟: p.duration_minutes,
  }));

  // 产品：每个 variant 一条 {名称, BOM: [...]}
  const productsBlock: YamlValue[] = draft.variants.map((v) => {
    const bom: YamlValue[] = draft.components.map((c) => {
      const cell = v.color_cells.find((x) => x.component_name === c.name);
      const row: { [k: string]: YamlValue } = { 组件: c.name };
      if (cell && cell.color) row['颜色'] = cell.color;
      row['数量'] = c.assembly_quantity;
      return row;
    });
    return { 名称: v.variant_name, BOM: bom };
  });

  const root: { [k: string]: YamlValue } = {
    组件: componentsBlock,
    打印盘: platesBlock,
    产品: productsBlock,
  };
  // 三段之间空行：手工拼接
  const compStr = `组件:\n${dumpList(componentsBlock, 2)}`;
  const plateStr = `打印盘:\n${dumpList(platesBlock, 2)}`;
  const prodStr = `产品:\n${dumpList(productsBlock, 2)}`;
  void root; // satisfy ts (we composed manually for blank lines)
  return [compStr, plateStr, prodStr].join('\n\n');
}

function collectComponentColors(componentName: string, variants: PreviewVariant[]): string[] {
  const seen = new Set<string>();
  for (const v of variants) {
    for (const cell of v.color_cells) {
      if (cell.component_name === componentName && cell.color) {
        seen.add(cell.color);
      }
    }
  }
  return Array.from(seen);
}

// ---------- syntax highlighting (very small regex pass over YAML text) ----------

// 渲染顺序：先按行 split，再对每行分别上色：
// - 注释行（以 # 开头）整行绿斜体
// - 否则：分离 "key:" 蓝、字符串值橙、数字值绿
// 简化处理，针对 catalog.yaml 这种简单结构够用。

const CODE_COLORS = {
  bg: '#1e1e1e',
  fg: '#d4d4d4',
  comment: '#6a9955',
  key: '#9cdcfe',
  string: '#ce9178',
  number: '#b5cea8',
};

function renderHighlightedLine(line: string, idx: number) {
  // comment line
  const trimmed = line.trimStart();
  if (trimmed.startsWith('#')) {
    return (
      <div key={idx} style={{ color: CODE_COLORS.comment, fontStyle: 'italic' }}>
        {line || ' '}
      </div>
    );
  }
  // try to split "  key: value" or "  - key: value" or just "  - value"
  // Regex: leading spaces + optional "- " + (key:) + optional " value"
  const m = line.match(/^(\s*(?:-\s+)?)([^\s:][^:]*?):(\s*)(.*)$/);
  if (m) {
    const [, lead, key, gap, rest] = m;
    return (
      <div key={idx}>
        <span style={{ color: CODE_COLORS.fg }}>{lead}</span>
        <span style={{ color: CODE_COLORS.key }}>{key}</span>
        <span style={{ color: CODE_COLORS.fg }}>:{gap}</span>
        {renderValue(rest)}
      </div>
    );
  }
  // a "- value" line with no key
  const m2 = line.match(/^(\s*-\s+)(.*)$/);
  if (m2) {
    const [, lead, rest] = m2;
    return (
      <div key={idx}>
        <span style={{ color: CODE_COLORS.fg }}>{lead}</span>
        {renderValue(rest)}
      </div>
    );
  }
  return (
    <div key={idx} style={{ color: CODE_COLORS.fg }}>
      {line || ' '}
    </div>
  );
}

function renderValue(v: string) {
  if (v === '') return null;
  // inline list: [a, b, c]
  if (v.startsWith('[') && v.endsWith(']')) {
    const inner = v.slice(1, -1);
    const parts = inner.split(/(\s*,\s*)/);
    return (
      <>
        <span style={{ color: CODE_COLORS.fg }}>[</span>
        {parts.map((p, i) => {
          if (/^\s*,\s*$/.test(p)) {
            return (
              <span key={i} style={{ color: CODE_COLORS.fg }}>
                {p}
              </span>
            );
          }
          return (
            <span key={i} style={{ color: CODE_COLORS.string }}>
              {p}
            </span>
          );
        })}
        <span style={{ color: CODE_COLORS.fg }}>]</span>
      </>
    );
  }
  // number
  if (/^-?\d+(\.\d+)?$/.test(v)) {
    return <span style={{ color: CODE_COLORS.number }}>{v}</span>;
  }
  // string (including quoted)
  return <span style={{ color: CODE_COLORS.string }}>{v}</span>;
}

// ---------- component ----------

export default function PreviewMode(props: PreviewModeProps) {
  const { finalDraft, sessionId, onBack, onMerging, onSuccess, onError } = props;

  const [submitting, setSubmitting] = useState(false);

  const totalDurationMinutes = useMemo(
    () => finalDraft.plates.reduce((sum, p) => sum + (p.duration_minutes || 0), 0),
    [finalDraft.plates],
  );

  const componentsList = finalDraft.components.map((c) => c.name).join(' / ');
  const variantsList = finalDraft.variants.map((v) => v.variant_name).join(' / ');

  const yamlBody = useMemo(() => expandDraftToYaml(finalDraft), [finalDraft]);
  const yamlHeader = useMemo(() => {
    const now = new Date().toLocaleString('zh-CN');
    return `# --- ${finalDraft.product_base_name} 系列，由产品录入工具于 ${now} 追加 ---`;
  }, [finalDraft.product_base_name]);
  const fullYaml = useMemo(() => `${yamlHeader}\n\n${yamlBody}`, [yamlHeader, yamlBody]);
  const yamlLines = useMemo(() => fullYaml.split('\n'), [fullYaml]);

  async function handleConfirm() {
    if (submitting) return;
    setSubmitting(true);
    onMerging();
    try {
      const res = (await api.intake.merge({
        final_draft: finalDraft,
        session_id: sessionId,
      })) as {
        ok?: boolean;
        stats?: Record<string, number>;
        backup_path?: string;
        timing_ms?: Record<string, number>;
        error_kind?: string;
        error?: string;
        details?: any;
      };
      if (res?.ok === true) {
        const stats = (res.stats || {}) as Record<string, unknown>;
        const newSkus = Array.isArray(stats.new_skus) ? (stats.new_skus as string[]) : [];
        onSuccess(
          {
            // 后端 schema 使用中文键 新增组件 / 新增打印盘 / 新增产品变体（参见 schemas_intake.MergeStats）。
            components_added: Number(stats['新增组件'] ?? stats.components_added ?? 0),
            plates_added: Number(stats['新增打印盘'] ?? stats.plates_added ?? 0),
            products_added: Number(stats['新增产品变体'] ?? stats.products_added ?? 0),
            new_skus: newSkus,
          },
          res.backup_path || '',
          res.timing_ms || {},
        );
      } else {
        onError(res?.error_kind || 'unknown', res?.error || '合并失败', res?.details);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      onError('network', msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      {/* 合并摘要 */}
      <div style={styles.card}>
        <div style={styles.cardHeader}>
          <h3 style={styles.cardTitle}>合并摘要</h3>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            即将向 <code>data/catalog.yaml</code> 追加以下内容
          </Typography.Text>
        </div>

        <ul style={styles.summaryList}>
          <SummaryRow
            tag={<Tag color="blue">组</Tag>}
            label={
              <>
                <b>{finalDraft.components.length}</b> 个新组件
              </>
            }
            detail={componentsList}
          />
          <SummaryRow
            tag={<Tag color="orange">盘</Tag>}
            label={
              <>
                <b>{finalDraft.plates.length}</b> 张新打印盘
              </>
            }
            detail={`总耗时约 ${formatDuration(totalDurationMinutes)}`}
          />
          <SummaryRow
            tag={<Tag color="green">品</Tag>}
            label={
              <>
                <b>{finalDraft.variants.length}</b> 个新产品变体
              </>
            }
            detail={variantsList}
          />
        </ul>

        <div style={styles.summaryNote}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            合并前会自动备份到 <code>catalog.yaml.bak.&lt;时间戳&gt;</code>
            ，合并成功后会自动触发「重新加载目录」 — 写入失败时会自动回滚到备份。
          </Typography.Text>
        </div>
      </div>

      {/* YAML 预览 */}
      <div style={styles.card}>
        <div style={styles.cardHeader}>
          <h3 style={styles.cardTitle}>YAML 预览</h3>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            追加到 <code>data/catalog.yaml</code> 末尾的内容
          </Typography.Text>
        </div>

        <pre style={styles.codeBlock}>{yamlLines.map((l, i) => renderHighlightedLine(l, i))}</pre>
      </div>

      {/* 底部按钮 */}
      <div style={styles.footer}>
        <Button onClick={onBack} disabled={submitting} icon={<ArrowLeftOutlined />}>
          上一步：填写颜色
        </Button>
        <Tooltip title={submitting ? '正在合并…' : undefined}>
          <Button
            type="primary"
            icon={<CheckOutlined />}
            loading={submitting}
            disabled={submitting}
            onClick={handleConfirm}
          >
            确认合并并重新加载
          </Button>
        </Tooltip>
      </div>
    </div>
  );
}

function SummaryRow(p: { tag: React.ReactNode; label: React.ReactNode; detail: string }) {
  return (
    <li style={styles.summaryRow}>
      <span style={{ flexShrink: 0 }}>{p.tag}</span>
      <span style={{ flexShrink: 0, minWidth: 140 }}>{p.label}</span>
      <Tooltip title={p.detail.length > 60 ? p.detail : undefined} placement="topLeft">
        <span style={styles.summaryDetail}>{p.detail}</span>
      </Tooltip>
    </li>
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
  cardHeader: {
    display: 'flex',
    alignItems: 'baseline',
    gap: 12,
    marginBottom: 12,
    paddingBottom: 8,
    borderBottom: '1px solid #f0f0f0',
  },
  cardTitle: {
    margin: 0,
    fontSize: 16,
    fontWeight: 600,
  },
  summaryList: {
    listStyle: 'none',
    margin: 0,
    padding: 0,
  },
  summaryRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: '10px 0',
    borderBottom: '1px dashed #f0f0f0',
    fontSize: 14,
  },
  summaryDetail: {
    color: 'rgba(0,0,0,0.45)',
    fontSize: 13,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    flex: '1 1 auto',
    minWidth: 0,
  },
  summaryNote: {
    marginTop: 12,
    padding: '10px 14px',
    background: '#fafafa',
    borderRadius: 4,
    borderLeft: '3px solid #d9d9d9',
  },
  codeBlock: {
    background: CODE_COLORS.bg,
    color: CODE_COLORS.fg,
    borderRadius: 4,
    padding: 16,
    maxHeight: 520,
    overflowY: 'auto',
    fontFamily: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
    fontSize: 12,
    lineHeight: 1.5,
    margin: 0,
    whiteSpace: 'pre',
  },
  footer: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 4,
  },
};
