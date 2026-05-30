"use client";

import { useEffect, useState } from "react";
import { App, Button, Card, Col, Descriptions, Form, Input, message, Modal, Row, Space, Statistic, Table, Tabs, Tag, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title } = Typography;

/* ---------- Org List + CRUD ---------- */

function OrgsTab() {
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const fetch = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/regional/orgs");
      setItems(Array.isArray(data) ? data : []);
    } catch {
      message.error("加载区域组织失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, []);

  const handleCreate = async (values: Record<string, unknown>) => {
    try {
      await api.post("/regional/orgs", values);
      message.success("组织创建成功");
      setOpen(false);
      form.resetFields();
      fetch();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "创建失败");
    }
  };

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "组织名称", dataIndex: "name", key: "name" },
    { title: "类型", dataIndex: "org_type", key: "org_type" },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: Record<string, unknown>) => (
        <Button size="small" type="link" onClick={() => { window.location.hash = `#org-${record.id}`; }}>
          管理成员
        </Button>
      ),
    },
  ];

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          新建组织
        </Button>
      </div>
      <Table columns={columns} dataSource={items} rowKey="id" loading={loading} pagination={false} />
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
    </>
  );
}

/* ---------- Members Management ---------- */

function MembersTab() {
  const [orgId, setOrgId] = useState("");
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const fetch = async () => {
    if (!orgId) return;
    setLoading(true);
    try {
      const { data } = await api.get(`/regional/orgs/${orgId}/members`);
      setItems(Array.isArray(data) ? data : []);
    } catch {
      message.error("加载成员失败");
    } finally {
      setLoading(false);
    }
  };

  const handleAdd = async (values: Record<string, unknown>) => {
    try {
      await api.post(`/regional/orgs/${orgId}/members`, values);
      message.success("成员添加成功");
      setOpen(false);
      form.resetFields();
      fetch();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "添加失败");
    }
  };

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "成员租户 ID", dataIndex: "member_tenant_id", key: "member_tenant_id", render: (v: string) => v?.slice(0, 8) + "..." },
    { title: "角色", dataIndex: "role", key: "role" },
  ];

  return (
    <>
      <Space className="mb-4">
        <Input placeholder="输入组织 ID" value={orgId} onChange={(e) => setOrgId(e.target.value)} style={{ width: 300 }} />
        <Button type="primary" onClick={fetch}>查询成员</Button>
        {orgId && <Button icon={<PlusOutlined />} onClick={() => setOpen(true)}>添加成员</Button>}
      </Space>
      <Table columns={columns} dataSource={items} rowKey="id" loading={loading} pagination={false} />
      <Modal title="添加成员企业" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={500}>
        <Form form={form} layout="vertical" onFinish={handleAdd}>
          <Form.Item name="member_tenant_id" label="成员租户 ID" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="role" label="角色">
            <Input placeholder="member / admin" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

/* ---------- Templates & Products ---------- */

function TemplatesTab() {
  const [orgId, setOrgId] = useState("");
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);

  const fetch = async () => {
    if (!orgId) return;
    setLoading(true);
    try {
      const { data } = await api.get(`/regional/orgs/${orgId}/templates`);
      setItems(Array.isArray(data) ? data : []);
    } catch {
      message.error("加载模板失败");
    } finally {
      setLoading(false);
    }
  };

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "模板 ID", dataIndex: "id", key: "id", render: (v: string) => v?.slice(0, 8) + "..." },
    { title: "模板名称", dataIndex: "name", key: "name" },
  ];

  return (
    <>
      <Space className="mb-4">
        <Input placeholder="输入组织 ID" value={orgId} onChange={(e) => setOrgId(e.target.value)} style={{ width: 300 }} />
        <Button type="primary" onClick={fetch}>查询模板</Button>
      </Space>
      <Table columns={columns} dataSource={items} rowKey="id" loading={loading} pagination={false} />
    </>
  );
}

/* ---------- Dashboard Tab ---------- */

function DashboardTab() {
  const [orgId, setOrgId] = useState("");
  const [data, setData] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(false);

  const fetch = async () => {
    if (!orgId) return;
    setLoading(true);
    try {
      const { data: d } = await api.get(`/regional/orgs/${orgId}/dashboard`);
      setData(d || {});
    } catch {
      message.error("加载看板失败");
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async () => {
    try {
      const response = await api.post("/analytics/exports", null, {
        params: { export_type: "regional_dashboard" },
        responseType: "blob",
      });
      const blob = new Blob([response.data], { type: "text/csv" });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "regional-dashboard.csv";
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      message.success("导出成功");
    } catch {
      message.error("导出失败，请确认您有管理员权限");
    }
  };

  return (
    <>
      <Space className="mb-4">
        <Input placeholder="输入组织 ID" value={orgId} onChange={(e) => setOrgId(e.target.value)} style={{ width: 300 }} />
        <Button type="primary" onClick={fetch}>查看数据</Button>
        <Button onClick={handleExport}>导出 CSV</Button>
      </Space>
      <Row gutter={[16, 16]}>
        <Col span={8}>
          <Card size="small"><Statistic title="成员企业数" value={Number(data.member_count) || 0} loading={loading} /></Card>
        </Col>
        <Col span={8}>
          <Card size="small"><Statistic title="授权产品数" value={Number(data.product_count) || 0} loading={loading} /></Card>
        </Col>
        <Col span={8}>
          <Card size="small"><Statistic title="总扫码量" value={Number(data.total_scans) || 0} loading={loading} /></Card>
        </Col>
      </Row>
    </>
  );
}

/* ---------- Main ---------- */

const tabItems = [
  { key: "orgs", label: "区域组织", children: <OrgsTab /> },
  { key: "members", label: "成员管理", children: <MembersTab /> },
  { key: "templates", label: "共享模板", children: <TemplatesTab /> },
  { key: "dashboard", label: "汇总看板", children: <DashboardTab /> },
];

export default function RegionalPage() {
  const { message } = App.useApp();
  return (
    <div>
      <Title level={4} className="!mb-4">区域品牌</Title>
      <Tabs defaultActiveKey="orgs" items={tabItems} />
    </div>
  );
}
