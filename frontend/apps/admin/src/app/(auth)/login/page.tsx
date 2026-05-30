"use client";

import { App, Button, Card, Form, Input, Typography } from "antd";
import { LockOutlined, MailOutlined } from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/lib/auth";
import { useState } from "react";

const { Title, Text } = Typography;

export default function LoginPage() {
  const router = useRouter();
  const login = useAuthStore((s) => s.login);
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);

  const onFinish = async (values: { email: string; password: string }) => {
    setLoading(true);
    try {
      await login(values.email, values.password);
      message.success("登录成功");
      router.push("/");
    } catch {
      message.error("登录失败，请检查邮箱和密码");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50">
      <Card className="w-full max-w-md shadow-lg" variant="borderless">
        <div className="mb-8 text-center">
          <Title level={2}>一码通</Title>
          <Text type="secondary">包装扫码增长 SaaS 管理后台</Text>
        </div>
        <Form layout="vertical" onFinish={onFinish} size="large">
          <Form.Item
            name="email"
            rules={[
              { required: true, message: "请输入邮箱" },
              { type: "email", message: "请输入有效的邮箱地址" },
            ]}
          >
            <Input prefix={<MailOutlined />} placeholder="邮箱" />
          </Form.Item>
          <Form.Item
            name="password"
            rules={[{ required: true, message: "请输入密码" }]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="密码" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={loading} block>
              登录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
