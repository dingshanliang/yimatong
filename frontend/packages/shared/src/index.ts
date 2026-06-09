// 一码通共享类型定义
// 本包只导出 TypeScript 类型和常量，不包含运行时代码

// ─── Auth ────────────────────────────────────

export interface AuthUser {
  account_id: string;
  tenant_id: string;
  role: string;
  email: string;
  name: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

// ─── Pagination ──────────────────────────────

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface PaginationParams {
  page?: number;
  page_size?: number;
}

// ─── Brand ───────────────────────────────────

export interface Brand {
  id: string;
  tenant_id: string;
  name: string;
  logo_url?: string;
  description?: string;
  industry?: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface BrandCreateRequest {
  name: string;
  logo_url?: string;
  description?: string;
  industry?: string;
}

// ─── Product ─────────────────────────────────

export interface Product {
  id: string;
  tenant_id: string;
  brand_id: string;
  name: string;
  category?: string;
  description?: string;
  images?: string[];
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProductCreateRequest {
  name: string;
  brand_id: string;
  category?: string;
  description?: string;
}

// ─── SKU ─────────────────────────────────────

export interface SKU {
  id: string;
  tenant_id: string;
  product_id: string;
  sku_code: string;
  name: string;
  specs?: Record<string, string>;
  is_active: boolean;
  created_at: string;
}

export interface SKUCreateRequest {
  product_id: string;
  sku_code: string;
  name: string;
  specs?: Record<string, string>;
}

// ─── Code Batch ──────────────────────────────

export type CodeBatchStatus = "created" | "activated" | "bound" | "exported";

export interface CodeBatch {
  id: string;
  tenant_id: string;
  batch_no: string;
  code_type: string;
  quantity: number;
  status: CodeBatchStatus;
  product_id?: string;
  template_id?: string;
  created_at: string;
}

// ─── Page Template ───────────────────────────

export type PageVersionStatus = "draft" | "published" | "offline";

export interface PageTemplate {
  id: string;
  tenant_id: string;
  name: string;
  industry?: string;
  is_industry: boolean;
  current_version?: PageVersion;
  created_at: string;
  updated_at: string;
}

export interface PageVersion {
  id: string;
  template_id: string;
  version_number: number;
  status: PageVersionStatus;
  config_json: Record<string, unknown>;
  published_at?: string;
  created_at: string;
}

// ─── Campaign ────────────────────────────────

export type CampaignStatus = "draft" | "active" | "paused" | "ended";
export type ComputedCampaignStatus = CampaignStatus | "pending";
export type CampaignType = "coupon" | "lottery" | "points";

export interface Campaign {
  id: string;
  tenant_id: string;
  name: string;
  campaign_type: CampaignType;
  status: CampaignStatus;
  computed_status?: ComputedCampaignStatus;
  product_id?: string | null;
  product_name?: string | null;
  start_at: string;
  end_at: string;
  rules_json: Record<string, unknown>;
  description?: string | null;
  // 统计字段
  benefit_count: number;
  stock_total: number;
  stock_used: number;
  claim_count: number;
  wecom_add_count?: number;
  created_at: string;
  updated_at: string;
}

export interface CampaignCreateRequest {
  name: string;
  campaign_type: CampaignType;
  start_at: string;
  end_at: string;
  product_id?: string | null;
  rules_json?: Record<string, unknown>;
  description?: string | null;
}

// ─── Benefit ─────────────────────────────────

export type BenefitStatus = "active" | "inactive";

export interface Benefit {
  id: string;
  tenant_id: string;
  campaign_id?: string | null;
  name: string;
  benefit_type: string;
  config_json: Record<string, unknown>;
  connector_id?: string | null;
  stock_total: number;
  stock_used: number;
  per_person_limit: number;
  status: BenefitStatus | string;
  created_at: string;
  updated_at: string;
}

// ─── Member & Points ─────────────────────────

export interface PointTransaction {
  id: string;
  amount: number;
  balance_after: number;
  txn_type: "earning" | "spending" | "expired";
  reason?: string | null;
  created_at?: string | null;
  expires_at?: string | null;
}

export interface PointProduct {
  id: string;
  name: string;
  description?: string | null;
  image_url?: string | null;
  points_cost: number;
  stock: number;
  total_claimed: number;
  enabled: boolean;
  benefit_id?: string | null;
  starts_at?: string | null;
  ends_at?: string | null;
  per_consumer_limit: number;
  sort_order: number;
  can_exchange?: boolean | null;
  exchange_block_reason?: string | null;
}

export interface ConsumerProfile {
  id: string;
  nickname?: string | null;
  phone?: string | null;
  member_level: "normal" | "silver" | "gold" | "platinum";
  total_points: number;
}

export interface MemberOverview {
  enabled_rules: number;
  active_products: number;
  points_awarded_7d: number;
  points_spent_7d: number;
  redemptions_7d: number;
  low_stock_products: number;
}

// ─── Organization & Account ──────────────────

export interface Organization {
  id: string;
  tenant_id: string;
  name: string;
  parent_id?: string;
  is_active: boolean;
  created_at: string;
}

export interface Account {
  id: string;
  tenant_id: string;
  org_id?: string;
  email: string;
  name: string;
  role: string;
  is_active: boolean;
  created_at: string;
}

// ─── Analytics ───────────────────────────────

export interface ScanSummary {
  total_scans: number;
  uv: number;
  first_scans: number;
  rescans: number;
}

export interface ScanDetail {
  date: string;
  total_scans: number;
  uv: number;
  first_scans: number;
  rescans: number;
}

// ─── Code Resolution (H5) ────────────────────

export interface Certificate {
  name: string;
  issuer?: string;
  valid_until?: string;
  cert_number?: string;
  image_url?: string;
  file_url?: string;
}

export interface MediaItem {
  type: "video" | "image";
  url: string;
  poster_url?: string;
  caption?: string;
}

export interface ResolveResponse {
  scan_token: string;
  code_data: {
    public_id: string;
    code_type: string;
    status: string;
    product?: Product;
    brand?: Brand;
    batch?: {
      batch_no: string;
      production_date?: string;
      expiry_date?: string;
      origin?: string;
    };
    test_reports?: Array<{
      id: string;
      title: string;
      summary?: string;
      image_url?: string;
      file_url?: string;
      date?: string;
    }>;
    certificates?: Certificate[];
    media_items?: MediaItem[];
  };
  scan_info?: {
    is_first_scan: boolean;
    scan_count: number;
    first_scan_time?: string;
  };
  campaign?: {
    name?: string;
    rules?: Record<string, unknown>;
  };
  page_config?: Record<string, unknown>;
  tenant_branding?: {
    name: string;
    logo_url?: string;
    primary_color?: string;
  };
}

// ─── JWT Utilities ───────────────────────────

export { parseJwtPayload, isJwtExpired } from "./jwt";
export type { JwtPayload } from "./jwt";
