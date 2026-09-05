"use client";

import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  message,
  Modal,
  Select,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api, { extractErrorMessage } from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { channelAccessForPrincipal } from "@/lib/channel-access";
import { STATUS_COLORS } from "@/lib/status-colors";
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
  {
    title: "类型",
    dataIndex: "org_type_label",
    key: "org_type_label",
    render: (v: string, record) => <Tag>{v || record.org_type}</Tag>,
  },
  {
    title: "成员",
    key: "members",
    render: (_: unknown, record) => (
      <span data-testid={`regional-org-summary-${record.id}`}>
        {record.active_member_count || 0}/{record.member_count || 0}
      </span>
    ),
  },
  {
    title: "共享模板",
    dataIndex: "template_count",
    key: "template_count",
    render: (v: number) => v || 0,
  },
  {
    title: "下一步",
    dataIndex: "next_action",
    key: "next_action",
    render: (v: string, record) => (
      <Tag
        color={STATUS_COLORS.processing}
        data-testid={`regional-org-next-action-${record.id}`}
      >
        {v || "添加成员企业"}
      </Tag>
    ),
  },
];

export default function RegionalPage() {
  const user = useAuthStore((state) => state.user);
  const access = channelAccessForPrincipal(user);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [selectedOrgId, setSelectedOrgId] = useState<string>("");
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const fetchOrgs = async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const { data } = await api.get("/regional/orgs");
      setOrgs(Array.isArray(data) ? data : []);
    } catch (err) {
      setLoadError(true);
      message.error(extractErrorMessage(err, "加载区域组织失败"));
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async (values: { name: string; org_type: string }) => {
    try {
      await api.post("/regional/orgs", values);
      message.success("区域组织已创建");
      setOpen(false);
      form.resetFields();
      fetchOrgs();
    } catch (err) {
      message.error(extractErrorMessage(err, "创建区域组织失败"));
    }
  };

  useEffect(() => {
    if (!access.canRead) return;
    fetchOrgs();
    // fetchOrgs 每次渲染重建，无资格参与依赖；canRead 变化（登录态水合）时补拉一次
  }, [access.canRead]);

  const selectedOrgName = orgs.find((o) => o.id === selectedOrgId)?.name || "";

  // 成员列表（用于 CampaignsTab）
  const [membersList, setMembersList] = useState<Record<string, unknown>[]>([]);
  useEffect(() => {
    if (!selectedOrgId) {
      setMembersList([]);
      return;
    }
    api
      .get(`/regional/orgs/${selectedOrgId}/members`, {
        params: { page_size: 100 },
      })
      .then(({ data }) => setMembersList(data.items || []))
      .catch((err) =>
        message.error(extractErrorMessage(err, "加载成员列表失败"))
      );
  }, [selectedOrgId]);

  const tabItems = [
    {
      key: "members",
      label: "成员管理",
      children: <MembersTab orgId={selectedOrgId} />,
      disabled: !selectedOrgId,
    },
    {
      key: "templates",
      label: "共享模板",
      children: <TemplatesTab orgId={selectedOrgId} />,
      disabled: !selectedOrgId,
    },
    {
      key: "dashboard",
      label: "基础看板",
      children: (
        <DashboardTab orgId={selectedOrgId} orgName={selectedOrgName} />
      ),
      disabled: !selectedOrgId,
    },
    {
      key: "advanced",
      label: "高级看板",
      children: (
        <AdvancedDashboardTab orgId={selectedOrgId} orgName={selectedOrgName} />
      ),
      disabled: !selectedOrgId,
    },
    {
      key: "campaigns",
      label: "统一活动",
      children: <CampaignsTab orgId={selectedOrgId} members={membersList} />,
      disabled: !selectedOrgId,
    },
  ];

  // 后端读路由要求 channel:read（viewer 无任何权限码）；写按钮仅 admin/operator 可见
  if (!access.canRead) {
    return <Alert type="warning" message="当前账号无区域组织管理权限" />;
  }

  return (
    <div>
      <Title level={4} className="!mb-2">
        区域品牌
      </Title>
      <div className="mb-4 text-sm text-text-muted">
        管理区域公用品牌、协会成员、统一模板和跨成员活动。
      </div>

      <div className="mb-4 flex justify-between items-center">
        <span className="text-sm text-text-muted">
          {selectedOrgId ? `当前组织: ${selectedOrgName}` : "请选择一个组织"}
        </span>
        {access.canManage && (
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setOpen(true)}
          >
            新建组织
          </Button>
        )}
      </div>

      {loadError && (
        <Alert
          type="error"
          showIcon
          className="mb-4"
          message="区域组织加载失败"
          description="请检查网络后重试；多次失败请联系管理员。"
          action={
            <Button size="small" onClick={fetchOrgs}>
              重试
            </Button>
          }
        />
      )}

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
              background:
                record.id === selectedOrgId
                  ? "var(--admin-table-row-selected-bg)"
                  : undefined,
            },
          })}
        />
      </Card>

      {selectedOrgId && (
        <div className="mt-6">
          <Tabs defaultActiveKey="members" items={tabItems} />
        </div>
      )}

      <Modal
        title="新建区域组织"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        width={500}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleCreate}
          initialValues={{ org_type: "association" }}
        >
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
