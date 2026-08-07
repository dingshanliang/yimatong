"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, App, Button, Space, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { useRouter } from "next/navigation";
import api, { extractErrorMessage } from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { StatsCards } from "./_components/StatsCards";
import { ClientTable } from "./_components/ClientTable";
import { TaskTable } from "./_components/TaskTable";
import { CreateTaskModal, ChecklistModal } from "./_components/TaskModals";
import PilotAggregate from "./_components/PilotAggregate";
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
  const router = useRouter();
  const [clients, setClients] = useState<AgencyClientRow[]>([]);
  const [tasks, setTasks] = useState<WorkbenchTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [workbenchError, setWorkbenchError] = useState(false);
  const [enteringClientId, setEnteringClientId] = useState<string>();
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

  const handleEnterClient = async (client: AgencyClientRow) => {
    setEnteringClientId(client.id);
    try {
      await useAuthStore.getState().switchAgencyContext(client.id);
      const liveScope = useAuthStore.getState().user?.agency_scope;
      const scope = liveScope || client.agency_scope || [];
      const firstAllowedRoute = scope.includes("analytics")
        ? "/analytics"
        : scope.includes("products")
          ? "/products"
          : scope.includes("pages")
            ? "/pages"
            : scope.includes("campaigns")
              ? "/campaigns"
              : scope.includes("codes")
                ? "/codes"
                : "/agency";
      router.push(firstAllowedRoute);
    } catch (error: unknown) {
      const status = (error as { response?: { status?: number } })?.response
        ?.status;
      if (status === 403) {
        await fetchWorkbench(workbenchFilter);
        message.error("该客户的代运营授权已失效，请联系客户重新授权后再进入");
      } else {
        message.error(
          extractErrorMessage(error, "进入客户失败，请检查网络后重试")
        );
      }
    } finally {
      setEnteringClientId(undefined);
    }
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
          {clients.some((client) => client.full_workbench_access) && (
            <Button icon={<PlusOutlined />} onClick={handleOpenEmptyTaskModal}>
              新建任务
            </Button>
          )}
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
        onEnterClient={handleEnterClient}
        enteringClientId={enteringClientId}
      />
      <TaskTable
        tasks={tasks}
        clients={clients.filter((client) => client.full_workbench_access)}
        onUpdateStatus={handleUpdateTaskStatus}
        onDelete={confirmDeleteTask}
      />

      {/* 试点复盘跨租户聚合（beads: yimatong-bgag.10，PRD §4.5） */}
      <div className="mb-4 mt-4">
        <Title level={5} className="!mb-2">
          试点复盘聚合
        </Title>
        <PilotAggregate />
      </div>

      <CreateTaskModal
        open={taskModalOpen}
        onClose={handleCloseTaskModal}
        clients={clients.filter((client) => client.full_workbench_access)}
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
