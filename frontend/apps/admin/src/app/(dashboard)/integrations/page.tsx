"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  Space,
  Steps,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { WebhooksTab } from "./_components/WebhooksTab";
import { ApiKeysTab } from "./_components/ApiKeysTab";
import { DeliveriesTab } from "./_components/DeliveriesTab";
import api from "@/lib/api";

const { Paragraph, Text, Title } = Typography;

interface WeComStatus {
  connected: boolean;
  status: string;
  callback_url?: string | null;
  config: {
    corp_id?: string;
    customer_service_user_ids?: string[];
    last_verified_at?: string;
    last_event_at?: string;
    last_error?: string;
    mock_mode?: boolean;
  };
  secrets: {
    secret?: string;
    callback_token?: string;
    encoding_aes_key?: string;
  };
}

interface WeComFormValues {
  corp_id: string;
  secret?: string;
  customer_service_user_ids?: string;
}

function getWeComStage(status: WeComStatus | null) {
  if (!status?.callback_url) {
    return {
      current: 0,
      tag: <Tag>未保存配置</Tag>,
      alertType: "info" as const,
      title: "先保存企业微信资料",
      description:
        "保存后系统会生成可复制到企业微信后台的 URL、Token 和 EncodingAESKey。",
    };
  }
  if (status.connected) {
    return {
      current: 3,
      tag: <Tag color="#16a34a">已连接</Tag>,
      alertType: "success" as const,
      title: "企业微信已连接",
      description:
        "现在可以在活动中启用添加企业微信入口，或设置为添加后再领取权益。",
    };
  }
  if (status.config?.last_error || status.status === "error") {
    return {
      current: 2,
      tag: <Tag color="#b91c1c">连接失败</Tag>,
      alertType: "warning" as const,
      title: "连接未通过",
      description:
        status.config?.last_error || "请检查企业微信后台填写内容后重新检测。",
    };
  }
  return {
    current: 1,
    tag: <Tag color="#1d4ed8">待检测</Tag>,
    alertType: "info" as const,
    title: "请完成企业微信后台填写",
    description:
      "把右侧三项内容复制到企业微信后台对应位置，然后回到这里检测连接。",
  };
}

function formatDateTime(value?: string) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function WeComTab() {
  const { message } = App.useApp();
  const router = useRouter();
  const [form] = Form.useForm<WeComFormValues>();
  const [status, setStatus] = useState<WeComStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const stage = getWeComStage(status);

  const loadStatus = useCallback(async () => {
    const { data } = await api.get<WeComStatus>("/integrations/wecom");
    setStatus(data);
    form.setFieldsValue({
      corp_id: data.config?.corp_id || "",
      customer_service_user_ids:
        data.config?.customer_service_user_ids?.join(",") || "",
    });
  }, [form]);

  useEffect(() => {
    loadStatus().catch(() => setStatus(null));
  }, [loadStatus]);

  const copyText = async (text?: string | null) => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      message.success("已复制");
    } catch {
      message.warning("浏览器未允许复制，请手动选择内容复制");
    }
  };

  const saveConfig = async (values: WeComFormValues) => {
    setLoading(true);
    try {
      const { data } = await api.post<WeComStatus>("/integrations/wecom", {
        corp_id: values.corp_id,
        secret: values.secret || undefined,
        customer_service_user_ids: (values.customer_service_user_ids || "")
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      });
      setStatus(data);
      message.success("企业微信资料已保存，请继续复制右侧内容");
    } catch {
      message.error("保存企业微信配置失败");
    } finally {
      setLoading(false);
    }
  };

  const verify = async () => {
    setLoading(true);
    try {
      const { data } = await api.post<WeComStatus>(
        "/integrations/wecom/verify"
      );
      setStatus(data);
      message.success("企业微信连接正常");
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "企业微信连接检测失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Space orientation="vertical" size="large" className="w-full">
      <Card>
        <Space orientation="vertical" size="middle" className="w-full">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <Title level={5} className="!mb-1">
                企业微信客户联系
              </Title>
              <Paragraph type="secondary" className="!mb-0">
                平台提供接收地址，你只需要在企业微信后台复制填写；连接后即可在活动中启用企业微信转化。
              </Paragraph>
            </div>
            {stage.tag}
          </div>
          <Alert
            type={stage.alertType}
            showIcon
            title={stage.title}
            description={stage.description}
          />
          <Steps
            size="small"
            current={stage.current}
            items={[
              { title: "填写资料", content: "保存企业微信信息" },
              { title: "复制到企业微信", content: "按字段复制填写" },
              { title: "检测连接", content: "确认添加记录可识别" },
              { title: "启用活动", content: "配置企业微信转化" },
            ]}
          />
        </Space>
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_460px]">
        <Card
          title="第 1 步：填写企业微信资料"
          extra={
            status?.config?.last_verified_at ? (
              <Tag color="#16a34a">最近检测通过</Tag>
            ) : undefined
          }
        >
          <Form form={form} layout="vertical" onFinish={saveConfig}>
            <Form.Item
              name="corp_id"
              label="企业 ID"
              extra="在企业微信后台「我的企业 > 企业信息」中查看。"
              rules={[{ required: true, message: "请输入企业 ID" }]}
            >
              <Input placeholder="例如 wwxxxxxxxxxxxxxx" />
            </Form.Item>
            <Form.Item
              name="secret"
              label="客户联系 Secret"
              extra={
                status?.secrets?.secret
                  ? `当前已保存：${status.secrets.secret}`
                  : "在企业微信后台「客户联系」相关设置中查看。"
              }
            >
              <Input.Password placeholder="不修改可留空" />
            </Form.Item>
            <Form.Item
              name="customer_service_user_ids"
              label="接待成员（可选）"
              extra="消费者添加后由这些成员接待；不填则使用企业微信默认接待成员。多个成员账号用英文逗号分隔。"
            >
              <Input placeholder="例如 zhangsan,lisi" />
            </Form.Item>
            <Space wrap>
              <Button type="primary" htmlType="submit" loading={loading}>
                保存资料
              </Button>
              <Button
                onClick={verify}
                loading={loading}
                disabled={!status?.callback_url}
              >
                检测连接
              </Button>
            </Space>
            {!status?.callback_url && (
              <Text type="secondary" className="mt-2 block">
                保存资料后即可复制到企业微信后台。
              </Text>
            )}
          </Form>
        </Card>

        <Card title="第 2-4 步：复制、检测、启用">
          <Space orientation="vertical" size="middle" className="w-full">
            <Alert
              type={status?.connected ? "success" : "info"}
              showIcon
              title={status?.connected ? "企业微信已连接" : "无需自建服务器"}
              description={
                status?.connected
                  ? "连接通过后，消费者添加企业微信的记录会自动用于活动发券判断。"
                  : "把下面三项复制到企业微信后台同名字段中，平台会自动处理消费者添加记录。"
              }
            />
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="URL">
                <Space orientation="vertical" size={4} className="w-full">
                  <Text copyable={false} className="break-all">
                    {status?.callback_url || "保存资料后生成"}
                  </Text>
                  <Button
                    size="small"
                    disabled={!status?.callback_url}
                    onClick={() => copyText(status?.callback_url)}
                  >
                    复制 URL
                  </Button>
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="Token">
                <Space orientation="vertical" size={4} className="w-full">
                  <Text className="break-all">
                    {status?.secrets?.callback_token || "保存资料后生成"}
                  </Text>
                  <Button
                    size="small"
                    disabled={!status?.secrets?.callback_token}
                    onClick={() => copyText(status?.secrets?.callback_token)}
                  >
                    复制 Token
                  </Button>
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="EncodingAESKey">
                <Space orientation="vertical" size={4} className="w-full">
                  <Text className="break-all">
                    {status?.secrets?.encoding_aes_key || "保存资料后生成"}
                  </Text>
                  <Button
                    size="small"
                    disabled={!status?.secrets?.encoding_aes_key}
                    onClick={() => copyText(status?.secrets?.encoding_aes_key)}
                  >
                    复制 EncodingAESKey
                  </Button>
                </Space>
              </Descriptions.Item>
            </Descriptions>
            {status?.config?.last_error && (
              <Alert
                type="warning"
                showIcon
                title="最近一次检测未通过"
                description={status.config.last_error}
              />
            )}
            <Space wrap>
              {stage.tag}
              {status?.config?.last_verified_at && (
                <Text type="secondary">
                  最近检测：{formatDateTime(status.config.last_verified_at)}
                </Text>
              )}
              {status?.config?.last_event_at && (
                <Text type="secondary">
                  最近添加记录：{formatDateTime(status.config.last_event_at)}
                </Text>
              )}
            </Space>
            <Space wrap>
              <Button
                onClick={verify}
                loading={loading}
                disabled={!status?.callback_url}
              >
                检测连接
              </Button>
              <Button
                type="primary"
                disabled={!status?.connected}
                onClick={() => router.push("/campaigns")}
              >
                去活动中启用
              </Button>
            </Space>
          </Space>
        </Card>
      </div>
    </Space>
  );
}

const tabItems = [
  { key: "wecom", label: "企业微信", children: <WeComTab /> },
  { key: "webhooks", label: "消息推送", children: <WebhooksTab /> },
  { key: "api-keys", label: "API 密钥", children: <ApiKeysTab /> },
  { key: "deliveries", label: "推送记录", children: <DeliveriesTab /> },
];

export default function IntegrationsPage() {
  return (
    <div>
      <Title level={4} className="!mb-4">
        集成管理
      </Title>
      <Tabs defaultActiveKey="wecom" items={tabItems} />
    </div>
  );
}
