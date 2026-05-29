/**
 * 页面 DSL Schema 验证与工具函数
 *
 * DSL 结构参考 docs/05_samples/page_config_example.yaml
 * config_json 顶层结构：{ modules: Module[], routing?: Routing }
 */

export interface ModuleConfig {
  id: string;
  type: ModuleType;
  enabled: boolean;
  config?: Record<string, unknown>;
}

export type ModuleType =
  | "product_hero"
  | "verification_status"
  | "light_traceability"
  | "test_reports"
  | "benefit_card"
  | "cta_group"
  | "legal_terms"
  | "custom_html"
  | "member_card"
  | "points_balance"
  | "points_exchange"
  | "outer_code_guide"
  | "risk_alert"
  | "dual_code_verify";

export interface CampaignPeriod {
  campaign_id?: string;
  start_at?: string;
  end_at?: string;
  mode: "campaign" | "evergreen";
}

export interface RoutingConfig {
  default_page?: boolean;
  campaign_periods?: CampaignPeriod[];
}

export interface PageDSL {
  modules?: ModuleConfig[];
  routing?: RoutingConfig;
  [key: string]: unknown;
}

export const MODULE_TYPE_LABELS: Record<ModuleType, string> = {
  product_hero: "产品展示",
  verification_status: "验真状态",
  light_traceability: "溯源信息",
  test_reports: "检测报告",
  benefit_card: "权益卡片",
  cta_group: "私域/跳转按钮",
  legal_terms: "法律条款",
  custom_html: "自定义 HTML",
  member_card: "会员卡片",
  points_balance: "积分余额",
  points_exchange: "积分兑换",
  outer_code_guide: "外码引导",
  risk_alert: "风险预警",
  dual_code_verify: "双码验真",
};

export const MODULE_TYPES = Object.entries(MODULE_TYPE_LABELS).map(
  ([value, label]) => ({ value, label }),
);

/** 验证 DSL 结构，返回错误列表 */
export function validateDSL(dsl: unknown): string[] {
  const errors: string[] = [];

  if (!dsl || typeof dsl !== "object") {
    errors.push("DSL 必须是一个对象");
    return errors;
  }

  const obj = dsl as Record<string, unknown>;

  if (obj.modules !== undefined) {
    if (!Array.isArray(obj.modules)) {
      errors.push("modules 必须是数组");
    } else {
      obj.modules.forEach((mod: unknown, i: number) => {
        if (!mod || typeof mod !== "object") {
          errors.push(`modules[${i}] 必须是对象`);
          return;
        }
        const m = mod as Record<string, unknown>;
        if (!m.id) errors.push(`modules[${i}].id 不能为空`);
        if (!m.type) errors.push(`modules[${i}].type 不能为空`);
        if (m.type && !MODULE_TYPE_LABELS[m.type as ModuleType]) {
          errors.push(`modules[${i}].type "${String(m.type)}" 不是有效的模块类型`);
        }
      });
    }
  }

  if (obj.routing !== undefined) {
    if (typeof obj.routing !== "object" || obj.routing === null) {
      errors.push("routing 必须是对象");
    } else {
      const routing = obj.routing as Record<string, unknown>;
      if (routing.campaign_periods !== undefined) {
        if (!Array.isArray(routing.campaign_periods)) {
          errors.push("routing.campaign_periods 必须是数组");
        } else {
          routing.campaign_periods.forEach((p: unknown, i: number) => {
            if (!p || typeof p !== "object") return;
            const period = p as Record<string, unknown>;
            if (period.start_at && isNaN(Date.parse(String(period.start_at)))) {
              errors.push(`routing.campaign_periods[${i}].start_at 日期格式无效`);
            }
            if (period.end_at && isNaN(Date.parse(String(period.end_at)))) {
              errors.push(`routing.campaign_periods[${i}].end_at 日期格式无效`);
            }
          });
        }
      }
    }
  }

  return errors;
}

/** 生成空白 DSL */
export function createEmptyDSL(): PageDSL {
  return {
    modules: [],
    routing: { default_page: true, campaign_periods: [{ mode: "evergreen" }] },
  };
}

/** 生成默认模块（从模板库复制时使用） */
export function createDefaultModules(): ModuleConfig[] {
  return [
    { id: "hero", type: "product_hero", enabled: true, config: { show_verify_badge: true } },
    { id: "verify", type: "verification_status", enabled: true },
    { id: "trace", type: "light_traceability", enabled: true, config: { fields: ["origin", "production_date", "batch_no"] } },
    { id: "legal", type: "legal_terms", enabled: true, config: { show_privacy_policy: true } },
  ];
}
