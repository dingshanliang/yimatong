import { describe, it, expect } from "vitest";
import {
  validateDSL,
  createEmptyDSL,
  createDefaultModules,
  inspectPageReadiness,
  MODULE_TYPES,
  MODULE_TYPE_LABELS,
} from "../page-dsl";
import type { PageDSL } from "../page-dsl";

describe("page-dsl utilities", () => {
  describe("validateDSL", () => {
    it("returns empty errors for valid empty DSL", () => {
      const dsl = createEmptyDSL();
      expect(validateDSL(dsl)).toEqual([]);
    });

    it("returns empty errors for DSL with valid modules", () => {
      const dsl: PageDSL = {
        modules: [
          { id: "hero", type: "product_hero", enabled: true, config: {} },
          { id: "verify", type: "verification_status", enabled: false },
        ],
      };
      expect(validateDSL(dsl)).toEqual([]);
    });

    it("returns error when modules is not an array", () => {
      const dsl = { modules: "not-array" };
      expect(validateDSL(dsl)).toContain("modules 必须是数组");
    });

    it("returns error when a module is missing id", () => {
      const dsl = {
        modules: [{ type: "product_hero", enabled: true }],
      };
      expect(validateDSL(dsl)).toContain("modules[0].id 不能为空");
    });

    it("returns error when a module is missing type", () => {
      const dsl = {
        modules: [{ id: "hero", enabled: true }],
      };
      expect(validateDSL(dsl)).toContain("modules[0].type 不能为空");
    });

    it("returns error for invalid module type", () => {
      const dsl = {
        modules: [{ id: "hero", type: "invalid_type", enabled: true }],
      };
      expect(validateDSL(dsl)).toContain(
        'modules[0].type "invalid_type" 不是有效的模块类型'
      );
    });

    it("returns error when DSL is not an object", () => {
      expect(validateDSL(null)).toContain("DSL 必须是一个对象");
      expect(validateDSL("string")).toContain("DSL 必须是一个对象");
      expect(validateDSL(123)).toContain("DSL 必须是一个对象");
    });

    it("validates routing campaign_periods date format", () => {
      const dsl = {
        routing: {
          campaign_periods: [
            { mode: "campaign", start_at: "invalid-date" },
          ],
        },
      };
      expect(validateDSL(dsl)).toContain(
        "routing.campaign_periods[0].start_at 日期格式无效"
      );
    });

    it("returns empty errors for valid routing", () => {
      const dsl = {
        routing: {
          default_page: true,
          campaign_periods: [
            { mode: "campaign", start_at: "2026-06-01T00:00:00+08:00", end_at: "2026-06-30T23:59:59+08:00" },
            { mode: "evergreen" },
          ],
        },
      };
      expect(validateDSL(dsl)).toEqual([]);
    });

    it("returns error when routing is not an object", () => {
      const dsl = { routing: "string" };
      expect(validateDSL(dsl)).toContain("routing 必须是对象");
    });

    it("returns error when campaign_periods is not an array", () => {
      const dsl = { routing: { campaign_periods: "string" } };
      expect(validateDSL(dsl)).toContain("routing.campaign_periods 必须是数组");
    });
  });

  describe("createEmptyDSL", () => {
    it("returns DSL with empty modules and default routing", () => {
      const dsl = createEmptyDSL();
      expect(dsl.modules).toEqual([]);
      expect(dsl.routing).toEqual({
        default_page: true,
        campaign_periods: [{ mode: "evergreen" }],
      });
    });
  });

  describe("createDefaultModules", () => {
    it("returns array of default modules", () => {
      const modules = createDefaultModules();
      expect(modules).toHaveLength(4);
      expect(modules[0]).toMatchObject({
        id: "hero",
        type: "product_hero",
        enabled: true,
      });
      expect(modules[1]).toMatchObject({
        id: "verify",
        type: "verification_status",
        enabled: true,
      });
      expect(modules[2]).toMatchObject({
        id: "trace",
        type: "light_traceability",
        enabled: true,
      });
      expect(modules[3]).toMatchObject({
        id: "legal",
        type: "legal_terms",
        enabled: true,
      });
    });
  });

  describe("MODULE_TYPES", () => {
    it("contains all module types with labels", () => {
      expect(MODULE_TYPES.length).toBe(Object.keys(MODULE_TYPE_LABELS).length);
      expect(MODULE_TYPES[0]).toHaveProperty("value");
      expect(MODULE_TYPES[0]).toHaveProperty("label");
    });
  });

  describe("inspectPageReadiness", () => {
    it("blocks publish when page has no product", () => {
      const dsl: PageDSL = {
        modules: [{ id: "hero", type: "product_hero", enabled: true, config: {} }],
      };

      const readiness = inspectPageReadiness(dsl, {});

      expect(readiness.blockingIssues).toContain("页面未关联产品，消费者扫码不会自动命中该页面");
      expect(readiness.usesExampleData).toBe(true);
      expect(readiness.moduleStatuses[0]).toMatchObject({
        moduleId: "hero",
        status: "example",
      });
    });

    it("blocks publish when no module is enabled", () => {
      const dsl: PageDSL = {
        modules: [{ id: "hero", type: "product_hero", enabled: false, config: {} }],
      };

      const readiness = inspectPageReadiness(dsl, {
        product: { id: "p1", name: "五常大米" },
      });

      expect(readiness.blockingIssues).toContain("页面没有启用模块");
    });

    it("warns when report and certificate modules have no selected assets", () => {
      const dsl: PageDSL = {
        modules: [
          { id: "report", type: "test_reports", enabled: true, config: {} },
          { id: "cert", type: "certificates", enabled: true, config: {} },
        ],
      };

      const readiness = inspectPageReadiness(dsl, {
        product: { id: "p1", name: "五常大米" },
      });

      expect(readiness.blockingIssues).toEqual([]);
      expect(readiness.warnings).toEqual(
        expect.arrayContaining([
          "检测报告：检测报告模块未关联产品资料",
          "资质证书：资质证书模块未关联产品资料",
        ]),
      );
    });

    it("warns when traceability has no real batch data", () => {
      const dsl: PageDSL = {
        modules: [
          {
            id: "trace",
            type: "light_traceability",
            enabled: true,
            config: { fields: ["origin", "batch_no"] },
          },
        ],
      };

      const readiness = inspectPageReadiness(dsl, {
        product: { id: "p1", name: "五常大米" },
        batches: [],
      });

      expect(readiness.usesExampleData).toBe(true);
      expect(readiness.warnings).toContain("溯源信息：溯源信息暂无真实生产批次数据");
    });

    it("passes configured product hero and traceability with real product and batch", () => {
      const dsl: PageDSL = {
        modules: [
          { id: "hero", type: "product_hero", enabled: true, config: {} },
          {
            id: "trace",
            type: "light_traceability",
            enabled: true,
            config: { fields: ["origin", "batch_no"] },
          },
        ],
      };

      const readiness = inspectPageReadiness(dsl, {
        product: { id: "p1", name: "五常大米" },
        batches: [{ id: "b1", batch_code: "PB-001", origin: "五常" }],
      });

      expect(readiness.blockingIssues).toEqual([]);
      expect(readiness.warnings).toEqual([]);
      expect(readiness.usesExampleData).toBe(false);
    });
  });
});
