"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Table,
  Tabs,
  Tag,
  Button,
  Space,
  Typography,
  message,
  Empty,
} from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title } = Typography;

interface CodeBatch {
  id: string;
  batch_code: string;
  quantity: number;
  status: string;
  product_name?: string;
  exported_at?: string;
  export_status?: string;
  created_at: string;
}

interface AuditRow {
  id: string;
  batch_code: string;
  operator: string;
  action: string;
  timestamp: string;
  detail: string;
}

const EXPORT_STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: "待导出", color: "default" },
  processing: { label: "导出中", color: "blue" },
  completed: { label: "已完成", color: "green" },
  failed: { label: "导出失败", color: "red" },
};

const BATCH_STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: "待生成", color: "default" },
  generating: { label: "生成中", color: "blue" },
  completed: { label: "已完成", color: "green" },
  failed: { label: "失败", color: "red" },
};

// Mock audit data skeleton - backend API not yet available
const MOCK_AUDIT_DATA: AuditRow[] = [
  {
    id: "1",
    batch_code: "CB-2026-001",
    operator: "管理员",
    action: "导出",
    timestamp: "2026-05-27 14:30:00",
    detail: "导出 10,000 码",
  },
  {
    id: "2",
    batch_code: "CB-2026-002",
    operator: "运营专员",
    action: "导出",
    timestamp: "2026-05-26 10:15:00",
    detail: "导出 5,000 码",
  },
];

export default function ExportsPage() {
  const [batches, setBatches] = useState<CodeBatch[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [exportingId, setExportingId] = useState<string | null>(null);

  const fetchBatches = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/code-batches", {
        params: { page, page_size: 20 },
      });
      const items = (data.items || []) as CodeBatch[];
      // Filter to show batches that are completed/generated (exportable)
      setBatches(items);
      setTotal(data.total || 0);
    } catch {
      message.error("加载码批次列表失败");
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => {
    fetchBatches();
  }, [fetchBatches]);

  const handleExport = async (batchId: string) => {
    setExportingId(batchId);
    try {
      await api.post(`/code-batches/${batchId}/export`);
      message.success("导出任务已提交，请稍后刷新查看");
      fetchBatches();
    } catch {
      message.error("导出失败");
    } finally {
      setExportingId(null);
    }
  };

  const batchColumns: ColumnsType<CodeBatch> = [
    { title: "批次号", dataIndex: "batch_code", key: "batch_code" },
    {
      title: "产品",
      dataIndex: "product_name",
      key: "product_name",
      render: (v: string) => v || "-",
    },
    {
      title: "数量",
      dataIndex: "quantity",
      key: "quantity",
    },
    {
      title: "批次状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = BATCH_STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "导出状态",
      dataIndex: "export_status",
      key: "export_status",
      render: (s: string) => {
        if (!s) return <Tag>未导出</Tag>;
        const info = EXPORT_STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "导出时间",
      dataIndex: "exported_at",
      key: "exported_at",
      render: (v: string) => (v ? dayjsFormat(v) : "-"),
    },
    {
      title: "操作",
      key: "action",
      render: (_, record) => (
        <Space>
          <Button
            size="small"
            icon={<DownloadOutlined />}
            loading={exportingId === record.id}
            onClick={() => handleExport(record.id)}
            disabled={record.status !== "completed"}
          >
            导出
          </Button>
        </Space>
      ),
    },
  ];

  const auditColumns: ColumnsType<AuditRow> = [
    { title: "批次号", dataIndex: "batch_code", key: "batch_code" },
    { title: "操作人", dataIndex: "operator", key: "operator" },
    { title: "操作类型", dataIndex: "action", key: "action" },
    { title: "时间", dataIndex: "timestamp", key: "timestamp" },
    { title: "详情", dataIndex: "detail", key: "detail" },
  ];

  return (
    <div>
      <Title level={4}>导出管理</Title>
      <Tabs
        defaultActiveKey="batches"
        items={[
          {
            key: "batches",
            label: "已导出码批次",
            children: (
              <Table
                columns={batchColumns}
                dataSource={batches}
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
            ),
          },
          {
            key: "audit",
            label: "导出审计",
            children: (
              <>
                <div className="mb-4 text-gray-400 text-sm">
                  注：导出审计功能待后端 API 实现，当前为模拟数据
                </div>
                {MOCK_AUDIT_DATA.length > 0 ? (
                  <Table
                    columns={auditColumns}
                    dataSource={MOCK_AUDIT_DATA}
                    rowKey="id"
                    pagination={false}
                    size="small"
                  />
                ) : (
                  <Empty description="暂无审计日志数据" />
                )}
              </>
            ),
          },
        ]}
      />
    </div>
  );
}

function dayjsFormat(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleString("zh-CN");
  } catch {
    return dateStr;
  }
}
