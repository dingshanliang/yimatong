"use client";

import { useCallback, useEffect, useState } from "react";
import { App, Button, Card, Descriptions, Empty, Space, Tabs, Tag, Typography } from "antd";
import { CloudSyncOutlined, ReloadOutlined, SyncOutlined } from "@ant-design/icons";
import api from "@/lib/api";
import { MappingsTab } from "./_components/MappingsTab";
import { LogsTab } from "./_components/LogsTab";
import type { SyncMapping, SyncLog } from "./_components/types";

const { Title } = Typography;

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
      const { data } = await api.get("/connectors/connectors", { params: { connector_type: "wecom_crm" } });
      const items = Array.isArray(data) ? data : data.items || [];
      setCrmConfigured(items.length > 0);
    } catch { setCrmConfigured(false); }
  }, []);

  const fetchMappings = useCallback(async () => {
    setMappingLoading(true);
    try { const { data } = await api.get("/crm/sync-mappings"); setMappings(Array.isArray(data) ? data : data.items || []); }
    catch { setMappings([]); }
    finally { setMappingLoading(false); }
  }, []);

  const fetchSyncLogs = useCallback(async () => {
    setLogLoading(true);
    try { const { data } = await api.get("/crm/sync-logs"); setSyncLogs(Array.isArray(data) ? data : data.items || []); }
    catch { setSyncLogs([]); }
    finally { setLogLoading(false); }
  }, []);

  useEffect(() => { checkCrmConfig(); fetchMappings(); }, [checkCrmConfig, fetchMappings]);

  const handleManualSync = async () => {
    setSyncing(true);
    try { await api.post("/crm/trigger-sync"); message.success("手动同步已触发，将在下一个周期执行"); }
    catch { message.info("同步功能将在下一个 cron 周期自动执行"); }
    finally { setSyncing(false); }
  };

  return (
    <div>
      <Title level={4}>CRM 同步管理</Title>
      {!crmConfigured && (
        <Card style={{ marginBottom: 24 }}>
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<span>尚未配置 CRM 连接，请先前往 <a href="/settings/crm">CRM 集成配置</a> 完成设置</span>} />
        </Card>
      )}
      <Card style={{ marginBottom: 24 }}>
        <Descriptions column={2} bordered size="small">
          <Descriptions.Item label="CRM 连接状态">{crmConfigured ? <Tag color="green">已配置</Tag> : <Tag color="default">未配置</Tag>}</Descriptions.Item>
          <Descriptions.Item label="同步映射数">{mappings.length}</Descriptions.Item>
        </Descriptions>
        <div style={{ marginTop: 12 }}>
          <Space>
            <Button icon={<SyncOutlined />} onClick={handleManualSync} loading={syncing} disabled={!crmConfigured}>手动触发同步</Button>
            <Button icon={<ReloadOutlined />} onClick={() => { fetchMappings(); if (activeTab === "logs") fetchSyncLogs(); }}>刷新</Button>
          </Space>
        </div>
      </Card>
      <Card>
        <Tabs activeKey={activeTab} onChange={(key) => { setActiveTab(key); if (key === "logs") fetchSyncLogs(); }} items={[
          { key: "mappings", label: "同步映射", children: <MappingsTab mappings={mappings} loading={mappingLoading} /> },
          { key: "logs", label: "同步日志", children: <LogsTab logs={syncLogs} loading={logLoading} /> },
        ]} />
      </Card>
    </div>
  );
}
