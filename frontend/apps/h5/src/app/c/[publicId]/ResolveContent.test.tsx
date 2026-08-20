import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { useScanEvent } = vi.hoisted(() => ({
  useScanEvent: vi.fn(),
}));

vi.mock("@/lib/useScanEvent", () => ({ useScanEvent }));

import { ResolveContent } from "./ResolveContent";

const recalledPayload = {
  code_data: {
    public_id: "PUBLIC-RECALLED",
    status: "activated",
    product: { name: "安心大米", origin: "产品默认产地" },
    batch: {
      batch_code: "PB-RECALLED",
      origin: "召回批次产地",
      status: "recalled",
      recall_reason: "该批次检测结果异常，请停止食用",
      recalled_at: "2026-08-10T12:30:00+08:00",
    },
  },
  scan_info: {
    recall_warning: {
      reason: "该批次检测结果异常，请停止食用",
      recalled_at: "2026-08-10T12:30:00+08:00",
    },
  },
  page_config: {
    modules: [
      { id: "trace", type: "light_traceability", enabled: true },
      {
        id: "benefit",
        type: "benefit_card",
        enabled: true,
        config: { title: "召回后不应出现的权益" },
      },
    ],
  },
};

describe("ResolveContent recalled production batch", () => {
  beforeEach(() => {
    useScanEvent.mockClear();
  });

  it("renders the recall facts before content and removes benefit entry points", () => {
    const markup = renderToStaticMarkup(
      <ResolveContent
        mode="json"
        publicId="PUBLIC-RECALLED"
        jsonPayload={recalledPayload}
        htmlContent={null}
      />
    );

    expect(markup).toContain("该生产批次已召回");
    expect(markup).toContain("该批次检测结果异常，请停止食用");
    expect(markup).toContain("2026-08-10 12:30");
    expect(markup).toContain("召回批次产地");
    expect(markup).not.toContain("召回后不应出现的权益");
    expect(markup).not.toContain("立即领取");
    expect(useScanEvent).toHaveBeenCalledWith(
      expect.objectContaining({ enabled: false })
    );
  });

  it("renders an expired warning and suppresses benefits and scan reporting", () => {
    const payload = {
      ...recalledPayload,
      code_data: {
        ...recalledPayload.code_data,
        batch: {
          batch_code: "PB-EXPIRED",
          origin: "过期批次产地",
          status: "expired",
        },
      },
      scan_info: {},
    };

    const markup = renderToStaticMarkup(
      <ResolveContent
        mode="json"
        publicId="PUBLIC-EXPIRED"
        jsonPayload={payload}
        htmlContent={null}
      />
    );

    expect(markup).toContain("该生产批次已过期");
    expect(markup).not.toContain("召回后不应出现的权益");
    expect(markup).not.toContain("立即领取");
    expect(useScanEvent).toHaveBeenCalledWith(
      expect.objectContaining({ enabled: false })
    );
  });

  it("keeps frozen traceability but removes every benefit entry point and scan request", () => {
    const payload = {
      ...recalledPayload,
      scan_token: "frozen-token-must-not-be-used",
      code_data: {
        ...recalledPayload.code_data,
        status: "frozen",
        lifecycle: "frozen",
        batch: {
          batch_code: "PB-FROZEN",
          origin: "冻结批次产地",
          status: "active",
        },
      },
      scan_info: {},
    };

    const markup = renderToStaticMarkup(
      <ResolveContent
        mode="json"
        publicId="PUBLIC-FROZEN"
        jsonPayload={payload}
        htmlContent={null}
      />
    );

    expect(markup).toContain("该码正在审核中");
    expect(markup).toContain("冻结批次产地");
    expect(markup).not.toContain("召回后不应出现的权益");
    expect(markup).not.toContain("立即领取");
    expect(useScanEvent).toHaveBeenCalledWith(
      expect.objectContaining({ enabled: false })
    );
  });

  it("keeps traceability but removes benefit entry points when no launch release is live", () => {
    const payload = {
      ...recalledPayload,
      scan_token: null,
      code_data: {
        ...recalledPayload.code_data,
        status: "activated",
        lifecycle: "active",
        batch: {
          batch_code: "PB-PAUSED",
          origin: "安全暂停批次",
          status: "active",
        },
      },
      scan_info: { benefit_paused: true, paused_reason: "launch_not_live" },
    };

    const markup = renderToStaticMarkup(
      <ResolveContent
        mode="json"
        publicId="PUBLIC-NO-LIVE-RELEASE"
        jsonPayload={payload}
        htmlContent={null}
      />
    );

    expect(markup).toContain("当前活动尚未开放");
    expect(markup).toContain("安全暂停批次");
    expect(markup).not.toContain("召回后不应出现的权益");
    expect(markup).not.toContain("立即领取");
    expect(useScanEvent).toHaveBeenCalledWith(
      expect.objectContaining({ enabled: false })
    );
  });

  it("does not present product origin as an authoritative batch fact", () => {
    const payload = {
      ...recalledPayload,
      code_data: {
        ...recalledPayload.code_data,
        batch: {
          ...recalledPayload.code_data.batch,
          origin: "",
        },
      },
    };

    const markup = renderToStaticMarkup(
      <ResolveContent
        mode="json"
        publicId="PUBLIC-MISSING-BATCH-ORIGIN"
        jsonPayload={payload}
        htmlContent={null}
      />
    );

    expect(markup).not.toContain("产品默认产地");
    expect(markup).toContain("未提供批次产地");
  });

  it("renders verification unavailable without presenting a repeat-scan result", () => {
    const markup = renderToStaticMarkup(
      <ResolveContent
        mode="json"
        publicId="PUBLIC-UNAVAILABLE"
        jsonPayload={{
          detail: "verification_unavailable",
          code_data: { result: "unavailable" },
        }}
        htmlContent={null}
      />
    );

    expect(markup).toContain("暂时无法加载");
    expect(markup).not.toContain("重复查验");
    expect(useScanEvent).toHaveBeenCalledWith(
      expect.objectContaining({ enabled: false })
    );
  });

  it("ignores forged risk and verification facts from page DSL", () => {
    const payload = {
      code_data: {
        public_id: "PUBLIC-AUTHORITY",
        status: "activated",
        lifecycle: "active",
        code_type: "inner",
        product: { name: "权威产品" },
        batch: { status: "active" },
      },
      scan_info: { is_first_scan: true, verification_count: 1 },
      page_config: {
        modules: [
          {
            id: "risk",
            type: "risk_alert",
            enabled: true,
            config: {
              alert_type: "suspected_copy",
              detail: "伪造风险事实",
              scan_count: 999,
              detected_city: "伪造地点",
            },
          },
          {
            id: "verify",
            type: "dual_code_verify",
            enabled: true,
            config: { product_verified: false },
          },
        ],
      },
    };

    const markup = renderToStaticMarkup(
      <ResolveContent
        mode="json"
        publicId="PUBLIC-AUTHORITY"
        jsonPayload={payload}
        htmlContent={null}
      />
    );

    expect(markup).not.toContain("伪造风险事实");
    expect(markup).not.toContain("伪造地点");
    expect(markup).not.toContain("999");
    expect(markup).toContain("首次验证 — 正品确认");
  });

  it("does not render unsafe media URLs from page DSL", () => {
    const payload = {
      code_data: {
        public_id: "PUBLIC-MEDIA",
        status: "activated",
        lifecycle: "active",
        product: { name: "权威产品" },
        batch: { status: "active" },
      },
      scan_info: { is_first_scan: true },
      page_config: {
        modules: [
          {
            id: "media",
            type: "media_section",
            enabled: true,
            config: {
              items: [
                {
                  type: "video",
                  url: "http://127.0.0.1/private",
                  poster_url: "data:text/html,bad",
                },
                { type: "image", url: "javascript:alert(1)" },
              ],
            },
          },
        ],
      },
    };

    const markup = renderToStaticMarkup(
      <ResolveContent
        mode="json"
        publicId="PUBLIC-MEDIA"
        jsonPayload={payload}
        htmlContent={null}
      />
    );

    expect(markup).not.toContain("127.0.0.1");
    expect(markup).not.toContain("data:text/html");
    expect(markup).not.toContain("javascript:");
  });

  it("does not revive legacy points or points-member modules in the first-phase journey", () => {
    const markup = renderToStaticMarkup(
      <ResolveContent
        mode="json"
        publicId="PUBLIC-NO-POINTS"
        jsonPayload={{
          code_data: {
            public_id: "PUBLIC-NO-POINTS",
            status: "activated",
            lifecycle: "active",
            product: { name: "品牌产品" },
            batch: { status: "active" },
          },
          scan_info: { is_first_scan: true },
          page_config: {
            modules: [
              { id: "member", type: "member_card", enabled: true },
              { id: "balance", type: "points_balance", enabled: true },
              { id: "shop", type: "points_shop", enabled: true },
              { id: "exchange", type: "points_exchange", enabled: true },
              { id: "history", type: "points_history", enabled: true },
            ],
          },
        }}
        htmlContent={null}
      />
    );

    expect(markup).not.toContain("我的积分");
    expect(markup).not.toContain("积分商城");
    expect(markup).not.toContain("积分明细");
    expect(markup).not.toContain("立即兑换");
  });
});
