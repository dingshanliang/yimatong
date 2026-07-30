"""快速创建 H5 扫码演示数据并输出可扫的 URL"""

import json

import requests

BASE = "http://localhost:8001"


def main():
    # 1. 创建租户
    r = requests.post(
        f"{BASE}/api/v1/tenants",
        json={
            "name": "好味道食品",
            "admin_email": "demo@haoweidao.com",
            "admin_name": "DemoAdmin",
            "admin_password": "Demo1234",
        },
    )
    assert r.status_code == 201, f"创建租户失败: {r.text}"
    tenant_id = r.json()["id"]
    print(f"✅ 租户创建: {tenant_id}")

    # 2. 登录获取 token
    # 先创建账号的 token 方式——直接用 create_access_token
    from app.utils.security import create_access_token

    token = create_access_token(tenant_id, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    # 3. 创建品牌
    r = requests.post(f"{BASE}/api/v1/brands", json={"name": "好味道"}, headers=headers)
    assert r.status_code == 201, f"创建品牌失败: {r.text}"
    brand_id = r.json()["id"]
    print(f"✅ 品牌创建: {brand_id}")

    # 4. 创建产品
    r = requests.post(
        f"{BASE}/api/v1/products",
        json={
            "brand_id": brand_id,
            "name": "五常有机大米 5kg",
            "description": "来自黑龙江五常的有机大米，粒粒饱满，软糯香甜",
        },
        headers=headers,
    )
    assert r.status_code == 201, f"创建产品失败: {r.text}"
    product_id = r.json()["id"]
    print(f"✅ 产品创建: {product_id}")

    # 5. 创建 SKU
    r = requests.post(
        f"{BASE}/api/v1/skus",
        json={
            "product_id": product_id,
            "code": "RICE-5KG-01",
            "name": "5kg 真空装",
        },
        headers=headers,
    )
    assert r.status_code == 201, f"创建SKU失败: {r.text}"
    sku_id = r.json()["id"]

    # 6. 创建生产批次
    r = requests.post(
        f"{BASE}/api/v1/production-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "batch_code": "PB-2026-001",
            "production_date": "2026-05-01",
            "expiry_date": "2027-05-01",
            "origin": "黑龙江省五常市",
        },
        headers=headers,
    )
    assert r.status_code == 201, f"创建生产批次失败: {r.text}"
    production_batch_id = r.json()["id"]

    # 7. 创建页面模板并发布
    r = requests.post(
        f"{BASE}/api/v1/page-templates",
        json={
            "name": "五常大米扫码页",
            "template_type": "product_info",
            "product_id": product_id,
        },
        headers=headers,
    )
    assert r.status_code == 201, f"创建模板失败: {r.text}"
    template_id = r.json()["id"]

    config = {
        "modules": [
            {"id": "hero", "type": "product_hero", "enabled": True, "config": {"show_verify_badge": True}},
            {"id": "verify", "type": "verification_status", "enabled": True},
            {
                "id": "trace",
                "type": "light_traceability",
                "enabled": True,
                "config": {"fields": ["origin", "production_date", "batch_code"]},
            },
            {
                "id": "cta",
                "type": "cta_group",
                "enabled": True,
                "config": {
                    "buttons": [
                        {"label": "加入企业微信", "action": "wecom_link", "url": "https://example.com/wecom"},
                        {"label": "去京东购买", "action": "external_shop", "url": "https://jd.com"},
                    ]
                },
            },
            {
                "id": "lead",
                "type": "lead_form",
                "enabled": True,
                "config": {"title": "留下联系方式", "fields": ["phone", "region"]},
            },
        ],
    }

    r = requests.post(
        f"{BASE}/api/v1/page-templates/{template_id}/versions",
        json={
            "config_json": config,
        },
        headers=headers,
    )
    assert r.status_code == 201, f"创建版本失败: {r.text}"
    version_id = r.json()["id"]

    r = requests.post(f"{BASE}/api/v1/page-versions/{version_id}/publish", headers=headers)
    assert r.status_code == 200, f"发布失败: {r.text}"
    print("✅ 页面模板已发布")

    # 8. 生成码批次
    r = requests.post(
        f"{BASE}/api/v1/code-batches",
        json={
            "product_id": product_id,
            "sku_id": sku_id,
            "production_batch_id": production_batch_id,
            "batch_code": "CB-2026-001",
            "quantity": 5,
        },
        headers=headers,
    )
    assert r.status_code == 201, f"创建码批次失败: {r.text}"
    batch_id = r.json()["id"]

    # 9. 激活码批次
    r = requests.post(f"{BASE}/api/v1/code-batches/{batch_id}/activate", headers=headers)
    assert r.status_code == 200, f"激活失败: {r.text}"
    print("✅ 码批次已激活")

    # 10. 获取码列表
    r = requests.get(f"{BASE}/api/v1/code-items?code_batch_id={batch_id}", headers=headers)
    assert r.status_code == 200
    items = r.json()["items"]

    print("\n" + "=" * 60)
    print("🎯 可扫码 URL（复制到手机浏览器或生成二维码）:")
    print("=" * 60)
    for i, item in enumerate(items[:3], 1):
        public_id = item["public_id"]
        url = f"http://localhost:8001/c/{public_id}"
        print(f"\n  码 {i}: {url}")
        print(f"  public_id: {public_id}")

    # 保存到文件方便后续使用
    with open("/tmp/h5_demo_codes.json", "w") as f:
        json.dump({"tenant_id": tenant_id, "batch_id": batch_id, "codes": items, "token": token}, f, indent=2)

    print("\n📁 数据已保存到 /tmp/h5_demo_codes.json")
    print("\n💡 下一步:")
    print("   1. 用二维码生成器把上面的 URL 生成二维码")
    print("   2. 或直接在手机浏览器打开上面的 URL")
    print("   3. 或启动 H5 前端: cd frontend && pnpm dev:h5")


if __name__ == "__main__":
    main()
