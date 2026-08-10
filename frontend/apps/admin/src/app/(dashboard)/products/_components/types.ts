export interface Product {
  id: string;
  name: string;
  brand_id: string;
  brand_name?: string;
  category?: string;
  origin?: string;
  image_url?: string;
  story_title?: string;
  story_content?: string;
  description?: string;
  status: string;
  created_at?: string;
}

export interface Brand {
  id: string;
  name: string;
}

export type ProductAssetType =
  "image" | "video" | "test_report" | "certificate" | "story" | "other";

export interface ProductAsset {
  id: string;
  product_id: string;
  asset_type: ProductAssetType;
  name: string;
  description?: string;
  issuer?: string;
  valid_until?: string;
  file_url?: string;
  image_url?: string;
  content_text?: string;
  metadata_json?: Record<string, unknown>;
  status: string;
}

export interface SKU {
  id: string;
  product_id: string;
  product_name?: string;
  code: string;
  name: string;
  specifications?: Record<string, string>;
  package_type?: string;
  barcode?: string;
  image_url?: string;
  status: string;
}

export interface ProductionBatch {
  id: string;
  product_id: string;
  product_name?: string;
  sku_id: string;
  sku_name?: string;
  sku_code?: string;
  batch_code: string;
  production_date: string;
  expiry_date: string;
  origin?: string;
  status: string;
  effective_status: string;
}
