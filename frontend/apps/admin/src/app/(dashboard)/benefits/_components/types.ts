export interface Benefit {
  id: string;
  name: string;
  benefit_type: string;
  stock_total: number;
  stock_used: number;
  per_person_limit: number;
  campaign_id: string;
  connector_id: string | null;
  status: string;
  created_at: string;
  config_json: Record<string, unknown>;
}

export interface BenefitClaim {
  id: string;
  consumer_id: string;
  benefit_id: string;
  campaign_id: string;
  status: string;
  delivery_status: string;
  claimed_at: string;
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
