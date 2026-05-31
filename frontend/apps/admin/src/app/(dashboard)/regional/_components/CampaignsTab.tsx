"use client";

import { useEffect, useState } from "react";
import { App, Button, Form, Input, Modal, Select, Table, Tag } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

interface Props {
  orgId: string;
  members: Record<string, unknown>[];
}

export function CampaignsTab({ orgId, members }: Props) {
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const { message } = App.useApp();

  const fetch = async () => {
    if (!orgId) return;
    setLoading(true);
    try {
      const { data } = await api.get(`/regional/orgs/${orgId}/unified-campaigns`);
      setItems(Array.isArray(data) ? data : []);
    } catch {
      message.error("加载统一活动失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, [orgId]);

  const handleCreate = async (values: Record<string, unknown>) => {
    try {
      await api.post(`/regional/orgs/${orgId}/unified-campaigns`, values);
      message.success("统一活动创建成功");
      setOpen(false);
      form.resetFields();
      fetch();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "创建失败");
    }
  };

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "活动名称", dataIndex: "name", key: "name" },
    { title: "参与成员数", dataIndex: "member_count", key: "member_count" },
    {
      title: "状态", dataIndex: "status", key: "status",
      render: (v: string) => {
        const color = v === "active" ? "green" : v === "draft" ? "default" : "red";
        return <Tag color={color}>{v === "active" ? "进行中" : v === "draft" ? "草稿" : v}</Tag>;
      },
    },
    {
      title: "创建时间", dataIndex: "created_at", key: "created_at",
      render: (v: string) => v ? new Date(v).toLocaleDateString() : "—",
    },
  ];

  const memberOptions = members.map((m) => ({
    value: String(m.id),
    label: String(m.member_name || m.tenant_id),
  }));

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          创建统一活动
        </Button>
      </div>
      <Table columns={columns} dataSource={items} rowKey="id" loading={loading} size="small" pagination={{ pageSize: 20 }} />

      <Modal title="创建统一营销活动" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={500}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="活动名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="活动描述">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="member_ids" label="参与成员（空=全部）">
            <Select mode="multiple" options={memberOptions} placeholder="选择成员企业" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
