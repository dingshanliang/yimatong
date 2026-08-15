"use client";

import { useState } from "react";
import { Alert, App, Button, Empty, Table, Tabs, Tag, Typography } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import {
  newExportIdempotencyKey,
  useExportReasonDialog,
} from "@/components/ExportReasonDialog";
import { useAuthStore } from "@/lib/auth";
import { codeAccessForPrincipal } from "@/lib/code-access";
import { useCrud } from "@/lib/hooks";
import { STATUS_COLORS } from "@/lib/status-colors";
import { useTenantPlanReadOnly } from "../_components/TenantPlanReadOnly";

const { Title } = Typography;

interface CodeBatch {
  id: string;
  batch_code: string;
  quantity: number;
  expected_item_count: number;
  status: string;
  code_type: string;
  created_at: string;
}

interface ExportLog {
  id: string;
  export_type: string;
  resource_id: string;
  file_name: string;
  row_count: number;
  status: string;
  created_at: string;
}

const BATCH_STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: "待生成", color: STATUS_COLORS.neutral },
  generating: { label: "生成中", color: STATUS_COLORS.processing },
  completed: { label: "已生成", color: STATUS_COLORS.success },
  exported: { label: "已导出", color: STATUS_COLORS.processing },
  printing: { label: "印刷中", color: STATUS_COLORS.warning },
  delivered: { label: "已交付", color: STATUS_COLORS.warning },
  activated: { label: "已激活", color: STATUS_COLORS.success },
  failed: { label: "失败", color: STATUS_COLORS.error },
};

function getDownloadFilename(
  contentDisposition: string | undefined,
  fallback: string
) {
  if (!contentDisposition) return fallback;
  const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    return decodeURIComponent(utf8Match[1].replace(/"/g, ""));
  }
  const filenameMatch = contentDisposition.match(/filename="?([^";]+)"?/i);
  return filenameMatch?.[1] || fallback;
}

export default function ExportsPage() {
  const user = useAuthStore((state) => state.user);
  const access = codeAccessForPrincipal(user);

  if (user?.tenant_type !== "brand" || !access.canManage) {
    return <Alert type="warning" showIcon title="当前账号无权查看导出记录" />;
  }

  return <ExportsCatalog />;
}

function ExportsCatalog() {
  const { message } = App.useApp();
  const { requestReason, exportReasonDialog } = useExportReasonDialog();
  const planReadOnly = useTenantPlanReadOnly();
  const {
    items: batches,
    total: batchTotal,
    page: batchPage,
    loading: batchLoading,
    setPage: setBatchPage,
    mutate: mutateBatches,
    error: batchError,
    retry: retryBatches,
  } = useCrud<CodeBatch>("/code-batches");

  const {
    items: exports,
    total: exportTotal,
    page: exportPage,
    loading: exportLoading,
    setPage: setExportPage,
    mutate: mutateExports,
    error: exportError,
    retry: retryExports,
  } = useCrud<ExportLog>("/analytics/exports");

  const [exportingId, setExportingId] = useState<string | null>(null);

  const handleExport = async (batchId: string) => {
    if (planReadOnly) return;
    const reason = await requestReason();
    if (!reason) return;
    setExportingId(batchId);
    try {
      const response = await api.post<Blob>(
        `/code-batches/${batchId}/export`,
        { reason },
        {
          headers: { "Idempotency-Key": newExportIdempotencyKey() },
          responseType: "blob",
        }
      );
      if (!response.data || response.data.size === 0) {
        message.error("当前码批次没有可下载的码表");
        return;
      }
      const url = window.URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = url;
      link.download = getDownloadFilename(
        response.headers["content-disposition"],
        `codes-${batchId}.csv`
      );
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      message.success("码表已导出");
      mutateBatches();
      mutateExports();
    } catch {
      message.error("导出失败");
    } finally {
      setExportingId(null);
    }
  };

  const batchColumns: ColumnsType<CodeBatch> = [
    {
      title: "批次名称",
      dataIndex: "batch_code",
      key: "batch_code",
      render: (v: string) => v || "—",
    },
    {
      title: "物理码数",
      dataIndex: "expected_item_count",
      key: "expected_item_count",
    },
    {
      title: "码类型",
      dataIndex: "code_type",
      key: "code_type",
      render: (v: string) => v || "standard",
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = BATCH_STATUS_MAP[s] || {
          label: s,
          color: STATUS_COLORS.neutral,
        };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "操作",
      key: "action",
      render: (_: unknown, record: CodeBatch) =>
        record.status === "completed" ? (
          <Button
            size="small"
            icon={<DownloadOutlined />}
            loading={exportingId === record.id}
            disabled={planReadOnly}
            onClick={() => handleExport(record.id)}
          >
            导出
          </Button>
        ) : null,
    },
  ];

  const exportColumns: ColumnsType<ExportLog> = [
    { title: "导出类型", dataIndex: "export_type", key: "export_type" },
    {
      title: "资源 ID",
      dataIndex: "resource_id",
      key: "resource_id",
      render: (v: string) => v?.slice(0, 8) + "...",
    },
    {
      title: "文件名",
      dataIndex: "file_name",
      key: "file_name",
      render: (v: string) => v || "—",
    },
    { title: "行数", dataIndex: "row_count", key: "row_count" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => (
        <Tag
          color={
            s === "completed"
              ? STATUS_COLORS.success
              : s === "failed"
                ? STATUS_COLORS.error
                : STATUS_COLORS.processing
          }
        >
          {s}
        </Tag>
      ),
    },
    {
      title: "导出时间",
      dataIndex: "created_at",
      key: "created_at",
      render: (v: string) => formatDate(v),
    },
  ];

  return (
    <div>
      {exportReasonDialog}
      <Title level={4}>导出管理</Title>
      <Tabs
        defaultActiveKey="exports"
        items={[
          {
            key: "exports",
            label: "导出记录",
            children: (
              <>
                {exportError ? (
                  <Alert
                    type="error"
                    showIcon
                    title="导出记录加载失败"
                    action={
                      <Button size="small" onClick={() => void retryExports()}>
                        重新加载
                      </Button>
                    }
                  />
                ) : exports.length === 0 && !exportLoading ? (
                  <Empty description="暂无导出记录" />
                ) : (
                  <Table
                    columns={exportColumns}
                    dataSource={exports}
                    rowKey="id"
                    loading={exportLoading}
                    pagination={{
                      current: exportPage,
                      total: exportTotal,
                      pageSize: 20,
                      onChange: setExportPage,
                      showTotal: (t) => `共 ${t} 条`,
                    }}
                  />
                )}
              </>
            ),
          },
          {
            key: "batches",
            label: "码批次导出",
            children: (
              <>
                {batchError ? (
                  <Alert
                    type="error"
                    showIcon
                    title="码批次加载失败"
                    action={
                      <Button size="small" onClick={() => void retryBatches()}>
                        重新加载
                      </Button>
                    }
                  />
                ) : batches.length === 0 && !batchLoading ? (
                  <Empty description="暂无可导出的码批次" />
                ) : (
                  <Table
                    columns={batchColumns}
                    dataSource={batches}
                    rowKey="id"
                    loading={batchLoading}
                    pagination={{
                      current: batchPage,
                      total: batchTotal,
                      pageSize: 20,
                      onChange: setBatchPage,
                      showTotal: (t) => `共 ${t} 条`,
                    }}
                  />
                )}
              </>
            ),
          },
        ]}
      />
    </div>
  );
}

function formatDate(dateStr?: string): string {
  if (!dateStr) return "-";
  const date = new Date(dateStr);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString("zh-CN");
}
