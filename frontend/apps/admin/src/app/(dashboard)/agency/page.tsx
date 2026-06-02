"use client";

import { useEffect, useState } from "react";
import { App, Button, Space, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import api, { extractErrorMessage } from "@/lib/api";
import { StatsCards } from "./_components/StatsCards";
import { ClientTable } from "./_components/ClientTable";
import { TaskTable } from "./_components/TaskTable";
import { InitClientModal } from "./_components/InitClientModal";
import { CreateTaskModal, ChecklistModal } from "./_components/TaskModals";
import type { Client, Task, ChecklistResult } from "./_components/types";

const { Title } = Typography;

export default function AgencyPage() {
  const { message, modal } = App.useApp();
  const [clients, setClients] = useState<Client[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(false);
  const [initModalOpen, setInitModalOpen] = useState(false);
  const [taskModalOpen, setTaskModalOpen] = useState(false);
  const [overview, setOverview] = useState<{
    total_clients?: number;
    active_clients?: number;
    onboarding_clients?: number;
    pending_tasks?: number;
  }>({});

  const [checklistModalOpen, setChecklistModalOpen] = useState(false);
  const [checklistData, setChecklistData] = useState<ChecklistResult | null>(null);
  const [checklistClientName, setChecklistClientName] = useState("");
  const [checklistLoading, setChecklistLoading] = useState(false);

  const [taskFilter, setTaskFilter] = useState<{ tenant_id?: string; status?: string }>({});

  const fetchClients = async (q?: string) => {
    setLoading(true);
    try {
      const params: Record<string, unknown> = { page: 1, page_size: 100 };
      if (q) params.q = q;
      const { data } = await api.get("/tenants", { params });
      setClients((data.items || []).map((t: Record<string, unknown>) => ({
        id: String(t.id), name: String(t.name || ""), status: String(t.status || "active"),
        plan: String(t.plan || "free"), plan_expires_at: t.plan_expires_at ? String(t.plan_expires_at) : null, created_at: String(t.created_at || ""),
      })));
    } catch { setClients([]); }
    finally { setLoading(false); }
  };

  const fetchOverview = async () => {
    try {
      const { data } = await api.get("/ops/overview");
      setOverview(data || {});
    } catch {
      setOverview({});
    }
  };

  const fetchTasks = async (filter?: { tenant_id?: string; status?: string }) => {
    try {
      const params: Record<string, unknown> = { page: 1, page_size: 100 };
      const f = filter || taskFilter;
      if (f.tenant_id) params.tenant_id = f.tenant_id;
      if (f.status) params.status = f.status;
      const { data } = await api.get("/ops/tasks", { params });
      setTasks((data.items || []).map((t: Record<string, unknown>) => ({
        id: String(t.id), tenant_id: String(t.tenant_id || ""), title: String(t.title || ""),
        tenant_name: t.tenant_name ? String(t.tenant_name) : undefined,
        status: String(t.status || "pending"), priority: String(t.priority || "medium"), due_date: t.due_date ? String(t.due_date) : null,
      })));
    } catch { setTasks([]); }
  };

  useEffect(() => { fetchOverview(); fetchClients(); fetchTasks(); }, []);

  const handleOpenChecklist = async (clientId: string, clientName: string) => {
    setChecklistLoading(true); setChecklistClientName(clientName); setChecklistModalOpen(true);
    try { const { data } = await api.get(`/ops/clients/${clientId}/launch-checklist`); setChecklistData(data); }
    catch { setChecklistData(null); }
    finally { setChecklistLoading(false); }
  };

  const handleUpdateTaskStatus = async (taskId: string, newStatus: string) => {
    try { await api.patch(`/ops/tasks/${taskId}`, { status: newStatus }); message.success("任务状态已更新"); fetchTasks(); fetchOverview(); }
    catch (e: unknown) { message.error(extractErrorMessage(e, "更新失败")); }
  };

  const handleDeleteTask = async (taskId: string) => {
    try { await api.delete(`/ops/tasks/${taskId}`); message.success("任务已删除"); fetchTasks(); fetchOverview(); }
    catch (e: unknown) { message.error(extractErrorMessage(e, "删除失败")); }
  };

  const confirmDeleteTask = (taskId: string, taskTitle: string) => {
    modal.confirm({ title: "确认删除", content: `确定删除任务"${taskTitle}"吗？`, okText: "删除", okButtonProps: { danger: true }, onOk: () => handleDeleteTask(taskId) });
  };

  const handleFilterChange = (newFilter: { tenant_id?: string; status?: string }) => {
    setTaskFilter(newFilter);
    fetchTasks(newFilter);
  };

  const activeClients = overview.active_clients ?? clients.filter((c) => c.status === "active").length;
  const onboardingClients = overview.onboarding_clients ?? clients.filter((c) => c.status === "onboarding").length;
  const pendingTasks = overview.pending_tasks ?? tasks.filter((t) => t.status === "pending").length;

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">代运营工作台</Title>
        <Space>
          <Button icon={<PlusOutlined />} onClick={() => setTaskModalOpen(true)}>新建任务</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setInitModalOpen(true)}>初始化新客户</Button>
        </Space>
      </div>

      <StatsCards totalClients={overview.total_clients ?? clients.length} activeClients={activeClients} onboardingClients={onboardingClients} pendingTasks={pendingTasks} />
      <ClientTable clients={clients} loading={loading} onSearch={(q) => fetchClients(q)} onOpenChecklist={handleOpenChecklist} />
      <TaskTable tasks={tasks} clients={clients} taskFilter={taskFilter} onFilterChange={handleFilterChange} onUpdateStatus={handleUpdateTaskStatus} onDelete={confirmDeleteTask} />

      <InitClientModal open={initModalOpen} onClose={() => setInitModalOpen(false)} onSuccess={() => { fetchOverview(); fetchClients(); }} />
      <CreateTaskModal open={taskModalOpen} onClose={() => setTaskModalOpen(false)} clients={clients} onSuccess={() => { fetchOverview(); fetchTasks(); }} />
      <ChecklistModal open={checklistModalOpen} clientName={checklistClientName} onClose={() => { setChecklistModalOpen(false); setChecklistData(null); }} data={checklistData} loading={checklistLoading} />
    </div>
  );
}
