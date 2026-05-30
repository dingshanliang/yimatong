export interface Product {
  id: string;
  name: string;
  brand_id: string;
  brand_name?: string;
  category?: string;
  status: string;
  created_at: string;
}

export interface Brand {
  id: string;
  name: string;
}
