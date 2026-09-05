function extractPublicId(options = {}) {
  if (typeof options.public_id === "string" && options.public_id.trim())
    return options.public_id.trim();
  if (typeof options.scene !== "string") return "";
  try {
    const scene = decodeURIComponent(options.scene);
    const pairs = scene.split("&").map((pair) => pair.split("="));
    const values = Object.fromEntries(
      pairs.map(([key, value = ""]) => [key, decodeURIComponent(value)])
    );
    return (values.public_id || values.p || scene).trim();
  } catch {
    return "";
  }
}

function extractShops(payload) {
  const modules =
    payload && payload.page_config && Array.isArray(payload.page_config.modules)
      ? payload.page_config.modules
      : [];
  const module = modules.find(
    (item) => item && item.type === "shop_redirect" && item.enabled !== false
  );
  return module && module.config && Array.isArray(module.config.shops)
    ? module.config.shops.filter(
        (shop) => shop && /^https?:\/\//i.test(shop.url || "")
      )
    : [];
}

function productSummary(payload) {
  const codeData = (payload && payload.code_data) || {};
  const product = codeData.product || {};
  const brand = codeData.brand || {};
  return {
    brandName:
      brand.name ||
      (payload && payload.tenant_branding && payload.tenant_branding.name) ||
      "品牌专区",
    productName: product.name || "已查验产品",
    productDescription:
      product.description || "包装码已完成查验，可继续查看会员权益。",
  };
}

module.exports = { extractPublicId, extractShops, productSummary };
