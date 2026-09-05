"use client";

import { useCrud } from "@/lib/hooks";
import { Alert, App, Button, Popconfirm, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import api, { extractErrorMessage } from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { riskAccessForPrincipal } from "@/lib/risk-access";
import { STATUS_COLORS } from "@/lib/status-colors";
import { useTenantPlanReadOnly } from "../../_components/TenantPlanReadOnly";

type RiskAlertRow = Record<string, unknown> & { id: string };

export function AlertsTab() {
  const user = useAuthStore((state) => state.user);
  const access = riskAccessForPrincipal(user);

  if (!access.canManage) {
    return <Alert type="warning" showIcon title="当前账号无权查看风险预警" />;
  }

  return <AlertsWorkspace />;
}

function AlertsWorkspace() {
  const { message } = App.useApp();
  const planReadOnly = useTenantPlanReadOnly();
  const { items, total, page, loading, error, setPage, mutate, retry } =
    useCrud<RiskAlertRow>("/risk-alerts");

  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        title="风险预警加载失败"
        action={
          <Button size="small" onClick={() => void retry()}>
            重试
          </Button>
        }
      />
    );
  }

  const columns: ColumnsType<RiskAlertRow> = [
    { title: "码 ID", dataIndex: "public_id", key: "public_id" },
    {
      title: "类型",
      dataIndex: "alert_type",
      key: "alert_type",
      render: (t: string) => <Tag color={STATUS_COLORS.warning}>{t}</Tag>,
    },
    { title: "详情", dataIndex: "detail", key: "detail", ellipsis: true },
    {
      title: "状态",
      dataIndex: "resolved",
      key: "resolved",
      render: (v: boolean) => (
        <Tag color={v ? STATUS_COLORS.success : STATUS_COLORS.error}>
          {v ? "已处理" : "待处理"}
        </Tag>
      ),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record) =>
        !record.resolved ? (
          <Popconfirm
            title="确认标记为已处理？"
            onConfirm={async () => {
              if (planReadOnly) return;
              try {
                // onConfirm 返回 Promise 时 Popconfirm 的确认按钮自带 loading
                await api.post(`/risk-alerts/${record.id as string}/resolve`);
                message.success("已处理");
              } catch (e: unknown) {
                message.error(extractErrorMessage(e, "处理失败，请重试"));
              } finally {
                mutate();
              }
            }}
          >
            <Button size="small" type="link" disabled={planReadOnly}>
              处理
            </Button>
          </Popconfirm>
        ) : null,
    },
  ];

  return (
    <Table
      columns={columns}
      dataSource={items}
      rowKey="id"
      loading={loading}
      pagination={{
        current: page,
        total,
        pageSize: 20,
        onChange: setPage,
        showTotal: (t) => `共 ${t} 条`,
      }}
    />
  );
}
