import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { BACKOFF_MS } from './constants';
import type { PrinterStatusEvent, PrinterWSMessage } from '../../api/client';

export type WSStatus = 'connecting' | 'connected' | 'reconnecting' | 'disconnected';

// WS hook：裸 WebSocket + 指数退避；每次 onopen（含 mount 首次）触发 onReconnect
// 让上层去拉一次 snapshot 补齐。
export function usePrinterStatusWS(
  onEvent: (e: PrinterStatusEvent) => void,
  onReconnect: () => void,
): { status: WSStatus } {
  const [status, setStatus] = useState<WSStatus>('connecting');
  const wsRef = useRef<WebSocket | null>(null);
  const backoffIdx = useRef<number>(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // ref 包一层避免 effect 被 callback 引用变化拽得反复重连
  const onEventRef = useRef(onEvent);
  const onReconnectRef = useRef(onReconnect);
  useLayoutEffect(() => {
    onEventRef.current = onEvent;
    onReconnectRef.current = onReconnect;
  });

  useEffect(() => {
    let closed = false;

    const connect = () => {
      if (closed) return;
      const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
      const url = `${scheme}://${window.location.host}/api/ws/printers/status`;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        backoffIdx.current = 0;
        setStatus('connected');
        onReconnectRef.current();
      };

      ws.onmessage = (ev) => {
        let msg: PrinterWSMessage;
        try {
          msg = JSON.parse(ev.data) as PrinterWSMessage;
        } catch {
          return;
        }
        if (msg.type === 'state_change') {
          onEventRef.current(msg);
        } else if (msg.type === 'ping') {
          try {
            ws.send(JSON.stringify({ type: 'pong' }));
          } catch {
            // socket 已关，忽略；onclose 会接管重连
          }
        }
      };

      ws.onerror = () => {
        // 静默；onclose 会触发重连
      };

      ws.onclose = () => {
        if (closed) return;
        const idx = backoffIdx.current;
        const delay = BACKOFF_MS[idx] ?? 30000;
        setStatus(idx >= BACKOFF_MS.length ? 'disconnected' : 'reconnecting');
        backoffIdx.current = idx + 1;
        timerRef.current = setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      closed = true;
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      const ws = wsRef.current;
      if (ws && ws.readyState !== WebSocket.CLOSED) {
        ws.close();
      }
      wsRef.current = null;
    };
  }, []);

  return { status };
}
