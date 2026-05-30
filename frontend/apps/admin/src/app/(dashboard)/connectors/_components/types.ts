export interface Connector {
  id: string;
  name: string;
  connector_type: string;
  config: Record<string, unknown>;
  enabled: boolean;
  secrets?: Record<string, string>;
  created_at: string;
  updated_at: string;
}

export interface Delivery {
  id: string;
  connector_id: string;
  consumer_id: string;
  benefit_type: string;
  status: string;
  retry_count: number;
  max_retries: number;
  external_data: Record<string, unknown> | null;
  next_retry_at: string | null;
}

export interface Pool {
  id: string;
  name: string;
  total_codes: number;
  remaining: number;
}

export interface PoolCode {
  id: string;
  code: string;
  consumer_id: string | null;
  distributed: boolean;
}

export const TYPE_LABELS: Record<string, string> = {
  generic_http: "通用 HTTP",
  coupon_pool: "券码池",
  youzan: "有赞",
  wechat_pay: "微信支付商家券",
  alipay: "支付宝商家券",
};
