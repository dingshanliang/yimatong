const test = require("node:test");
const assert = require("node:assert/strict");
const {
  extractPublicId,
  extractShops,
  productSummary,
} = require("../utils/member-state");

test("extracts public id from direct and scene entry", () => {
  assert.equal(extractPublicId({ public_id: " CODE-1 " }), "CODE-1");
  assert.equal(
    extractPublicId({ scene: encodeURIComponent("public_id=CODE-2") }),
    "CODE-2"
  );
});

test("keeps only enabled safe shop links", () => {
  const shops = extractShops({
    page_config: {
      modules: [
        {
          type: "shop_redirect",
          enabled: true,
          config: {
            shops: [
              {
                name: "商城",
                url: "https://shop.example.com",
                commerce_connection_id: "connection-1",
              },
              { name: "坏链接", url: "javascript:alert(1)" },
            ],
          },
        },
      ],
    },
  });
  assert.deepEqual(shops, [
    {
      name: "商城",
      url: "https://shop.example.com",
      commerce_connection_id: "connection-1",
    },
  ]);
});

test("builds business-facing product summary without internal ids", () => {
  assert.deepEqual(
    productSummary({
      code_data: {
        brand: { name: "清源" },
        product: { name: "鲜米", description: "本季新米" },
      },
    }),
    {
      brandName: "清源",
      productName: "鲜米",
      productDescription: "本季新米",
    }
  );
});
