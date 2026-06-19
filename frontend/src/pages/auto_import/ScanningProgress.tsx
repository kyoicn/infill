import { Button, Spin } from 'antd';
import { CheckOutlined } from '@ant-design/icons';

interface ScanningProgressProps {
  steps: string[];
  currentStep: number; // 1-indexed
  elapsedMs?: number;
  onCancel?: () => void;
}

export default function ScanningProgress({
  steps,
  currentStep,
  elapsedMs,
  onCancel,
}: ScanningProgressProps) {
  const elapsedLabel = elapsedMs != null
    ? `已用 ${Math.round(elapsedMs / 1000)}s`
    : null;

  return (
    <div style={{ maxWidth: 560, margin: '0 auto' }}>
      <div
        style={{
          background: '#fafafa',
          borderRadius: 8,
          padding: '20px 24px',
          marginBottom: 16,
        }}
      >
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 16,
            fontWeight: 600,
            fontSize: 14,
          }}
        >
          <span>正在捕捉千帆订单</span>
          {elapsedLabel && (
            <span style={{ color: 'rgba(0,0,0,0.45)', fontWeight: 400, fontSize: 12 }}>
              {elapsedLabel}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {steps.map((label, idx) => {
            const stepNum = idx + 1;
            const isDone = stepNum < currentStep;
            const isActive = stepNum === currentStep;
            return (
              <div
                key={idx}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  fontSize: 13,
                  color: isDone
                    ? 'rgba(0,0,0,0.65)'
                    : isActive
                      ? 'rgba(0,0,0,0.88)'
                      : 'rgba(0,0,0,0.25)',
                  fontWeight: isActive ? 600 : 400,
                }}
              >
                <span
                  style={{
                    width: 20,
                    height: 20,
                    borderRadius: '50%',
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    background: isDone ? '#52c41a' : isActive ? '#ff2442' : '#f0f0f0',
                    color: isDone || isActive ? '#fff' : 'rgba(0,0,0,0.25)',
                    fontSize: 11,
                    flexShrink: 0,
                  }}
                >
                  {isDone ? (
                    <CheckOutlined style={{ fontSize: 10 }} />
                  ) : isActive ? (
                    <Spin size="small" style={{ color: '#fff' }} />
                  ) : (
                    '○'
                  )}
                </span>
                <span>{label}</span>
              </div>
            );
          })}
        </div>
      </div>
      {onCancel && (
        <div style={{ textAlign: 'center' }}>
          <Button onClick={onCancel}>取消本次扫描</Button>
        </div>
      )}
    </div>
  );
}
