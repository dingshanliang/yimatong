"use client";

import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Checkbox,
  Form,
  Input,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import api, { extractErrorMessage } from "@/lib/api";

const { Title, Text } = Typography;
const SCOPES = [
  { label: "商品资料", value: "products" },
  { label: "扫码页面", value: "pages" },
  { label: "营销活动", value: "campaigns" },
  { label: "码批次", value: "codes" },
  { label: "经营分析", value: "analytics" },
];

interface AuthorizationItem {
  id: string;
  agency_name?: string;
  agency_tenant_id: string;
  scope: string[];
  status: string;
  granted_at?: string;
}

export default function AgencyAuthorizationsPage() {
  const { message } = App.useApp();
  const [items, setItems] = useState<AuthorizationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<{ agency_slug: string; scope: string[] }>();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get<{ items: AuthorizationItem[] }>(
        "/ops/authorizations"
      );
      setItems(data.items);
    } catch (error) {
      message.error(extractErrorMessage(error, "加载代运营授权失败"));
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void load();
  }, [load]);

  const submit = async (values: { agency_slug: string; scope: string[] }) => {
    setSaving(true);
    try {
      await api.post("/ops/authorizations", values);
      message.success("授权已生效");
      setOpen(false);
      form.resetFields();
      await load();
    } catch (error) {
      message.error(extractErrorMessage(error, "授权失败"));
    } finally {
      setSaving(false);
    }
  };

  const revoke = (item: AuthorizationItem) => {
    Modal.confirm({
      title: `撤销 ${item.agency_name || "该服务商"} 的授权？`,
      content: "撤销后，服务商现有客户上下文会立即失效，未保存操作将无法继续。",
      okText: "确认撤销",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        await api.delete(`/ops/authorizations/${item.id}`);
        message.success("授权已撤销");
        await load();
      },
    });
  };

  return (
    <Space orientation="vertical" size={16} className="w-full">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Title level={4} className="!mb-1">
            代运营授权
          </Title>
          <Text type="secondary">
            按服务商工作区标识授权，并明确可操作的业务范围。
          </Text>
        </div>
        <Button
          type="primary"
          onClick={() => {
            form.setFieldsValue({ scope: SCOPES.map((s) => s.value) });
            setOpen(true);
          }}
        >
          新增授权
        </Button>
      </div>
      <Card variant="borderless">
        <Table
          rowKey="id"
          loading={loading}
          dataSource={items}
          pagination={false}
          columns={[
            {
              title: "代运营服务商",
              render: (_, item) => item.agency_name || "服务商名称不可用",
            },
            {
              title: "授权范围",
              render: (_, item) => (
                <Space wrap>
                  {item.scope.map((scope) => (
                    <Tag key={scope}>
                      {SCOPES.find((s) => s.value === scope)?.label || scope}
                    </Tag>
                  ))}
                </Space>
              ),
            },
            { title: "状态", render: () => <Tag color="success">已生效</Tag> },
            {
              title: "操作",
              width: 100,
              render: (_, item) => (
                <Button danger type="link" onClick={() => revoke(item)}>
                  撤销
                </Button>
              ),
            },
          ]}
        />
      </Card>
      <Modal
        title="授权代运营服务商"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={saving}
        okText="授权后立即生效"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" onFinish={submit}>
          <Form.Item
            name="agency_slug"
            label="服务商工作区标识"
            extra="请向代运营服务商确认其工作区标识。"
            rules={[{ required: true, message: "请输入服务商工作区标识" }]}
          >
            <Input placeholder="例如：acme-agency" />
          </Form.Item>
          <Form.Item
            name="scope"
            label="允许操作的范围"
            rules={[{ required: true, message: "请至少选择一项" }]}
          >
            <Checkbox.Group options={SCOPES} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
