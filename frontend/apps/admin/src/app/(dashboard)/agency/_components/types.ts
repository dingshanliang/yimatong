export interface Client {
  id: string;
  name: string;
  status: string;
  plan: string;
  industry?: string | null;
  plan_expires_at: string | null;
  created_at: string;
}

export interface Task {
  id: string;
  tenant_id: string;
  tenant_name?: string;
  title: string;
  status: string;
  priority: string;
  due_date: string | null;
  assigned_to?: string | null;
}

export interface ChecklistResult {
  tenant_id: string;
  ready: boolean;
  passed_count: number;
  total_count: number;
  checks: { name: string; passed: boolean; detail: string }[];
}

export interface ReadinessSummary {
  ready: boolean;
  passed_count: number;
  total_count: number;
  percent: number;
  missing_keys: string[];
  missing_labels: string[];
}

export interface TaskSummary {
  pending: number;
  in_progress: number;
  overdue: number;
  high_priority: number;
}

export interface NextAction {
  type: string;
  label: string;
  href: string;
  task_title: string;
}

export interface AgencyClientRow extends Client {
  readiness: ReadinessSummary;
  task_summary: TaskSummary;
  next_action: NextAction;
}

export interface WorkbenchSummary {
  total_clients: number;
  active_clients: number;
  ready_clients: number;
  blocked_clients: number;
  pending_tasks: number;
  in_progress_tasks: number;
  overdue_tasks: number;
}

export interface WorkbenchTask extends Task {
  description?: string | null;
  overdue: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface AgencyWorkbenchResponse {
  summary: WorkbenchSummary;
  clients: AgencyClientRow[];
  tasks: WorkbenchTask[];
  total: number;
  page: number;
  page_size: number;
}

export interface IndustryTemplate {
  id: number;
  name: string;
  template_type: string;
  description: string;
}

export const STATUS_MAP: Record<string, { label: string; color: string }> = {
  active: { label: "活跃", color: "#16a34a" },
  suspended: { label: "已暂停", color: "#8c8c8c" },
  onboarding: { label: "配置中", color: "#1d4ed8" },
};

export const PRIORITY_MAP: Record<string, { label: string; color: string }> = {
  low: { label: "低", color: "#8c8c8c" },
  medium: { label: "中", color: "#1d4ed8" },
  high: { label: "高", color: "#b91c1c" },
};

export const TASK_STATUS_MAP: Record<string, { label: string; color: string }> =
  {
    pending: { label: "待处理", color: "#8c8c8c" },
    in_progress: { label: "进行中", color: "#1d4ed8" },
    completed: { label: "已完成", color: "#16a34a" },
    cancelled: { label: "已取消", color: "#8c8c8c" },
  };
