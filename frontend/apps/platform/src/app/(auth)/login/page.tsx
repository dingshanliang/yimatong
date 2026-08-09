"use client";

import { useEffect, useState } from "react";
import { Button, Card, Form, Input, message, Typography } from "antd";
import { LockOutlined, MailOutlined } from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { usePlatformAuth } from "@/lib/platform-auth";
import { extractErrorMessage } from "@/lib/api";

const { Title, Text } = Typography;

export default function PlatformLoginPage() {
  const [loading, setLoading] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const router = useRouter();
  const login = usePlatformAuth((s) => s.login);

  useEffect(() => setHydrated(true), []);

  const onFinish = async (values: { email: string; password: string }) => {
    setLoading(true);
    try {
      await login(values.email, values.password);
      message.success("登录成功");
      router.push("/");
    } catch (err) {
      message.error(extractErrorMessage(err, "登录失败，请检查邮箱和密码"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background:
          "linear-gradient(135deg, var(--ymt-color-brand-800) 0%, var(--ymt-color-brand-500) 100%)",
      }}
    >
      <Card
        style={{
          width: 400,
          borderRadius: "var(--ymt-radius-md)",
          boxShadow: "var(--ymt-shadow-overlay)",
        }}
        styles={{ body: { padding: "40px 32px" } }}
      >
        <div style={{ textAlign: "center", marginBottom: 32 }}>
          <Title
            level={3}
            style={{ marginBottom: 4, color: "var(--ymt-color-brand-primary)" }}
          >
            一码通 · 平台管理
          </Title>
          <Text type="secondary">SaaS 平台管理后台</Text>
        </div>

        <Form
          data-testid="platform-login-form"
          data-hydrated={hydrated}
          layout="vertical"
          onFinish={onFinish}
          size="large"
          autoComplete="off"
        >
          <Form.Item
            name="email"
            rules={[
              { required: true, message: "请输入邮箱" },
              { type: "email", message: "邮箱格式不正确" },
            ]}
          >
            <Input prefix={<MailOutlined />} placeholder="管理员邮箱" />
          </Form.Item>

          <Form.Item
            name="password"
            rules={[{ required: true, message: "请输入密码" }]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="密码" />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" htmlType="submit" loading={loading} block>
              登 录
            </Button>
          </Form.Item>
        </Form>

        <div style={{ textAlign: "center", marginTop: 16 }}>
          <Text
            type="secondary"
            style={{ fontSize: "var(--ymt-font-size-xs)" }}
          >
            仅限平台管理员访问
          </Text>
        </div>
      </Card>
    </div>
  );
}
