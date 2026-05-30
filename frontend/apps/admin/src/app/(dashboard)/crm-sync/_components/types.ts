export interface SyncMapping {
  id: string;
  consumer_id: string;
  external_id: string;
  source_system: string;
  sync_direction: string;
  last_synced_at: string | null;
  status: string;
}

export interface SyncLog {
  id: string;
  sync_type: string;
  external_id: string;
  data_summary: string;
  status: string;
  error_message: string | null;
  created_at: string;
}
