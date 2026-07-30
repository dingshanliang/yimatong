"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, App, Button, Space, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import api, { extractErrorMessage } from "@/lib/api";
import { StatsCards } from "./_components/StatsCards";
import { ClientTable } from "./_components/ClientTable";
import { TaskTable } from "./_components/TaskTable";
import { InitClientModal } from "./_components/InitClientModal";
import { CreateTaskModal, ChecklistModal } from "./_components/TaskModals";
import type {
  AgencyClientRow,
  AgencyWorkbenchResponse,
  ChecklistResult,
  WorkbenchSummary,
  WorkbenchTask,
} from "./_components/types";

const { Title } = Typography;

const EMPTY_SUMMARY: WorkbenchSummary = {
  total_clients: 0,
  active_clients: 0,
  ready_clients: 0,
  blocked_clients: 0,
  pending_tasks: 0,
  in_progress_tasks: 0,
  overdue_tasks: 0,
};

export default function AgencyPage() {
  const { message, modal } = App.useApp();
  const [clients, setClients] = useState<AgencyClientRow[]>([]);
  const [tasks, setTasks] = useState<WorkbenchTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [workbenchError, setWorkbenchError] = useState(false);
  const [initModalOpen, setInitModalOpen] = useState(false);
  const [taskModalOpen, setTaskModalOpen] = useState(false);
  const [taskInitialValues, setTaskInitialValues] = useState<{
    tenantId?: string;
    title?: string;
  }>({});
  const [overview, setOverview] = useState<WorkbenchSummary>(EMPTY_SUMMARY);
  const [currentTime] = useState(() => Date.now());

  const [checklistModalOpen, setChecklistModalOpen] = useState(false);
  const [checklistData, setChecklistData] = useState<ChecklistResult | null>(
    null
  );
  const [checklistClientName, setChecklistClientName] = useState("");
  const [checklistClientId, setChecklistClientId] = useState("");
  const [checklistLoading, setChecklistLoading] = useState(false);

  const [workbenchFilter, setWorkbenchFilter] = useState<{
    q?: string;
    readiness?: string;
    task_status?: string;
  }>({});

  const fetchWorkbench = useCallback(
    async (
      filter: { q?: string; readiness?: string; task_status?: string } = {}
    ) => {
      setLoading(true);
      setWorkbenchError(false);
      try {
        const params: Record<string, unknown> = { page: 1, page_size: 100 };
        if (filter.q) params.q = filter.q;
        if (filter.readiness && filter.readiness !== "all")
          params.readiness = filter.readiness;
        if (filter.task_status && filter.task_status !== "all")
          params.task_status = filter.task_status;
        const { data } = await api.get("/ops/workbench", { params });
        const workbench = data as AgencyWorkbenchResponse;
        setOverview(workbench.summary || EMPTY_SUMMARY);
        setClients(workbench.clients || []);
        setTasks(workbench.tasks || []);
      } catch {
        setWorkbenchError(true);
        setClients([]);
        setTasks([]);
      } finally {
        setLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    fetchWorkbench({});
  }, [fetchWorkbench]);

  const handleOpenChecklist = async (clientId: string, clientName: string) => {
    setChecklistLoading(true);
    setChecklistClientName(clientName);
    setChecklistClientId(clientId);
    setChecklistModalOpen(true);
    try {
      const { data } = await api.get(
        `/ops/clients/${clientId}/launch-checklist`
      );
      setChecklistData(data);
    } catch {
      setChecklistData(null);
    } finally {
      setChecklistLoading(false);
    }
  };

  const handleRetryChecklist = async () => {
    if (!checklistClientId) return;
    setChecklistLoading(true);
    setChecklistData(null);
    try {
      const { data } = await api.get(
        `/ops/clients/${checklistClientId}/launch-checklist`
      );
      setChecklistData(data);
    } catch {
      setChecklistData(null);
    } finally {
      setChecklistLoading(false);
    }
  };

  const handleUpdateTaskStatus = async (taskId: string, newStatus: string) => {
    try {
      await api.patch(`/ops/tasks/${taskId}`, { status: newStatus });
      message.success("任务状态已更新");
      fetchWorkbench(workbenchFilter);
    } catch (e: unknown) {
      message.error(extractErrorMessage(e, "更新失败"));
    }
  };

  const handleDeleteTask = async (taskId: string) => {
    try {
      await api.delete(`/ops/tasks/${taskId}`);
      message.success("任务已删除");
      fetchWorkbench(workbenchFilter);
    } catch (e: unknown) {
      message.error(extractErrorMessage(e, "删除失败"));
    }
  };

  const expiringClients = useMemo(
    () =>
      clients.filter((c) => {
        if (!c.plan_expires_at) return false;
        const daysLeft = Math.ceil(
          (new Date(c.plan_expires_at).getTime() - currentTime) /
            (1000 * 60 * 60 * 24)
        );
        return daysLeft >= 0 && daysLeft < 30;
      }),
    [clients, currentTime]
  );

  const confirmDeleteTask = (taskId: string, taskTitle: string) => {
    modal.confirm({
      title: "确认删除",
      content: `确定删除任务"${taskTitle}"吗？`,
      okText: "删除",
      okButtonProps: { danger: true },
      onOk: () => handleDeleteTask(taskId),
    });
  };

  const handleFilterChange = (newFilter: {
    q?: string;
    readiness?: string;
    task_status?: string;
  }) => {
    setWorkbenchFilter(newFilter);
    fetchWorkbench(newFilter);
  };

  const handleStatsCardClick = (
    filterType: "overdue" | "blocked" | "ready" | "pending"
  ) => {
    switch (filterType) {
      case "ready":
        handleFilterChange({ ...workbenchFilter, readiness: "ready" });
        break;
      case "blocked":
        handleFilterChange({ ...workbenchFilter, readiness: "blocked" });
        break;
      case "overdue":
        handleFilterChange({ ...workbenchFilter, task_status: "overdue" });
        break;
      case "pending":
        handleFilterChange({ ...workbenchFilter, task_status: "pending" });
        break;
    }
  };

  const handleCreateTaskFromClient = (tenantId: string, title: string) => {
    setTaskInitialValues({ tenantId, title });
    setTaskModalOpen(true);
  };

  const handleOpenEmptyTaskModal = () => {
    setTaskInitialValues({});
    setTaskModalOpen(true);
  };

  const handleCloseTaskModal = () => {
    setTaskModalOpen(false);
    setTaskInitialValues({});
  };

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">
          代运营工作台
        </Title>
        <Space>
          <Button icon={<PlusOutlined />} onClick={handleOpenEmptyTaskModal}>
            新建任务
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setInitModalOpen(true)}
          >
            初始化新客户
          </Button>
        </Space>
      </div>

      <StatsCards summary={overview} onCardClick={handleStatsCardClick} />

      {/* 即将到期客户提醒 */}
      {expiringClients.length > 0 && (
        <Alert
          className="mb-4"
          type="warning"
          showIcon
          message="有客户套餐即将到期"
          description={
            <span>
              以下客户套餐将在 30 天内到期：
              {expiringClients.map((c) => ` ${c.name}`).join("、")}
            </span>
          }
        />
      )}

      {workbenchError && (
        <Alert
          className="mb-4"
          type="error"
          showIcon
          message="工作台数据加载失败"
          description="请检查网络或后端服务状态后重试。"
          action={
            <Button
              size="small"
              onClick={() => fetchWorkbench(workbenchFilter)}
            >
              重新加载
            </Button>
          }
        />
      )}
      <ClientTable
        clients={clients}
        loading={loading}
        filter={workbenchFilter}
        hasAnyClients={overview.total_clients > 0}
        hasError={workbenchError}
        onFilterChange={handleFilterChange}
        onOpenChecklist={handleOpenChecklist}
        onCreateTask={handleCreateTaskFromClient}
      />
      <TaskTable
        tasks={tasks}
        clients={clients}
        onUpdateStatus={handleUpdateTaskStatus}
        onDelete={confirmDeleteTask}
      />

      <InitClientModal
        open={initModalOpen}
        onClose={() => setInitModalOpen(false)}
        onSuccess={() => {
          fetchWorkbench(workbenchFilter);
        }}
      />
      <CreateTaskModal
        open={taskModalOpen}
        onClose={handleCloseTaskModal}
        clients={clients}
        initialTenantId={taskInitialValues.tenantId}
        initialTitle={taskInitialValues.title}
        onSuccess={() => {
          fetchWorkbench(workbenchFilter);
        }}
      />
      <ChecklistModal
        open={checklistModalOpen}
        clientName={checklistClientName}
        onClose={() => {
          setChecklistModalOpen(false);
          setChecklistData(null);
          setChecklistClientId("");
        }}
        data={checklistData}
        loading={checklistLoading}
        onRetry={handleRetryChecklist}
      />
    </div>
  );
}
