"use client";

import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { CheckOutlined, DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { STATUS_COLORS } from "@/lib/status-colors";

const { Title, Text } = Typography;

/**
 * 自定义域名管理（白标）
 *
 * 注意：本页只管理「自定义域名 / CNAME 验证」这类后台门面配置（白标）。
 * 影响消费者扫码 H5 外观的「品牌定制」请到 /settings/brand-profile。
 * 术语区分见 CONTEXT.md：白标(whitelabel) vs 品牌定制(brand_profile)。
 */
export default function BrandingSettingsPage() {
  const [domains, setDomains] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);
  const [domainOpen, setDomainOpen] = useState(false);
  const [domainForm] = Form.useForm();
  const { message } = App.useApp();

  // 需要先获取用户的 org_id（简化：使用第一个 regional org）
  const [orgId, setOrgId] = useState<string>("");

  useEffect(() => {
    api
      .get("/regional/orgs")
      .then(({ data }) => {
        const orgs = Array.isArray(data) ? data : [];
        if (orgs.length > 0) setOrgId(orgs[0].id);
      })
      .catch(() => {});
  }, []);

  const fetchDomains = useCallback(async () => {
    if (!orgId) return;
    setLoading(true);
    try {
      const { data } = await api.get(`/regional/orgs/${orgId}/domains`);
      setDomains(Array.isArray(data) ? data : []);
    } catch {
      setDomains([]);
    } finally {
      setLoading(false);
    }
  }, [orgId]);

  useEffect(() => {
    fetchDomains();
  }, [fetchDomains]);

  const handleAddDomain = async (values: { domain: string }) => {
    if (!orgId) return;
    try {
      await api.post(`/regional/orgs/${orgId}/domains`, values);
      message.success("域名已添加");
      setDomainOpen(false);
      domainForm.resetFields();
      fetchDomains();
    } catch {
      message.error("添加失败");
    }
  };

  const handleVerify = async (domainId: string) => {
    if (!orgId) return;
    try {
      await api.post(`/regional/orgs/${orgId}/domains/${domainId}/verify`);
      message.success("域名验证成功");
      fetchDomains();
    } catch {
      message.error("验证失败");
    }
  };

  const handleDeleteDomain = async (domainId: string) => {
    if (!orgId) return;
    try {
      await api.delete(`/regional/orgs/${orgId}/domains/${domainId}`);
      message.success("域名已删除");
      fetchDomains();
    } catch {
      message.error("删除失败");
    }
  };

  const domainCols: ColumnsType<Record<string, unknown>> = [
    { title: "域名", dataIndex: "domain", key: "domain" },
    {
      title: "状态",
      key: "status",
      render: (_: unknown, record: Record<string, unknown>) => (
        <Space>
          {record.verified ? (
            <Tag color={STATUS_COLORS.success}>已验证</Tag>
          ) : (
            <Tag color={STATUS_COLORS.warning}>待验证</Tag>
          )}
          <Tag>{String(record.ssl_status)}</Tag>
        </Space>
      ),
    },
    { title: "CNAME 目标", dataIndex: "cname_target", key: "cname_target" },
    {
      title: "操作",
      key: "actions",
      width: 150,
      render: (_: unknown, record: Record<string, unknown>) => (
        <Space>
          {!record.verified && (
            <Button
              size="small"
              type="link"
              icon={<CheckOutlined />}
              onClick={() => handleVerify(String(record.id))}
            >
              验证
            </Button>
          )}
          <Button
            size="small"
            type="link"
            danger
            icon={<DeleteOutlined />}
            onClick={() => handleDeleteDomain(String(record.id))}
          />
        </Space>
      ),
    },
  ];

  return (
    <div style={{ maxWidth: 800 }}>
      <Title level={4} className="!mb-1">
        自定义域名
      </Title>
      <Text type="secondary" className="mb-4 block">
        在自有域名下运营一码通后台与登录入口。若要调整消费者扫码页的品牌外观，请前往
        <a href="/settings/brand-profile"> 品牌定制</a>。
      </Text>

      <Card
        title="自定义域名"
        size="small"
        extra={
          <Button
            size="small"
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setDomainOpen(true)}
          >
            添加域名
          </Button>
        }
      >
        <Table
          columns={domainCols}
          dataSource={domains}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={false}
        />
        {domains.length === 0 && !loading && (
          <p className="py-4 text-center text-sm text-text-muted">
            暂无自定义域名。请将域名的 CNAME 指向 cname.yimatong.cn
          </p>
        )}
      </Card>

      <Modal
        title="添加自定义域名"
        open={domainOpen}
        onCancel={() => setDomainOpen(false)}
        onOk={() => domainForm.submit()}
        width={450}
      >
        <Form form={domainForm} layout="vertical" onFinish={handleAddDomain}>
          <Form.Item name="domain" label="域名" rules={[{ required: true }]}>
            <Input placeholder="brand.example.com" />
          </Form.Item>
          <p className="text-xs text-text-muted">
            请先将该域名的 CNAME 记录指向 cname.yimatong.cn
          </p>
        </Form>
      </Modal>
    </div>
  );
}
