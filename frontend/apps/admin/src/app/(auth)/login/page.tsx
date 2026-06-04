"use client";

import { useState } from "react";
import { App, Button, Card, Divider, Form, Input, Space, Tag, Typography } from "antd";
import { CrownOutlined, GlobalOutlined, LockOutlined, MailOutlined, ShopOutlined, TeamOutlined, UserSwitchOutlined } from "@ant-design/icons";
import { useRouter } from "next/navigation";
import axios from "axios";
import { useAuthStore } from "@/lib/auth";

const { Title, Text } = Typography;

const DEMO_ACCOUNTS = [
  {
    key: "admin",
    label: "品牌管理员",
    role: "admin",
    email: "admin@demo.com",
    password: "Admin1234",
    description: "查看全量经营、码、活动和权限",
    icon: <CrownOutlined />,
  },
  {
    key: "ops",
    label: "活动运营",
    role: "operator",
    email: "ops@demo.com",
    password: "Ops123456",
    description: "配置产品、页面、码和活动",
    icon: <TeamOutlined />,
  },
  {
    key: "agency",
    label: "代运营顾问",
    role: "operator",
    email: "agency@demo.com",
    password: "Agency1234",
    description: "代客户维护日常运营动作",
    icon: <UserSwitchOutlined />,
  },
  {
    key: "distributor",
    label: "经销商入口",
    role: "distributor",
    email: "dist@demo.com",
    password: "Dist123456",
    description: "查看收货流向、区域门店统计和异常线索",
    icon: <TeamOutlined />,
    route: "/channel-portal",
  },
  {
    key: "store",
    label: "门店入口",
    role: "store_guide",
    email: "store@demo.com",
    password: "Store123456",
    description: "查看本店资料、收货批次和扫码趋势",
    icon: <ShopOutlined />,
    route: "/store-portal",
  },
  {
    key: "platform",
    label: "平台管理员",
    role: "platform_admin",
    email: "platform@yimatong.cn",
    password: "Platform1234",
    description: "管理租户、套餐、审计日志和全局配置",
    icon: <GlobalOutlined />,
    platform: true,
  },
];

export default function LoginPage() {
  const router = useRouter();
  const login = useAuthStore((s) => s.login);
  const { message } = App.useApp();
  const [form] = Form.useForm<{ email: string; password: string }>();
  const [loading, setLoading] = useState(false);
  const [loadingAccount, setLoadingAccount] = useState<string | null>(null);

  const onFinish = async (values: { email: string; password: string }, redirectTo = "/", tenantSlug?: string) => {
    setLoading(true);
    try {
      await login(values.email, values.password, tenantSlug ? { tenantSlug } : undefined);
      message.success("登录成功");
      router.push(redirectTo);
    } catch (err) {
      const data = (err as { response?: { data?: { detail?: string } } })?.response?.data;
      const detail = data?.detail;
      if (detail?.toLowerCase().includes("locked")) {
        message.error("账户已被锁定，请 15 分钟后再试");
      } else if (detail) {
        message.error(detail);
      } else {
        message.error("登录失败，请检查邮箱和密码");
      }
    } finally {
      setLoading(false);
    }
  };

  const handleDemoLogin = async (account: (typeof DEMO_ACCOUNTS)[number]) => {
    setLoadingAccount(account.key);
    try {
      if (account.platform) {
        // 平台管理员：使用独立登录端点和 cookie
        const API_BASE =
          process.env.NEXT_PUBLIC_API_URL ||
          (window.location.hostname === "127.0.0.1" ? "http://127.0.0.1:8000" : "http://localhost:8000");
        const { data } = await axios.post(`${API_BASE}/api/v1/platform/auth/login`, {
          email: account.email,
          password: account.password,
        });
        const { access_token } = data;
        localStorage.setItem("platform_access_token", access_token);
        const secure = window.location.protocol === "https:" ? "; Secure" : "";
        document.cookie = `platform_access_token=${access_token}; path=/; max-age=${30 * 24 * 3600}; SameSite=Lax${secure}`;
        message.success("平台管理员登录成功，正在跳转…");
        // 跳转到平台管理后台（端口 3002）
        const platformUrl = process.env.NEXT_PUBLIC_PLATFORM_URL || "http://localhost:3002";
        window.open(platformUrl, "_blank");
      } else {
        form.setFieldsValue({ email: account.email, password: account.password });
        await onFinish({ email: account.email, password: account.password }, account.route ?? "/", "demo");
      }
    } finally {
      setLoadingAccount(null);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-8" style={{ background: "var(--admin-bg-layout)" }}>
      <Card className="w-full max-w-[520px] shadow-xl" variant="borderless">
        <div className="mb-8 text-center">
          <Title level={2} className="!mb-2">一码通</Title>
          <Text type="secondary">包装扫码增长 SaaS 管理后台</Text>
        </div>
        <div className="admin-muted-panel mb-6 rounded-md border p-3">
          <div className="mb-3 flex items-center justify-between">
            <Text strong>演示快捷账号</Text>
            <Tag color="green">Demo Ready</Tag>
          </div>
          <Space orientation="vertical" className="w-full" size={8}>
            {DEMO_ACCOUNTS.map((account) => (
              <span key={account.key}>
                {account.platform && <Divider className="!my-2" plain><Text type="secondary" className="!text-xs">平台管理</Text></Divider>}
                <Button
                  block
                  className="!h-auto !justify-start !py-3 text-left"
                  icon={account.icon}
                  loading={loadingAccount === account.key}
                  onClick={() => handleDemoLogin(account)}
                >
                  <span className="flex w-full items-center justify-between gap-3">
                    <span className="min-w-0">
                      <span className="block font-medium">{account.label}</span>
                      <span className="block truncate text-xs text-text-muted">{account.description}</span>
                    </span>
                    <Tag className="m-0" color={account.role === "platform_admin" ? "purple" : account.role === "admin" ? "gold" : "blue"}>
                      {account.role}
                    </Tag>
                  </span>
                </Button>
              </span>
            ))}
          </Space>
        </div>
        <Form form={form} layout="vertical" onFinish={onFinish} size="large">
          <Form.Item
            name="email"
            rules={[
              { required: true, message: "请输入邮箱" },
              { type: "email", message: "请输入有效的邮箱地址" },
            ]}
          >
            <Input prefix={<MailOutlined />} placeholder="邮箱" autoComplete="email" />
          </Form.Item>
          <Form.Item
            name="password"
            rules={[{ required: true, message: "请输入密码" }]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="密码" autoComplete="current-password" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={loading} block>
              手动登录
            </Button>
          </Form.Item>
          <div className="text-center">
            <Text type="secondary" className="text-sm">
              忘记密码？请联系您的管理员重置
            </Text>
          </div>
        </Form>
      </Card>
    </div>
  );
}
