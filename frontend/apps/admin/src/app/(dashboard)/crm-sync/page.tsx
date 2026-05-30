"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Descriptions,
  Empty,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import {
  CloudSyncOutlined,
  ReloadOutlined,
  SyncOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title } = Typography;

interface SyncMapping {
  id: string;
  consumer_id: string;
  external_id: string;
  source_system: string;
  sync_direction: string;
  last_synced_at: string | null;
  status: string;
}

interface SyncLog {
  id: string;
  sync_type: string;
  external_id: string;
  data_summary: string;
  status: string;
  error_message: string | null;
  created_at: string;
}

const DIRECTION_MAP: Record<string, { label: string; color: string }> = {
  outbound: { label: "推送到 CRM", color: "blue" },
  inbound: { label: "从 CRM 拉取", color: "green" },
  bidirectional: { label: "双向同步", color: "purple" },
};

const SYNC_STATUS_MAP: Record<string, { label: string; color: string }> = {
  success: { label: "成功", color: "green" },
  failed: { label: "失败", color: "red" },
  pending: { label: "待处理", color: "default" },
  processing: { label: "处理中", color: "blue" },
};

function formatDate(dateStr: string | null): string {
  if (!dateStr) return "-";
  try {
    return new Date(dateStr).toLocaleString("zh-CN");
  } catch {
    return dateStr;
  }
}

function maskId(id: string): string {
  if (!id || id.length <= 8) return id;
  return id.slice(0, 4) + "****" + id.slice(-4);
}

export default function CrmSyncPage() {
  const { message } = App.useApp();
  const [mappings, setMappings] = useState<SyncMapping[]>([]);
  const [syncLogs, setSyncLogs] = useState<SyncLog[]>([]);
  const [mappingLoading, setMappingLoading] = useState(false);
  const [logLoading, setLogLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [crmConfigured, setCrmConfigured] = useState(false);
  const [activeTab, setActiveTab] = useState("mappings");

  const checkCrmConfig = useCallback(async () => {
    try {
      const { data } = await api.get("/connectors/connectors", {
        params: { connector_type: "wecom_crm" },
      });
      const items = Array.isArray(data) ? data : data.items || [];
      setCrmConfigured(items.length > 0);
    } catch {
      setCrmConfigured(false);
    }
  }, []);

  const fetchMappings = useCallback(async () => {
    setMappingLoading(true);
    try {
      const { data } = await api.get("/crm/sync-mappings");
      setMappings(Array.isArray(data) ? data : data.items || []);
    } catch {
      // API 尚未就绪时显示空列表
      setMappings([]);
    } finally {
      setMappingLoading(false);
    }
  }, []);

  const fetchSyncLogs = useCallback(async () => {
    setLogLoading(true);
    try {
      const { data } = await api.get("/crm/sync-logs");
      setSyncLogs(Array.isArray(data) ? data : data.items || []);
    } catch {
      // API 尚未就绪时显示空列表
      setSyncLogs([]);
    } finally {
      setLogLoading(false);
    }
  }, []);

  useEffect(() => {
    checkCrmConfig();
    fetchMappings();
  }, [checkCrmConfig, fetchMappings]);

  const handleTabChange = (key: string) => {
    setActiveTab(key);
    if (key === "logs") {
      fetchSyncLogs();
    }
  };

  const handleManualSync = async () => {
    setSyncing(true);
    try {
      // 触发手动同步（后续对接 arq 任务）
      await api.post("/crm/trigger-sync");
      message.success("手动同步已触发，将在下一个周期执行");
    } catch {
      // 接口未就绪时仅给出提示
      message.info("同步功能将在下一个 cron 周期自动执行");
    } finally {
      setSyncing(false);
    }
  };

  const mappingColumns: ColumnsType<SyncMapping> = [
    {
      title: "消费者 ID",
      dataIndex: "consumer_id",
      key: "consumer_id",
      render: (v: string) => maskId(v),
    },
    {
      title: "外部 ID",
      dataIndex: "external_id",
      key: "external_id",
      render: (v: string) => v || "-",
    },
    {
      title: "来源系统",
      dataIndex: "source_system",
      key: "source_system",
      render: (v: string) => <Tag>{v || "-"}</Tag>,
    },
    {
      title: "同步方向",
      dataIndex: "sync_direction",
      key: "sync_direction",
      render: (v: string) => {
        const info = DIRECTION_MAP[v] || { label: v, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "最近同步",
      dataIndex: "last_synced_at",
      key: "last_synced_at",
      render: (v: string | null) => formatDate(v),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (v: string) => {
        const info = SYNC_STATUS_MAP[v] || { label: v, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
  ];

  const logColumns: ColumnsType<SyncLog> = [
    {
      title: "同步类型",
      dataIndex: "sync_type",
      key: "sync_type",
      render: (v: string) => <Tag color="blue">{v}</Tag>,
    },
    {
      title: "外部 ID",
      dataIndex: "external_id",
      key: "external_id",
      render: (v: string) => v || "-",
    },
    {
      title: "数据摘要",
      dataIndex: "data_summary",
      key: "data_summary",
      ellipsis: true,
      render: (v: string) => v || "-",
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (v: string) => {
        const info = SYNC_STATUS_MAP[v] || { label: v, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "错误信息",
      dataIndex: "error_message",
      key: "error_message",
      ellipsis: true,
      render: (v: string | null) =>
        v ? (
          <Typography.Text type="danger" ellipsis title={v}>
            {v}
          </Typography.Text>
        ) : (
          "-"
        ),
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      render: (v: string) => formatDate(v),
    },
  ];

  return (
    <div>
      <Title level={4}>CRM 同步管理</Title>

      {!crmConfigured && (
        <Card style={{ marginBottom: 24 }}>
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <span>
                尚未配置 CRM 连接，请先前往{" "}
                <a href="/settings/crm">CRM 集成配置</a> 完成设置
              </span>
            }
          />
        </Card>
      )}

      <Card style={{ marginBottom: 24 }}>
        <Descriptions column={2} bordered size="small">
          <Descriptions.Item label="CRM 连接状态">
            {crmConfigured ? (
              <Tag color="green">已配置</Tag>
            ) : (
              <Tag color="default">未配置</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="同步映射数">
            {mappings.length}
          </Descriptions.Item>
        </Descriptions>
        <div style={{ marginTop: 12 }}>
          <Space>
            <Button
              icon={<SyncOutlined />}
              onClick={handleManualSync}
              loading={syncing}
              disabled={!crmConfigured}
            >
              手动触发同步
            </Button>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => {
                fetchMappings();
                if (activeTab === "logs") fetchSyncLogs();
              }}
            >
              刷新
            </Button>
          </Space>
        </div>
      </Card>

      <Card>
        <Tabs
          activeKey={activeTab}
          onChange={handleTabChange}
          items={[
            {
              key: "mappings",
              label: "同步映射",
              children: (
                <Table
                  columns={mappingColumns}
                  dataSource={mappings}
                  rowKey="id"
                  loading={mappingLoading}
                  pagination={{
                    pageSize: 20,
                    showTotal: (t) => `共 ${t} 条`,
                  }}
                  locale={{ emptyText: "暂无同步映射记录" }}
                />
              ),
            },
            {
              key: "logs",
              label: "同步日志",
              children: (
                <Table
                  columns={logColumns}
                  dataSource={syncLogs}
                  rowKey="id"
                  loading={logLoading}
                  pagination={{
                    pageSize: 20,
                    showTotal: (t) => `共 ${t} 条`,
                  }}
                  locale={{ emptyText: "暂无同步日志" }}
                />
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
}
