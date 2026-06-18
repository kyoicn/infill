interface SkuPickerProps {
  value?: string | null;
  onChange?: (sku: string | null) => void;
}

export default function SkuPicker(_props: SkuPickerProps) {
  return <div style={{ color: '#999' }}>SKU 选择器（Task 4.3 实现中）</div>;
}
