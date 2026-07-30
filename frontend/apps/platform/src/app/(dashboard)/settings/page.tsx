"use client";

import { useEffect, useState } from "react";
import { App, Button, Card, Switch, Tabs, InputNumber, Typography } from "antd";
import { SaveOutlined } from "@ant-design/icons";
import useSWR, { mutate } from "swr";
import api from "@/lib/api";
import { extractErrorMessage } from "@/lib/api";

const { Title, Text } = Typography;

interface PlatformConfig {
  feature_flags: Record<string, boolean>;
  notification_settings: Record<string, boolean>;
  compliance_defaults: Record<string, number>;
}

const FEATURE_FLAGS = [
  {
    key: "ai_assistant",
    label: "AI 助手",
    desc: "允许租户使用 AI 写作和数据分析功能",
  },
  { key: "risk_module", label: "风控模块", desc: "启用扫码风控和异常检测" },
  { key: "channel_portal", label: "渠道门户", desc: "启用经销商/门店渠道管理" },
];

const NOTIFICATION_SETTINGS = [
  {
    key: "email_enabled",
    label: "邮件通知",
    desc: "系统通知和告警通过邮件发送",
  },
  {
    key: "webhook_enabled",
    label: "Webhook 推送",
    desc: "允许租户配置 Webhook 事件推送",
  },
];

export default function SettingsPage() {
  const { data, isLoading } = useSWR<PlatformConfig>("/platform/config");
  const { message } = App.useApp();
  const [saving, setSaving] = useState(false);

  const [features, setFeatures] = useState<Record<string, boolean>>({});
  const [notifications, setNotifications] = useState<Record<string, boolean>>(
    {}
  );
  const [retentionDays, setRetentionDays] = useState<number>(365);

  useEffect(() => {
    if (data) {
      setFeatures(data.feature_flags || {});
      setNotifications(data.notification_settings || {});
      setRetentionDays(data.compliance_defaults?.data_retention_days ?? 365);
    }
  }, [data]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.patch("/platform/config", {
        feature_flags: features,
        notification_settings: notifications,
        compliance_defaults: { data_retention_days: retentionDays },
      });
      mutate("/platform/config");
      message.success("配置已保存");
    } catch (err) {
      message.error(extractErrorMessage(err, "保存失败"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 16,
        }}
      >
        <Title level={4} style={{ margin: 0 }}>
          系统配置
        </Title>
        <Button
          type="primary"
          icon={<SaveOutlined />}
          loading={saving}
          onClick={handleSave}
        >
          保存配置
        </Button>
      </div>

      <Card loading={isLoading}>
        <Tabs
          items={[
            {
              key: "features",
              label: "功能开关",
              children: (
                <div style={{ maxWidth: 600 }}>
                  {FEATURE_FLAGS.map(({ key, label, desc }) => (
                    <div
                      key={key}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        padding: "12px 0",
                        borderBottom: "1px solid var(--ymt-color-border-base)",
                      }}
                    >
                      <div>
                        <Text strong>{label}</Text>
                        <br />
                        <Text
                          type="secondary"
                          style={{ fontSize: "var(--ymt-font-size-xs)" }}
                        >
                          {desc}
                        </Text>
                      </div>
                      <Switch
                        checked={features[key] ?? true}
                        onChange={(v) => setFeatures({ ...features, [key]: v })}
                      />
                    </div>
                  ))}
                </div>
              ),
            },
            {
              key: "notifications",
              label: "通知设置",
              children: (
                <div style={{ maxWidth: 600 }}>
                  {NOTIFICATION_SETTINGS.map(({ key, label, desc }) => (
                    <div
                      key={key}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        padding: "12px 0",
                        borderBottom: "1px solid var(--ymt-color-border-base)",
                      }}
                    >
                      <div>
                        <Text strong>{label}</Text>
                        <br />
                        <Text
                          type="secondary"
                          style={{ fontSize: "var(--ymt-font-size-xs)" }}
                        >
                          {desc}
                        </Text>
                      </div>
                      <Switch
                        checked={notifications[key] ?? true}
                        onChange={(v) =>
                          setNotifications({ ...notifications, [key]: v })
                        }
                      />
                    </div>
                  ))}
                </div>
              ),
            },
            {
              key: "compliance",
              label: "合规设置",
              children: (
                <div style={{ maxWidth: 600 }}>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      padding: "12px 0",
                    }}
                  >
                    <div>
                      <Text strong>数据保留天数</Text>
                      <br />
                      <Text
                        type="secondary"
                        style={{ fontSize: "var(--ymt-font-size-xs)" }}
                      >
                        扫码事件和审计日志的保留期限
                      </Text>
                    </div>
                    <InputNumber
                      min={30}
                      max={3650}
                      value={retentionDays}
                      onChange={(v) => setRetentionDays(v ?? 365)}
                      style={{ width: 120 }}
                    />
                  </div>
                </div>
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
}
