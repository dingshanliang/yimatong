export interface Benefit {
  id: string;
  name: string;
  benefit_type: string;
  stock_total: number;
  stock_used: number;
  per_person_limit: number;
  campaign_id: string | null;
  connector_id: string | null;
  status: string;
  created_at: string;
  updated_at?: string;
  config_json: Record<string, unknown>;
}

export interface BenefitClaim {
  id: string;
  consumer_id: string;
  benefit_id: string;
  campaign_id: string;
  benefit_name?: string | null;
  campaign_name?: string | null;
  status: string;
  delivery_status: string;
  claimed_at: string | null;
  latest_delivery_id?: string | null;
  latest_delivery_status?: string | null;
  delivery_retry_count?: number | null;
  delivery_next_retry_at?: string | null;
}

export interface BenefitDelivery {
  id: string;
  connector_id: string;
  consumer_id: string;
  benefit_id?: string | null;
  claim_id?: string | null;
  benefit_type: string;
  status: string;
  retry_count: number;
  max_retries: number;
  external_data: Record<string, unknown> | null;
  next_retry_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface Campaign {
  id: string;
  name: string;
}

export interface Connector {
  id: string;
  name: string;
  connector_type: string;
  enabled: boolean;
  config: Record<string, unknown>;
}

export interface BenefitSummary {
  total: number;
  active: number;
  unused: number;
  stock_total: number;
  stock_used: number;
  stock_remaining: number;
  claim_count: number;
  failed_delivery_count: number;
}
