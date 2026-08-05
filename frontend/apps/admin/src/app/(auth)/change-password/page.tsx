"use client";

import { useState } from "react";
import { App, Button, Card, Form, Input, Typography } from "antd";
import { LockOutlined } from "@ant-design/icons";
import { useRouter } from "next/navigation";

import api, { extractErrorMessage } from "@/lib/api";
import { useAuthStore } from "@/lib/auth";

const { Text, Title } = Typography;

interface ChangePasswordValues {
  old_password: string;
  new_password: string;
  confirm_password: string;
}

export default function ChangePasswordPage() {
  const router = useRouter();
  const { message } = App.useApp();
  const clearSession = useAuthStore((state) => state.clearSession);
  const [submitting, setSubmitting] = useState(false);

  const onFinish = async (values: ChangePasswordValues) => {
    setSubmitting(true);
    try {
      await api.post("/auth/change-password", {
        old_password: values.old_password,
        new_password: values.new_password,
      });
      clearSession();
      message.success("密码已修改，请使用新密码重新登录");
      router.replace("/login");
    } catch (error) {
      message.error(extractErrorMessage(error, "密码修改失败，请重试"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="flex min-h-screen items-center justify-center px-4"
      style={{ background: "var(--admin-bg-layout)" }}
    >
      <Card className="w-full max-w-105 shadow-xl" variant="borderless">
        <div className="mb-6 text-center">
          <Title level={3} className="!mb-2">
            首次登录修改密码
          </Title>
          <Text type="secondary">
            临时密码仅用于首次登录。修改后请使用新密码重新登录。
          </Text>
        </div>
        <Form<ChangePasswordValues>
          layout="vertical"
          size="large"
          onFinish={onFinish}
        >
          <Form.Item
            name="old_password"
            label="当前临时密码"
            rules={[{ required: true, message: "请输入当前临时密码" }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              autoComplete="current-password"
            />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[
              { required: true, message: "请输入新密码" },
              { min: 8, message: "密码至少 8 位" },
              {
                pattern: /^(?=.*[A-Za-z])(?=.*\d).+$/,
                message: "密码必须包含字母和数字",
              },
            ]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="至少 8 位，包含字母和数字"
              autoComplete="new-password"
            />
          </Form.Item>
          <Form.Item
            name="confirm_password"
            label="确认新密码"
            dependencies={["new_password"]}
            rules={[
              { required: true, message: "请再次输入新密码" },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue("new_password") === value) {
                    return Promise.resolve();
                  }
                  return Promise.reject(new Error("两次输入的密码不一致"));
                },
              }),
            ]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              autoComplete="new-password"
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={submitting} block>
            修改密码并重新登录
          </Button>
        </Form>
      </Card>
    </div>
  );
}
