"use client";

import { Suspense, useEffect, useState } from "react";
import { App, Button, Card, Form, Input, Result, Spin, Typography } from "antd";
import { LockOutlined } from "@ant-design/icons";
import { useRouter, useSearchParams } from "next/navigation";

import api from "@/lib/api";

const { Title, Text } = Typography;

type PageState = "loading" | "form" | "success" | "error";

function ResetPasswordForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { message } = App.useApp();
  const [form] = Form.useForm<{ new_password: string; confirm_password: string }>();
  const [pageState, setPageState] = useState<PageState>("loading");
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  const token = searchParams.get("token");
  const accountId = searchParams.get("account_id");

  useEffect(() => {
    if (!token || !accountId) {
      setErrorMsg("重置链接无效，缺少必要参数。请联系管理员重新生成。");
      setPageState("error");
    } else {
      setPageState("form");
    }
  }, [token, accountId]);

  const onFinish = async (values: { new_password: string; confirm_password: string }) => {
    if (values.new_password !== values.confirm_password) {
      message.error("两次输入的密码不一致");
      return;
    }

    setSubmitting(true);
    try {
      await api.post("/auth/confirm-reset-password", {
        token,
        account_id: accountId,
        new_password: values.new_password,
      });
      setPageState("success");
    } catch (err) {
      const data = (err as { response?: { data?: { detail?: string } } })?.response?.data;
      const detail = data?.detail || "重置失败，请重试或联系管理员";
      setErrorMsg(detail);
      message.error(detail);
    } finally {
      setSubmitting(false);
    }
  };

  if (pageState === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center" style={{ background: "var(--admin-bg-layout)" }}>
        <Text type="secondary">加载中…</Text>
      </div>
    );
  }

  if (pageState === "error") {
    return (
      <div className="flex min-h-screen items-center justify-center px-4" style={{ background: "var(--admin-bg-layout)" }}>
        <Card className="w-full max-w-[420px] shadow-xl" variant="borderless">
          <Result
            status="error"
            title="无法重置密码"
            subTitle={errorMsg}
            extra={
              <Button type="primary" onClick={() => router.push("/login")}>
                返回登录
              </Button>
            }
          />
        </Card>
      </div>
    );
  }

  if (pageState === "success") {
    return (
      <div className="flex min-h-screen items-center justify-center px-4" style={{ background: "var(--admin-bg-layout)" }}>
        <Card className="w-full max-w-[420px] shadow-xl" variant="borderless">
          <Result
            status="success"
            title="密码已重置"
            subTitle="请使用新密码登录"
            extra={
              <Button type="primary" onClick={() => router.push("/login")}>
                去登录
              </Button>
            }
          />
        </Card>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4" style={{ background: "var(--admin-bg-layout)" }}>
      <Card className="w-full max-w-[420px] shadow-xl" variant="borderless">
        <div className="mb-6 text-center">
          <Title level={3} className="!mb-2">设置新密码</Title>
          <Text type="secondary">请输入您的新密码</Text>
        </div>
        <Form form={form} layout="vertical" onFinish={onFinish} size="large">
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[
              { required: true, message: "请输入新密码" },
              { min: 8, message: "密码至少 8 位" },
              {
                validator: (_, value) => {
                  if (!value) return Promise.resolve();
                  const hasLetter = /[a-zA-Z]/.test(value);
                  const hasDigit = /\d/.test(value);
                  if (hasLetter && hasDigit) return Promise.resolve();
                  return Promise.reject(new Error("密码必须包含字母和数字"));
                },
              },
            ]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="至少 8 位，包含字母和数字" />
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
            <Input.Password prefix={<LockOutlined />} placeholder="再次输入新密码" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={submitting} block>
              确认重置
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center" style={{ background: "var(--admin-bg-layout)" }}>
          <Spin size="large" />
        </div>
      }
    >
      <ResetPasswordForm />
    </Suspense>
  );
}
