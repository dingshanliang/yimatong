"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import {
  ApiOutlined,
  ExperimentOutlined,
  SaveOutlined,
} from "@ant-design/icons";
import api, { extractErrorMessage } from "@/lib/api";

const { Title, Text } = Typography;

interface ConnectorConfig {
  id?: string;
  name: string;
  connector_type: string;
  config: Record<string, unknown>;
  enabled: boolean;
  created_at?: string;
  updated_at?: string;
}

interface ConnectionState {
  configured: boolean;
  connected: boolean | null;
  lastSyncAt: string | null;
  testing: boolean;
}

export default function CrmSettingsPage() {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [loadingConfig, setLoadingConfig] = useState(false);
  const [existingConnector, setExistingConnector] =
    useState<ConnectorConfig | null>(null);
  const [connectionState, setConnectionState] = useState<ConnectionState>({
    configured: false,
    connected: null,
    lastSyncAt: null,
    testing: false,
  });

  const loadExistingConfig = useCallback(async () => {
    setLoadingConfig(true);
    try {
      const { data } = await api.get("/connectors/connectors", {
        params: { connector_type: "wecom_crm" },
      });
      const items = Array.isArray(data) ? data : data.items || [];
      if (items.length > 0) {
        const connector = items[0] as ConnectorConfig;
        setExistingConnector(connector);
        form.setFieldsValue({
          name: connector.name,
          corpid: (connector.config?.corpid as string) || "",
          secret: "", // 密钥不从后端返回，保持空
        });
        setConnectionState((prev) => ({
          ...prev,
          configured: true,
          lastSyncAt: connector.updated_at || null,
        }));
      }
    } catch {
      // 尚未配置，保持空表单
    } finally {
      setLoadingConfig(false);
    }
  }, [form]);

  useEffect(() => {
    loadExistingConfig();
  }, [loadExistingConfig]);

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);

      if (existingConnector?.id) {
        await api.patch(`/connectors/connectors/${existingConnector.id}`, {
          name: values.name,
          config: { corpid: values.corpid },
          secrets: values.secret ? { secret: values.secret } : undefined,
        });
      } else {
        await api.post("/connectors/connectors", {
          name: values.name || "企业微信 CRM",
          connector_type: "wecom_crm",
          config: { corpid: values.corpid },
          secrets: { secret: values.secret },
        });
      }

      message.success("配置保存成功");
      loadExistingConfig();
    } catch (err) {
      const axiosErr = err as Parameters<typeof extractErrorMessage>[0];
      if (axiosErr && typeof axiosErr === "object" && "response" in axiosErr) {
        message.error(extractErrorMessage(axiosErr, "保存失败"));
      }
    } finally {
      setSaving(false);
    }
  };

  const handleTestConnection = async () => {
    if (!existingConnector?.id) {
      message.warning("请先保存配置后再测试连接");
      return;
    }

    setConnectionState((prev) => ({ ...prev, testing: true }));
    try {
      const { data } = await api.post(
        `/connectors/connectors/${existingConnector.id}/test`
      );
      if (data.success) {
        setConnectionState((prev) => ({ ...prev, connected: true }));
        message.success("连接测试成功");
      } else {
        setConnectionState((prev) => ({ ...prev, connected: false }));
        message.warning(`连接测试: ${data.message || "未知错误"}`);
      }
    } catch (err) {
      setConnectionState((prev) => ({ ...prev, connected: false }));
      message.error(extractErrorMessage(err, "连接测试失败"));
    } finally {
      setConnectionState((prev) => ({ ...prev, testing: false }));
    }
  };

  return (
    <div>
      <Title level={4}>CRM 集成配置 - 企业微信</Title>

      <Spin spinning={loadingConfig}>
        <Card title="基本配置" style={{ marginBottom: 24 }}>
          <Form
            form={form}
            layout="vertical"
            initialValues={{ name: "企业微信 CRM" }}
          >
            <Form.Item
              name="name"
              label="连接器名称"
              rules={[{ required: true, message: "请输入连接器名称" }]}
            >
              <Input placeholder="如：企业微信 CRM" />
            </Form.Item>

            <Form.Item
              name="corpid"
              label="企业 ID (corpid)"
              rules={[{ required: true, message: "请输入企业 ID" }]}
            >
              <Input placeholder="ww1234567890abcdef" />
            </Form.Item>

            <Form.Item
              name="secret"
              label="应用密钥 (secret)"
              rules={
                existingConnector
                  ? []
                  : [{ required: true, message: "请输入应用密钥" }]
              }
              extra={existingConnector ? "留空则保持原有密钥不变" : undefined}
            >
              <Input.Password placeholder="请输入应用密钥" />
            </Form.Item>

            <Form.Item>
              <Space>
                <Button
                  type="primary"
                  icon={<SaveOutlined />}
                  onClick={handleSave}
                  loading={saving}
                >
                  保存配置
                </Button>
              </Space>
            </Form.Item>
          </Form>
        </Card>
      </Spin>

      <Card title="连接状态">
        <Descriptions column={1} bordered>
          <Descriptions.Item label="配置状态">
            {connectionState.configured ? (
              <Tag color="#16a34a">已配置</Tag>
            ) : (
              <Tag color="#8c8c8c">未配置</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="连接状态">
            {connectionState.connected === null ? (
              <Text type="secondary">未测试</Text>
            ) : connectionState.connected ? (
              <Tag color="#16a34a">已连接</Tag>
            ) : (
              <Tag color="#b91c1c">连接失败</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="最近同步时间">
            {connectionState.lastSyncAt
              ? new Date(connectionState.lastSyncAt).toLocaleString("zh-CN")
              : "暂无同步记录"}
          </Descriptions.Item>
        </Descriptions>

        <div style={{ marginTop: 16 }}>
          <Button
            icon={<ExperimentOutlined />}
            onClick={handleTestConnection}
            loading={connectionState.testing}
            disabled={!connectionState.configured}
          >
            测试连接
          </Button>
        </div>
      </Card>
    </div>
  );
}
