"use client";

import { useState } from "react";
import { App, Button, Table, Tabs, Tag, Typography } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { useCrud } from "@/lib/hooks";

const { Title } = Typography;

interface CodeBatch {
  id: string;
  name?: string;
  quantity: number;
  status: string;
  code_type?: string;
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
  draft: { label: "草稿", color: "default" },
  activated: { label: "已激活", color: "green" },
  frozen: { label: "已冻结", color: "orange" },
  voided: { label: "已作废", color: "red" },
};

export default function ExportsPage() {
  const { message } = App.useApp();
  const {
    items: batches, total: batchTotal, page: batchPage, loading: batchLoading,
    setPage: setBatchPage, mutate: mutateBatches,
  } = useCrud<CodeBatch>("/code-batches");

  const {
    items: exports, total: exportTotal, page: exportPage, loading: exportLoading,
    setPage: setExportPage, mutate: mutateExports,
  } = useCrud<ExportLog>("/analytics/exports");

  const [exportingId, setExportingId] = useState<string | null>(null);

  const handleExport = async (batchId: string) => {
    setExportingId(batchId);
    try {
      await api.post(`/code-batches/${batchId}/export`);
      message.success("导出任务已提交");
      mutateBatches();
      mutateExports();
    } catch {
      message.error("导出失败");
    } finally {
      setExportingId(null);
    }
  };

  const batchColumns: ColumnsType<CodeBatch> = [
    { title: "批次名称", dataIndex: "name", key: "name", render: (v: string) => v || "—" },
    { title: "码数量", dataIndex: "quantity", key: "quantity" },
    { title: "码类型", dataIndex: "code_type", key: "code_type", render: (v: string) => v || "standard" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = BATCH_STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "操作",
      key: "action",
      render: (_: unknown, record: CodeBatch) => (
        <Button
          size="small"
          icon={<DownloadOutlined />}
          loading={exportingId === record.id}
          onClick={() => handleExport(record.id)}
        >
          导出
        </Button>
      ),
    },
  ];

  const exportColumns: ColumnsType<ExportLog> = [
    { title: "导出类型", dataIndex: "export_type", key: "export_type" },
    { title: "资源 ID", dataIndex: "resource_id", key: "resource_id", render: (v: string) => v?.slice(0, 8) + "..." },
    { title: "文件名", dataIndex: "file_name", key: "file_name", render: (v: string) => v || "—" },
    { title: "行数", dataIndex: "row_count", key: "row_count" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => <Tag color={s === "completed" ? "green" : s === "failed" ? "red" : "blue"}>{s}</Tag>,
    },
    { title: "导出时间", dataIndex: "created_at", key: "created_at", render: (v: string) => formatDate(v) },
  ];

  return (
    <div>
      <Title level={4}>导出管理</Title>
      <Tabs
        defaultActiveKey="exports"
        items={[
          {
            key: "exports",
            label: "导出记录",
            children: (
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
            ),
          },
          {
            key: "batches",
            label: "码批次导出",
            children: (
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
            ),
          },
        ]}
      />
    </div>
  );
}

function formatDate(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleString("zh-CN");
  } catch {
    return dateStr;
  }
}
