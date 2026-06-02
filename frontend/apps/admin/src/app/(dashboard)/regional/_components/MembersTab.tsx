"use client";

import { useEffect, useState } from "react";
import api from "@/lib/api";
import { Button, Form, Input, Modal, Select, Space, Table, Tag, message } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";

type Member = Record<string, unknown> & {
  id: string;
  member_name: string;
  tenant_id: string;
  status: string;
};

type ClientOption = {
  id: string;
  name: string;
};

const statusMap: Record<string, { color: string; label: string }> = {
  active: { color: "green", label: "活跃" },
  suspended: { color: "orange", label: "暂停" },
  expelled: { color: "red", label: "已移除" },
};

const columns: ColumnsType<Member> = [
  { title: "企业名称", dataIndex: "member_name", key: "member_name" },
  { title: "租户 ID", dataIndex: "tenant_id", key: "tenant_id", render: (v: string) => v?.slice(0, 8) + "..." },
  {
    title: "状态", dataIndex: "status", key: "status", width: 100,
    render: (v: string) => {
      const info = statusMap[v] || { color: "default", label: v };
      return <Tag color={info.color}>{info.label}</Tag>;
    },
  },
];

export function MembersTab({ orgId }: { orgId: string }) {
  const [items, setItems] = useState<Member[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [open, setOpen] = useState(false);
  const [clients, setClients] = useState<ClientOption[]>([]);
  const [form] = Form.useForm();
  const fetch = async () => {
    if (!orgId) return;
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: "20" });
      if (statusFilter) params.set("status", statusFilter);
      const { data } = await api.get(`/regional/orgs/${orgId}/members?${params}`);
      setItems(data?.items || []);
      setTotal(data?.total || 0);
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, [orgId, page, statusFilter]);

  const handleAdd = async (values: { tenant_id: string; member_name: string }) => {
    try {
      await api.post(`/regional/orgs/${orgId}/members`, values);
      message.success("成员添加成功");
      setOpen(false);
      form.resetFields();
      fetch();
    } catch {
      message.error("添加失败");
    }
  };

  const handleStatusChange = async (memberId: string, status: string) => {
    try {
      await api.put(`/regional/orgs/${orgId}/members/${memberId}`, { status });
      message.success("状态已更新");
      fetch();
    } catch {
      message.error("更新失败");
    }
  };

  const handleRemove = async (memberId: string) => {
    try {
      await api.delete(`/regional/orgs/${orgId}/members/${memberId}`);
      message.success("成员已移除");
      fetch();
    } catch {
      message.error("移除失败");
    }
  };

  const fetchClients = async () => {
    try {
      const { data } = await api.get("/tenants", { params: { page: 1, page_size: 100 } });
      setClients((data.items || []).map((item: Record<string, unknown>) => ({ id: String(item.id), name: String(item.name || "") })));
    } catch {
      setClients([]);
    }
  };

  const openMemberModal = () => {
    fetchClients();
    setOpen(true);
  };

  const actionColumns: ColumnsType<Member> = [
    ...columns,
    {
      title: "操作", key: "actions", width: 200,
      render: (_: unknown, record: Member) => (
        <Space size="small">
          {record.status === "active" && (
            <>
              <Button size="small" type="link" onClick={() => handleStatusChange(record.id, "suspended")}>暂停</Button>
              <Button size="small" type="link" danger onClick={() => handleRemove(record.id)}>移除</Button>
            </>
          )}
          {record.status === "suspended" && (
            <Button size="small" type="link" onClick={() => handleStatusChange(record.id, "active")}>恢复</Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <>
      <div className="mb-4 flex justify-between">
        <Select
          placeholder="状态筛选"
          allowClear
          style={{ width: 120 }}
          onChange={(v) => { setStatusFilter(v); setPage(1); }}
          options={[
            { label: "活跃", value: "active" },
            { label: "暂停", value: "suspended" },
            { label: "已移除", value: "expelled" },
          ]}
        />
        <Button type="primary" icon={<PlusOutlined />} onClick={openMemberModal}>
          添加成员
        </Button>
      </div>
      <Table
        columns={actionColumns}
        dataSource={items}
        rowKey="id"
        loading={loading}
        pagination={{ current: page, total, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
      />
      <Modal title="添加成员企业" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={500}>
        <Form form={form} layout="vertical" onFinish={handleAdd}>
          <Form.Item name="tenant_id" label="成员企业" rules={[{ required: true, message: "请选择成员企业" }]}>
            <Select
              data-testid="member-client-select"
              showSearch
              placeholder="选择已开通客户"
              optionFilterProp="label"
              options={clients.map((client) => ({ value: client.id, label: client.name }))}
              onChange={(value) => {
                const client = clients.find((item) => item.id === value);
                if (client) form.setFieldValue("member_name", client.name);
              }}
            />
          </Form.Item>
          <Form.Item name="member_name" label="企业名称" rules={[{ required: true }]}>
            <Input placeholder="用于区域品牌成员列表展示" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
