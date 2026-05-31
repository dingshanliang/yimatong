"use client";

import { useEffect, useState } from "react";
import { App, Button, Card, ColorPicker, Form, Input, Modal, Space, Switch, Table, Tag, Typography } from "antd";
import { CheckOutlined, DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title } = Typography;

export default function BrandingSettingsPage() {
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [domains, setDomains] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);
  const [domainOpen, setDomainOpen] = useState(false);
  const [domainForm] = Form.useForm();
  const { message } = App.useApp();

  // 需要先获取用户的 org_id（简化：使用第一个 regional org）
  const [orgId, setOrgId] = useState<string>("");

  useEffect(() => {
    api.get("/regional/orgs").then(({ data }) => {
      const orgs = Array.isArray(data) ? data : [];
      if (orgs.length > 0) setOrgId(orgs[0].id);
    }).catch(() => {});
  }, []);

  const fetchConfig = async () => {
    if (!orgId) return;
    setLoading(true);
    try {
      const { data } = await api.get(`/regional/orgs/${orgId}/whitelabel-config`);
      setConfig(data || {});
    } catch { /* silent */ }
    try {
      const { data } = await api.get(`/regional/orgs/${orgId}/domains`);
      setDomains(Array.isArray(data) ? data : []);
    } catch { /* silent */ }
    setLoading(false);
  };

  useEffect(() => { fetchConfig(); }, [orgId]);

  const handleSave = async (values: Record<string, unknown>) => {
    if (!orgId) return;
    try {
      await api.put(`/regional/orgs/${orgId}/whitelabel-config`, values);
      message.success("白标配置已保存");
      fetchConfig();
    } catch {
      message.error("保存失败");
    }
  };

  const handleAddDomain = async (values: { domain: string }) => {
    if (!orgId) return;
    try {
      await api.post(`/regional/orgs/${orgId}/domains`, values);
      message.success("域名已添加");
      setDomainOpen(false);
      domainForm.resetFields();
      fetchConfig();
    } catch {
      message.error("添加失败");
    }
  };

  const handleVerify = async (domainId: string) => {
    if (!orgId) return;
    try {
      await api.post(`/regional/orgs/${orgId}/domains/${domainId}/verify`);
      message.success("域名验证成功");
      fetchConfig();
    } catch {
      message.error("验证失败");
    }
  };

  const handleDeleteDomain = async (domainId: string) => {
    if (!orgId) return;
    try {
      await api.delete(`/regional/orgs/${orgId}/domains/${domainId}`);
      message.success("域名已删除");
      fetchConfig();
    } catch {
      message.error("删除失败");
    }
  };

  const domainCols: ColumnsType<Record<string, unknown>> = [
    { title: "域名", dataIndex: "domain", key: "domain" },
    {
      title: "状态", key: "status",
      render: (_: unknown, record: Record<string, unknown>) => (
        <Space>
          {record.verified ? <Tag color="green">已验证</Tag> : <Tag color="orange">待验证</Tag>}
          <Tag>{String(record.ssl_status)}</Tag>
        </Space>
      ),
    },
    { title: "CNAME 目标", dataIndex: "cname_target", key: "cname_target" },
    {
      title: "操作", key: "actions", width: 150,
      render: (_: unknown, record: Record<string, unknown>) => (
        <Space>
          {!record.verified && (
            <Button size="small" type="link" icon={<CheckOutlined />} onClick={() => handleVerify(String(record.id))}>验证</Button>
          )}
          <Button size="small" type="link" danger icon={<DeleteOutlined />} onClick={() => handleDeleteDomain(String(record.id))} />
        </Space>
      ),
    },
  ];

  return (
    <div style={{ maxWidth: 800 }}>
      <Title level={4} className="!mb-4">品牌定制</Title>

      <Card title="品牌外观" size="small" className="mb-4">
        <Form layout="vertical" onFinish={handleSave} initialValues={{
          brand_name: config.brand_name || "",
          primary_color: config.primary_color || "#000000",
          hide_yimatong: config.hide_yimatong || false,
          logo_url: config.logo_url || "",
          favicon_url: config.favicon_url || "",
          login_bg_url: config.login_bg_url || "",
          font_family: config.font_family || "",
          custom_css: config.custom_css || "",
        }}>
          <Form.Item name="brand_name" label="品牌名称">
            <Input placeholder="留空使用一码通默认" />
          </Form.Item>
          <Form.Item name="primary_color" label="主色调">
            <Input placeholder="#000000" />
          </Form.Item>
          <Form.Item name="logo_url" label="Logo URL">
            <Input placeholder="https://..." />
          </Form.Item>
          <Form.Item name="favicon_url" label="Favicon URL">
            <Input placeholder="https://..." />
          </Form.Item>
          <Form.Item name="login_bg_url" label="登录页背景图 URL">
            <Input placeholder="https://..." />
          </Form.Item>
          <Form.Item name="font_family" label="字体">
            <Input placeholder="如: 'Noto Sans SC', sans-serif" />
          </Form.Item>
          <Form.Item name="hide_yimatong" label="隐藏一码通标识" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="custom_css" label="自定义 CSS">
            <Input.TextArea rows={4} placeholder="/* 自定义样式 */" />
          </Form.Item>
          <Button type="primary" htmlType="submit">保存配置</Button>
        </Form>
      </Card>

      <Card title="自定义域名" size="small" extra={
        <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => setDomainOpen(true)}>
          添加域名
        </Button>
      }>
        <Table columns={domainCols} dataSource={domains} rowKey="id" loading={loading} size="small" pagination={false} />
        {domains.length === 0 && !loading && (
          <p className="py-4 text-center text-sm text-gray-400">暂无自定义域名。请将域名的 CNAME 指向 cname.yimatong.cn</p>
        )}
      </Card>

      <Modal title="添加自定义域名" open={domainOpen} onCancel={() => setDomainOpen(false)} onOk={() => domainForm.submit()} width={450}>
        <Form form={domainForm} layout="vertical" onFinish={handleAddDomain}>
          <Form.Item name="domain" label="域名" rules={[{ required: true }]}>
            <Input placeholder="brand.example.com" />
          </Form.Item>
          <p className="text-xs text-gray-400">请先将该域名的 CNAME 记录指向 cname.yimatong.cn</p>
        </Form>
      </Modal>
    </div>
  );
}
