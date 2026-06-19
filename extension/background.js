// NOTE: extractQianfanOrders is injected into the 千帆 page via chrome.scripting.executeScript({func}).
// Because executeScript serializes the function and runs it in the page's context (no closure),
// SELECTORS must be declared INSIDE the function. Keep this in sync with extension/content_xhs.js.
function extractQianfanOrders() {
  const SELECTORS = {
    ORDER_CARD: '[data-order-id], .order-card',
    EXTERNAL_ORDER_ID: '[data-order-id]',
    BUYER_NICKNAME: '.buyer-name, .nickname',
    EXTERNAL_CREATED_AT: '.order-time, [data-time]',
    PRODUCT_ITEM: '.product-item, .order-item',
    PRODUCT_TITLE: '.product-title, .item-title',
    PRODUCT_QUANTITY: '.product-qty, .quantity',
  };

  const cards = document.querySelectorAll(SELECTORS.ORDER_CARD);
  if (!cards || cards.length === 0) {
    console.error('[infill-ext] selectors not found on this page');
    return [];
  }

  const orders = [];
  cards.forEach((card) => {
    const idEl = card.matches(SELECTORS.EXTERNAL_ORDER_ID)
      ? card
      : card.querySelector(SELECTORS.EXTERNAL_ORDER_ID);
    const external_order_id = idEl
      ? (idEl.getAttribute('data-order-id') || idEl.textContent || '').trim()
      : '';

    const buyerEl = card.querySelector(SELECTORS.BUYER_NICKNAME);
    const buyer_nickname = buyerEl ? (buyerEl.textContent || '').trim() : '';

    const timeEl = card.querySelector(SELECTORS.EXTERNAL_CREATED_AT);
    const external_created_at = timeEl
      ? (timeEl.getAttribute('data-time') || timeEl.textContent || '').trim()
      : '';

    const productEls = card.querySelectorAll(SELECTORS.PRODUCT_ITEM);
    const products = [];
    productEls.forEach((pEl) => {
      const titleEl = pEl.querySelector(SELECTORS.PRODUCT_TITLE);
      const qtyEl = pEl.querySelector(SELECTORS.PRODUCT_QUANTITY);
      const listing_title = titleEl ? (titleEl.textContent || '').trim() : '';
      const qtyRaw = qtyEl ? (qtyEl.textContent || '').trim() : '';
      const qtyNum = parseInt(qtyRaw.replace(/[^0-9]/g, ''), 10);
      const quantity = Number.isFinite(qtyNum) && qtyNum > 0 ? qtyNum : 1;
      if (listing_title) {
        products.push({ listing_title, quantity });
      }
    });

    if (!external_order_id || products.length === 0) {
      return;
    }

    orders.push({
      external_order_id,
      buyer_nickname,
      external_created_at,
      products,
    });
  });

  return orders;
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
  const raw_orders = (results && results[0] && results[0].result) || [];

  const resp = await fetch('http://localhost:8000/api/auto-import/xhs/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ batch_id: batchId, raw_orders }),
  });
  const scan_response = await resp.json();
  return { ok: true, scan_response };
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
