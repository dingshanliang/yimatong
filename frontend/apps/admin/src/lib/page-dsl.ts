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
  | "shop_redirect"
  | "lead_form"
  | "certificates"
  | "media_section"
  | "legal_terms"
  | "custom_html"
  | "member_card"
  | "points_balance"
  | "points_exchange"
  | "outer_code_guide"
  | "risk_alert"
  | "dual_code_verify"
  | "points_history";

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

export interface PreviewProduct {
  id: string;
  name: string;
  brand_name?: string;
  category?: string;
  origin?: string;
  image_url?: string;
  description?: string;
  story_title?: string;
  story_content?: string;
}

export interface PreviewBatch {
  id: string;
  batch_code: string;
  production_date?: string;
  expiry_date?: string;
  origin?: string;
  status?: string;
}

export interface PreviewAsset {
  id: string;
  asset_type: "image" | "video" | "test_report" | "certificate" | "story" | "other" | string;
  name: string;
  description?: string;
  issuer?: string;
  valid_until?: string;
  file_url?: string;
  image_url?: string;
  content_text?: string;
  status?: string;
}

export interface PagePreviewContext {
  product?: PreviewProduct | null;
  batches?: PreviewBatch[];
  assets?: PreviewAsset[];
}

export type ModuleReadinessStatus = "configured" | "incomplete" | "example";

export interface ModuleReadiness {
  moduleId: string;
  moduleType: ModuleType;
  label: string;
  status: ModuleReadinessStatus;
  message: string;
  issues: string[];
}

export interface PageReadiness {
  blockingIssues: string[];
  warnings: string[];
  moduleStatuses: ModuleReadiness[];
  usesExampleData: boolean;
}

export const MODULE_TYPE_LABELS: Record<ModuleType, string> = {
  product_hero: "产品展示",
  verification_status: "验真状态",
  light_traceability: "溯源信息",
  test_reports: "检测报告",
  certificates: "资质证书",
  benefit_card: "权益卡片",
  cta_group: "私域/跳转按钮",
  shop_redirect: "购买渠道",
  lead_form: "留资表单",
  media_section: "视频/图文",
  legal_terms: "法律条款",
  custom_html: "自定义 HTML",
  member_card: "会员卡片",
  points_balance: "积分余额",
  points_exchange: "积分兑换",
  outer_code_guide: "外码引导",
  risk_alert: "风险预警",
  dual_code_verify: "双码验真",
  points_history: "积分明细",
};

export const MODULE_TYPES = Object.entries(MODULE_TYPE_LABELS).map(
  ([value, label]) => ({ value, label }),
);

const MODULE_STATUS_PRIORITY: Record<ModuleReadinessStatus, number> = {
  incomplete: 0,
  example: 1,
  configured: 2,
};

function normalizeStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String).filter(Boolean) : [];
}

function hasAnyAsset(context: PagePreviewContext | undefined, assetTypes: string[]) {
  return Boolean(context?.assets?.some((asset) => assetTypes.includes(asset.asset_type)));
}

function buildModuleReadiness(
  module: ModuleConfig,
  context?: PagePreviewContext,
): ModuleReadiness {
  const label = MODULE_TYPE_LABELS[module.type] || module.type;
  const config = module.config || {};
  const product = context?.product || null;
  const batches = context?.batches || [];
  const issues: string[] = [];
  let status: ModuleReadinessStatus = "configured";
  let message = "已配置";

  const setStatus = (nextStatus: ModuleReadinessStatus, nextMessage: string, issue?: string) => {
    if (MODULE_STATUS_PRIORITY[nextStatus] < MODULE_STATUS_PRIORITY[status]) {
      status = nextStatus;
      message = nextMessage;
    }
    if (issue) issues.push(issue);
  };

  switch (module.type) {
    case "product_hero":
      if (!product && !config.title_template) {
        setStatus("example", "使用示例产品", "产品展示将使用示例产品信息");
      }
      break;
    case "verification_status":
      if (!product) {
        setStatus("example", "使用示例验真", "验真状态将使用示例扫码结果");
      }
      break;
    case "light_traceability": {
      const fields = normalizeStringArray(config.fields);
      if (fields.length === 0) {
        setStatus("incomplete", "未选择字段", "溯源信息需要选择至少一个展示字段");
      }
      if (batches.length === 0) {
        setStatus("example", "使用示例批次", "溯源信息暂无真实生产批次数据");
      }
      break;
    }
    case "test_reports":
      if (normalizeStringArray(config.report_ids).length === 0) {
        setStatus("incomplete", "未关联报告", "检测报告模块未关联产品资料");
      } else if (!hasAnyAsset(context, ["test_report"])) {
        setStatus("example", "报告不可预览", "当前产品资料库中没有可预览的检测报告");
      }
      break;
    case "certificates":
      if (normalizeStringArray(config.certificate_ids).length === 0) {
        setStatus("incomplete", "未关联证书", "资质证书模块未关联产品资料");
      } else if (!hasAnyAsset(context, ["certificate"])) {
        setStatus("example", "证书不可预览", "当前产品资料库中没有可预览的资质证书");
      }
      break;
    case "media_section":
      if (normalizeStringArray(config.asset_ids).length === 0) {
        setStatus("example", "使用示例素材", "视频/图文模块未关联素材");
      }
      break;
    case "cta_group":
      if (!Array.isArray(config.buttons) || config.buttons.length === 0) {
        setStatus("incomplete", "未配置按钮", "私域/跳转按钮模块需要配置至少一个按钮");
      }
      break;
    case "shop_redirect":
      if (!Array.isArray(config.shops) || config.shops.length === 0) {
        setStatus("incomplete", "未配置渠道", "购买渠道模块需要配置至少一个渠道");
      }
      break;
    case "lead_form":
      if (normalizeStringArray(config.fields).length === 0) {
        setStatus("incomplete", "未配置字段", "留资表单需要选择至少一个收集字段");
      }
      break;
    case "benefit_card":
    case "points_exchange":
      if (!config.benefit_id) {
        setStatus("incomplete", "未关联权益", "权益模块需要关联权益 ID");
      }
      break;
    case "custom_html":
      if (!config.html) {
        setStatus("incomplete", "未填写内容", "自定义 HTML 模块内容为空");
      }
      break;
    case "outer_code_guide":
      if (!config.product_name && !product?.name) {
        setStatus("example", "使用示例产品", "外码引导将使用示例产品信息");
      }
      break;
    default:
      break;
  }

  return {
    moduleId: module.id,
    moduleType: module.type,
    label,
    status,
    message,
    issues,
  };
}

/** 页面发布前完整度检查，供编辑器、发布确认和测试复用 */
export function inspectPageReadiness(
  dsl: PageDSL,
  context: PagePreviewContext = {},
): PageReadiness {
  const blockingIssues = [...validateDSL(dsl)];
  const warnings: string[] = [];
  const modules = Array.isArray(dsl.modules) ? dsl.modules : [];
  const enabledModules = modules.filter((module) => module.enabled !== false);

  if (!context.product) {
    blockingIssues.push("页面未关联产品，消费者扫码不会自动命中该页面");
  }

  if (enabledModules.length === 0) {
    blockingIssues.push("页面没有启用模块");
  }

  const moduleStatuses = enabledModules.map((module) => buildModuleReadiness(module, context));
  moduleStatuses.forEach((moduleStatus) => {
    if (moduleStatus.status === "incomplete") {
      warnings.push(`${moduleStatus.label}：${moduleStatus.issues[0] || moduleStatus.message}`);
    }
    if (moduleStatus.status === "example") {
      warnings.push(`${moduleStatus.label}：${moduleStatus.issues[0] || moduleStatus.message}`);
    }
  });

  return {
    blockingIssues,
    warnings,
    moduleStatuses,
    usesExampleData: moduleStatuses.some((moduleStatus) => moduleStatus.status === "example"),
  };
}

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
