const SELECTORS = {
  ORDER_CARD: '[data-order-id], .order-card',
  EXTERNAL_ORDER_ID: '[data-order-id]',
  BUYER_NICKNAME: '.buyer-name, .nickname',
  EXTERNAL_CREATED_AT: '.order-time, [data-time]',
  PRODUCT_ITEM: '.product-item, .order-item',
  PRODUCT_TITLE: '.product-title, .item-title',
  PRODUCT_QUANTITY: '.product-qty, .quantity',
};
// TODO: 实际选择器待千帆 DOM 真实样本验证 / 后续 QA 阶段校准

function extractQianfanOrders() {
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

window.extractQianfanOrders = extractQianfanOrders;
