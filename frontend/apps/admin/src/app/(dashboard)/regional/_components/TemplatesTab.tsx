"use client";

import { useEffect, useState } from "react";
import api from "@/lib/api";
import { Alert, Button, Form, Input, Modal, Table, message } from "antd";
import { PlusOutlined, SendOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";

type Template = Record<string, unknown> & {
  id: string;
  name: string;
  config: { publish_history?: Array<{ published_at: string; member_count: number; delivered_count: number }> };
};

const columns: ColumnsType<Template> = [
  { title: "模板名称", dataIndex: "name", key: "name" },
  {
    title: "下发次数", key: "publish_count",
    render: (_: unknown, record: Template) => record.config?.publish_history?.length || 0,
  },
];

export function TemplatesTab({ orgId }: { orgId: string }) {
  const [items, setItems] = useState<Template[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const fetch = async () => {
    if (!orgId) return;
    setLoading(true);
    try {
      const { data } = await api.get(`/regional/orgs/${orgId}/templates`);
      setItems(Array.isArray(data) ? data : []);
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, [orgId]);

  const handleCreate = async (values: { name: string; config: string }) => {
    try {
      const config = values.config ? JSON.parse(values.config) : {};
      await api.post(`/regional/orgs/${orgId}/templates`, { name: values.name, config });
      message.success("模板创建成功");
      setOpen(false);
      form.resetFields();
      fetch();
    } catch {
      message.error("创建失败");
    }
  };

  const handlePublish = async (templateId: string) => {
    try {
      const { data } = await api.post(`/regional/orgs/${orgId}/templates/${templateId}/publish`);
      message.success(`已下发到 ${data.published} 个成员企业`);
      fetch();
    } catch {
      message.error("下发失败");
    }
  };

  const actionColumns: ColumnsType<Template> = [
    ...columns,
    {
      title: "操作", key: "actions", width: 120,
      render: (_: unknown, record: Template) => (
        <Button size="small" type="link" icon={<SendOutlined />} onClick={() => handlePublish(record.id)}>
          下发
        </Button>
      ),
    },
  ];

  return (
    <>
      <Alert
        className="mb-4"
        type="info"
        showIcon
        message="共享模板用于统一沉淀区域品牌的页面或活动配置，发布后可下发给成员企业复用，减少各成员重复配置。"
      />
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          新建模板
        </Button>
      </div>
      <Table columns={actionColumns} dataSource={items} rowKey="id" loading={loading} pagination={false} />
      <Modal title="新建模板" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={600}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="模板名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="config" label="模板配置 (JSON)">
            <Input.TextArea rows={6} placeholder='{"components": [...]}' />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
