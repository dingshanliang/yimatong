"use client";

import { useEffect, useState } from "react";
import { Button, Card, Form, Input, Modal, Select, Table, Tabs, Tag, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { MembersTab } from "./_components/MembersTab";
import { TemplatesTab } from "./_components/TemplatesTab";
import { DashboardTab } from "./_components/DashboardTab";
import { AdvancedDashboardTab } from "./_components/AdvancedDashboardTab";
import { CampaignsTab } from "./_components/CampaignsTab";

const { Title } = Typography;

type Org = Record<string, unknown> & {
  id: string;
  name: string;
  org_type: string;
  org_type_label?: string;
  member_count?: number;
  active_member_count?: number;
  template_count?: number;
  next_action?: string;
};

const orgColumns: ColumnsType<Org> = [
  { title: "组织名称", dataIndex: "name", key: "name" },
  { title: "类型", dataIndex: "org_type_label", key: "org_type_label", render: (v: string, record) => <Tag>{v || record.org_type}</Tag> },
  {
    title: "成员",
    key: "members",
    render: (_: unknown, record) => (
      <span data-testid={`regional-org-summary-${record.id}`}>
        {record.active_member_count || 0}/{record.member_count || 0}
      </span>
    ),
  },
  { title: "共享模板", dataIndex: "template_count", key: "template_count", render: (v: number) => v || 0 },
  {
    title: "下一步",
    dataIndex: "next_action",
    key: "next_action",
    render: (v: string, record) => <Tag color="blue" data-testid={`regional-org-next-action-${record.id}`}>{v || "添加成员企业"}</Tag>,
  },
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

  useEffect(() => {
    fetchOrgs();
  }, []);

  const selectedOrgName = orgs.find((o) => o.id === selectedOrgId)?.name || "";

  // 成员列表（用于 CampaignsTab）
  const [membersList, setMembersList] = useState<Record<string, unknown>[]>([]);
  useEffect(() => {
    if (!selectedOrgId) { setMembersList([]); return; }
    api.get(`/regional/orgs/${selectedOrgId}/members`, { params: { page_size: 100 } })
      .then(({ data }) => setMembersList(data.items || []))
      .catch(() => {});
  }, [selectedOrgId]);

  const tabItems = [
    { key: "members", label: "成员管理", children: <MembersTab orgId={selectedOrgId} />, disabled: !selectedOrgId },
    { key: "templates", label: "共享模板", children: <TemplatesTab orgId={selectedOrgId} />, disabled: !selectedOrgId },
    { key: "dashboard", label: "基础看板", children: <DashboardTab orgId={selectedOrgId} orgName={selectedOrgName} />, disabled: !selectedOrgId },
    { key: "advanced", label: "高级看板", children: <AdvancedDashboardTab orgId={selectedOrgId} orgName={selectedOrgName} />, disabled: !selectedOrgId },
    { key: "campaigns", label: "统一活动", children: <CampaignsTab orgId={selectedOrgId} members={membersList} />, disabled: !selectedOrgId },
  ];

  return (
    <div>
      <Title level={4} className="!mb-2">区域品牌</Title>
      <div className="mb-4 text-sm text-text-muted">管理区域公用品牌、协会成员、统一模板和跨成员活动。</div>

      <div className="mb-4 flex justify-between items-center">
        <span className="text-sm text-text-muted">
          {selectedOrgId ? `当前组织: ${selectedOrgName}` : "请选择一个组织"}
        </span>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          新建组织
        </Button>
      </div>

      <Card size="small" className="mb-6">
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
              background: record.id === selectedOrgId ? "var(--admin-table-row-selected-bg)" : undefined,
            },
          })}
        />
      </Card>

      {selectedOrgId && (
        <div className="mt-6">
          <Tabs defaultActiveKey="members" items={tabItems} />
        </div>
      )}

      <Modal title="新建区域组织" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={500}>
        <Form form={form} layout="vertical" onFinish={handleCreate} initialValues={{ org_type: "association" }}>
          <Form.Item name="name" label="组织名称" rules={[{ required: true }]}>
            <Input placeholder="例如：赣南脐橙协会" />
          </Form.Item>
          <Form.Item name="org_type" label="组织类型">
            <Select
              options={[
                { value: "association", label: "协会组织" },
                { value: "brand_group", label: "区域品牌集团" },
                { value: "brand", label: "区域品牌" },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
