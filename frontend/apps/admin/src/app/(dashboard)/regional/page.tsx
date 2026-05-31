"use client";

import { useState } from "react";
import { Button, Form, Input, Modal, Table, Tabs, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { MembersTab } from "./_components/MembersTab";
import { TemplatesTab } from "./_components/TemplatesTab";
import { DashboardTab } from "./_components/DashboardTab";

const { Title } = Typography;

type Org = Record<string, unknown> & { id: string; name: string; org_type: string };

const orgColumns: ColumnsType<Org> = [
  { title: "组织名称", dataIndex: "name", key: "name" },
  { title: "类型", dataIndex: "org_type", key: "org_type" },
];

export default function RegionalPage() {
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedOrgId, setSelectedOrgId] = useState<string>("");
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const fetchOrgs = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/regional/orgs");
      setOrgs(Array.isArray(data) ? data : []);
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async (values: { name: string; org_type: string }) => {
    try {
      await api.post("/regional/orgs", values);
      setOpen(false);
      form.resetFields();
      fetchOrgs();
    } catch {
      /* silent */
    }
  };

  // 初次加载
  if (orgs.length === 0 && !loading) {
    fetchOrgs();
  }

  const selectedOrgName = orgs.find((o) => o.id === selectedOrgId)?.name || "";

  const tabItems = [
    { key: "members", label: "成员管理", children: <MembersTab orgId={selectedOrgId} />, disabled: !selectedOrgId },
    { key: "templates", label: "共享模板", children: <TemplatesTab orgId={selectedOrgId} />, disabled: !selectedOrgId },
    { key: "dashboard", label: "汇总看板", children: <DashboardTab orgId={selectedOrgId} orgName={selectedOrgName} />, disabled: !selectedOrgId },
  ];

  return (
    <div>
      <Title level={4} className="!mb-4">区域品牌</Title>

      <div className="mb-4 flex justify-between items-center">
        <span className="text-sm text-gray-500">
          {selectedOrgId ? `当前组织: ${selectedOrgName}` : "请选择一个组织"}
        </span>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          新建组织
        </Button>
      </div>

      <Table
        columns={orgColumns}
        dataSource={orgs}
        rowKey="id"
        loading={loading}
        pagination={false}
        onRow={(record) => ({
          onClick: () => setSelectedOrgId(record.id),
          style: {
            cursor: "pointer",
            background: record.id === selectedOrgId ? "#e6f4ff" : undefined,
          },
        })}
      />

      {selectedOrgId && (
        <div className="mt-6">
          <Tabs defaultActiveKey="members" items={tabItems} />
        </div>
      )}

      <Modal title="新建区域组织" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={500}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="组织名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="org_type" label="组织类型">
            <Input placeholder="association / brand_group" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
