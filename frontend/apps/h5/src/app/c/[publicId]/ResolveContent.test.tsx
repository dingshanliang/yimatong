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
});
