"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Space,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { ReloadOutlined, SyncOutlined } from "@ant-design/icons";
import api from "@/lib/api";
import { STATUS_COLORS } from "@/lib/status-colors";
import { MappingsTab } from "./_components/MappingsTab";
import { LogsTab } from "./_components/LogsTab";
import type { SyncMapping, SyncLog } from "./_components/types";

const { Title } = Typography;

const CRM_SYNC_UNAVAILABLE = "CRM 同步功能尚未接入";

export default function CrmSyncPage() {
  const [mappings, setMappings] = useState<SyncMapping[]>([]);
  const [syncLogs, setSyncLogs] = useState<SyncLog[]>([]);
  const [mappingLoading, setMappingLoading] = useState(false);
  const [logLoading, setLogLoading] = useState(false);
  const [mappingsUnavailable, setMappingsUnavailable] = useState(false);
  const [logsUnavailable, setLogsUnavailable] = useState(false);
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
      setMappingsUnavailable(false);
    } catch {
      // 不再静默置空：明确告知该数据源暂不可用
      setMappings([]);
      setMappingsUnavailable(true);
    } finally {
      setMappingLoading(false);
    }
  }, []);

  const fetchSyncLogs = useCallback(async () => {
    setLogLoading(true);
    try {
      const { data } = await api.get("/crm/sync-logs");
      setSyncLogs(Array.isArray(data) ? data : data.items || []);
      setLogsUnavailable(false);
    } catch {
      setSyncLogs([]);
      setLogsUnavailable(true);
    } finally {
      setLogLoading(false);
    }
  }, []);

  useEffect(() => {
    checkCrmConfig();
    fetchMappings();
  }, [checkCrmConfig, fetchMappings]);

  return (
    <div>
      <Title level={4}>CRM 同步管理</Title>
      <Alert
        type="warning"
        showIcon
        title={CRM_SYNC_UNAVAILABLE}
        description="后端 CRM 同步服务尚未上线，本页面为预留入口：同步映射、同步日志与手动触发同步暂不可用。连接器配置本身可在 CRM 集成配置中维护。"
        style={{ marginBottom: 24 }}
      />
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
              <Tag color={STATUS_COLORS.success}>已配置</Tag>
            ) : (
              <Tag color={STATUS_COLORS.neutral}>未配置</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="同步映射数">
            {mappingsUnavailable ? (
              <Tag color={STATUS_COLORS.neutral}>不可用</Tag>
            ) : (
              mappings.length
            )}
          </Descriptions.Item>
        </Descriptions>
        <div style={{ marginTop: 12 }}>
          <Space>
            <Tooltip title={`${CRM_SYNC_UNAVAILABLE}，暂不支持手动触发同步`}>
              <Button icon={<SyncOutlined />} disabled>
                手动触发同步
              </Button>
            </Tooltip>
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
          onChange={(key) => {
            setActiveTab(key);
            if (key === "logs") fetchSyncLogs();
          }}
          items={[
            {
              key: "mappings",
              label: "同步映射",
              children: mappingsUnavailable ? (
                <Alert
                  type="warning"
                  showIcon
                  title="同步映射暂不可用"
                  description="CRM 同步接口尚未接入，无法读取同步映射数据。"
                />
              ) : (
                <MappingsTab mappings={mappings} loading={mappingLoading} />
              ),
            },
            {
              key: "logs",
              label: "同步日志",
              children: logsUnavailable ? (
                <Alert
                  type="warning"
                  showIcon
                  title="同步日志暂不可用"
                  description="CRM 同步接口尚未接入，无法读取同步日志数据。"
                />
              ) : (
                <LogsTab logs={syncLogs} loading={logLoading} />
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
}
