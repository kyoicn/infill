// NOTE: extractQianfanOrders is injected into the 千帆 page via chrome.scripting.executeScript({func}).
// Because executeScript serializes the function and runs it in the page's context (no closure),
// SELECTORS must be declared INSIDE the function. Keep this in sync with extension/content_xhs.js.
function extractQianfanOrders() {
  // ark.xiaohongshu.com 打单发货页 DOM 选择器（2026-06 校准）
  // 每条订单一个 <tr>，内含 .order-info-grid 容器
  // 商品和件数在平行的 .multi-sku-info / .multi-price-quantity 中，靠 index 对齐
  const SEL = {
    ORDER_ROW: 'tr',                          // 候选行，再用 .order-info-grid 过滤
    GRID: '.order-info-grid',                 // 订单元信息容器
    ORDER_ID: '.order-id .order-value',       // 编号：P797...
    ORDER_TIME: '.order-time-value',          // 下单：2026-06-19 15:58:08
    BUYER_NAME: '.buyer-info .buyer-name',    // 买家昵称
    SKU_ITEMS: '.multi-sku-info .sku-item',   // 商品列（多个）
    PRODUCT_NAME: '.product-name',            // 商品名（含 .presell-tag 子元素，需剔除）
    SPEC: '.base-info-spec',                  // 规格:白色
    PRICE_ITEMS: '.multi-price-quantity .price-item', // 价格件数列（与商品列平行 index）
    QTY: '.quantity-text',                    // x1
    // v0.4.1 收货信息（同一 <tr> 第 2 个 <td> 里）
    USER_CELL: '.user-info-cell',             // 整块收货信息卡
    USER_NAME: '.user-name-line span',        // 姓名（千帆已脱敏：张*）
    USER_PHONE: '.user-phone span',           // 手机（千帆已脱敏：137***9223）
  };

  // 诊断信息：用户报告 0 单时把命中数 + URL 回传，帮我判断选择器是否过时
  const debug = {
    page_url: location.href,
    page_title: document.title,
    selector_hits: {
      ORDER_ROW: document.querySelectorAll(SEL.ORDER_ROW).length,
      GRID: document.querySelectorAll(SEL.GRID).length,
      ORDER_ID: document.querySelectorAll(SEL.ORDER_ID).length,
      BUYER_NAME: document.querySelectorAll(SEL.BUYER_NAME).length,
      SKU_ITEMS: document.querySelectorAll(SEL.SKU_ITEMS).length,
      PRICE_ITEMS: document.querySelectorAll(SEL.PRICE_ITEMS).length,
    },
    body_text_sample: (document.body && document.body.innerText
      ? document.body.innerText.slice(0, 500)
      : ''),
  };

  // 只取文本节点（用于 product-name 这种包含子标签 .presell-tag 的场景）
  function directText(el) {
    if (!el) return '';
    return Array.from(el.childNodes)
      .filter((n) => n.nodeType === 3) // TEXT_NODE
      .map((n) => (n.textContent || '').trim())
      .filter(Boolean)
      .join(' ')
      .trim();
  }

  const rows = document.querySelectorAll(SEL.ORDER_ROW);
  const orders = [];

  rows.forEach((row) => {
    const grid = row.querySelector(SEL.GRID);
    if (!grid) return; // 非订单行（表头 / 分隔 / 其它）

    const idEl = grid.querySelector(SEL.ORDER_ID);
    const external_order_id = idEl ? (idEl.textContent || '').trim() : '';

    const timeEl = grid.querySelector(SEL.ORDER_TIME);
    const external_created_at = timeEl ? (timeEl.textContent || '').trim() : '';

    const buyerEl = grid.querySelector(SEL.BUYER_NAME);
    const buyer_nickname = buyerEl ? (buyerEl.textContent || '').trim() : '';

    // v0.4.1 收货信息（千帆已脱敏，仍能用于打单）
    const userCell = row.querySelector(SEL.USER_CELL);
    let recipient_name = null;
    let recipient_phone = null;
    let recipient_address = null;
    if (userCell) {
      const nameEl = userCell.querySelector(SEL.USER_NAME);
      if (nameEl) recipient_name = (nameEl.textContent || '').trim() || null;
      const phoneEl = userCell.querySelector(SEL.USER_PHONE);
      if (phoneEl) recipient_phone = (phoneEl.textContent || '').trim() || null;
      // address: 第一个不在 .user-name-line / .user-phone 里的 <span>
      const directSpans = Array.from(userCell.children).filter(
        (n) => n.tagName === 'SPAN'
      );
      for (const s of directSpans) {
        const txt = (s.textContent || '').trim();
        if (txt && !/^\d{3}\*{3}\d{4}$/.test(txt) && !s.closest('.user-name-line, .user-phone')) {
          recipient_address = txt;
          break;
        }
      }
    }

    const skuEls = row.querySelectorAll(SEL.SKU_ITEMS);
    const priceEls = row.querySelectorAll(SEL.PRICE_ITEMS);
    const products = [];

    skuEls.forEach((sku, i) => {
      const nameEl = sku.querySelector(SEL.PRODUCT_NAME);
      let title = directText(nameEl);
      if (!title && nameEl) {
        // 兜底：整段 textContent 再剔除 "预售"
        title = (nameEl.textContent || '').replace(/预售/g, '').trim();
      }

      const specEl = sku.querySelector(SEL.SPEC);
      const specText = specEl ? (specEl.textContent || '').trim() : '';
      // 规格:白色 → 把"规格:"去掉只留颜色
      const spec = specText.replace(/^规格[:：]\s*/, '').trim();
      const listing_title = spec ? `${title} ${spec}`.trim() : title;

      let quantity = 1;
      const priceItem = priceEls[i];
      if (priceItem) {
        const qtyEl = priceItem.querySelector(SEL.QTY);
        if (qtyEl) {
          const m = (qtyEl.textContent || '').match(/(\d+)/);
          if (m) quantity = parseInt(m[1], 10) || 1;
        }
      }

      if (listing_title) {
        products.push({ listing_title, quantity });
      }
    });

    if (external_order_id && products.length > 0) {
      orders.push({
        external_order_id,
        buyer_nickname,
        external_created_at,
        recipient_name,
        recipient_phone,
        recipient_address,
        products,
      });
    }
  });

  if (orders.length === 0) {
    console.error('[infill-ext] selectors matched 0 orders', debug);
  } else {
    console.log(`[infill-ext] extracted ${orders.length} orders`, debug);
  }

  return { orders, debug };
}

async function handleScrapeXhs(batchId) {
  const tabs = await chrome.tabs.query({
    url: [
      '*://*.qianfan.xiaohongshu.com/*',
      '*://ark.xiaohongshu.com/*',
    ],
  });
  if (!tabs || tabs.length === 0) {
    return { ok: false, error_kind: 'extension_no_xhs_tab' };
  }
  const tab = tabs[0];

  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: extractQianfanOrders,
  });
  const payload = (results && results[0] && results[0].result) || { orders: [], debug: {} };
  const raw_orders = payload.orders || [];
  const ext_debug = payload.debug || {};

  const resp = await fetch('http://localhost:8000/api/auto-import/xhs/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ batch_id: batchId, raw_orders, ext_debug }),
  });
  const scan_response = await resp.json();
  return { ok: true, scan_response, ext_debug };
}

chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  if (!message || typeof message !== 'object') {
    sendResponse({ ok: false, error_kind: 'extension_runtime_error', message: 'invalid message' });
    return false;
  }

  if (message.action === 'ping') {
    sendResponse({ ok: true, version: chrome.runtime.getManifest().version });
    return false;
  }

  if (message.action === 'scrape_xhs') {
    handleScrapeXhs(message.batch_id)
      .then((result) => sendResponse(result))
      .catch((e) => {
        sendResponse({
          ok: false,
          error_kind: 'extension_runtime_error',
          message: e && e.message ? e.message : String(e),
        });
      });
    return true;
  }

  sendResponse({ ok: false, error_kind: 'extension_runtime_error', message: 'unknown action' });
  return false;
});
