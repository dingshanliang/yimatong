export interface Client {
  id: string;
  name: string;
  status: string;
  plan: string;
  plan_expires_at: string | null;
  created_at: string;
}

export interface Task {
  id: string;
  tenant_id: string;
  title: string;
  status: string;
  priority: string;
  due_date: string | null;
}

export interface ChecklistResult {
  tenant_id: string;
  ready: boolean;
  passed_count: number;
  total_count: number;
  checks: { name: string; passed: boolean; detail: string }[];
}

export const STATUS_MAP: Record<string, { label: string; color: string }> = {
  active: { label: "活跃", color: "green" },
  suspended: { label: "已暂停", color: "default" },
  onboarding: { label: "配置中", color: "blue" },
};

export const PRIORITY_MAP: Record<string, { label: string; color: string }> = {
  low: { label: "低", color: "default" },
  medium: { label: "中", color: "blue" },
  high: { label: "高", color: "red" },
};

export const TASK_STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: "待处理", color: "default" },
  in_progress: { label: "进行中", color: "processing" },
  completed: { label: "已完成", color: "success" },
  cancelled: { label: "已取消", color: "default" },
};
