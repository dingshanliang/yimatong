"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Result,
  Spin,
  Typography,
} from "antd";
import {
  KeyOutlined,
  LockOutlined,
  MailOutlined,
  ShopOutlined,
  UserOutlined,
} from "@ant-design/icons";

import api, { extractErrorMessage } from "@/lib/api";

const { Text, Title } = Typography;

interface RegisterValues {
  invite_code: string;
  name: string;
  admin_name: string;
  admin_email: string;
  admin_password: string;
  confirm_password: string;
  industry?: string;
}

interface RegisterResponse {
  tenant_id: string;
  tenant_slug: string;
  message: string;
}

function RegisterForm() {
  const searchParams = useSearchParams();
  const [form] = Form.useForm<RegisterValues>();
  const [submitting, setSubmitting] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const [registered, setRegistered] = useState<{
    email: string;
    tenantSlug: string;
  } | null>(null);
  const [errorMessage, setErrorMessage] = useState("");
  const idempotencyKeyRef = useRef<string | null>(null);

  useEffect(() => {
    setHydrated(true);
    const inviteCode = searchParams.get("invite_code")?.trim();
    if (inviteCode) form.setFieldValue("invite_code", inviteCode);
  }, [form, searchParams]);

  const onFinish = async (values: RegisterValues) => {
    setSubmitting(true);
    setErrorMessage("");
    const idempotencyKey =
      idempotencyKeyRef.current ?? window.crypto.randomUUID();
    idempotencyKeyRef.current = idempotencyKey;
    try {
      const { data } = await api.post<RegisterResponse>(
        "/invite-codes/register",
        {
          invite_code: values.invite_code.trim(),
          name: values.name.trim(),
          admin_email: values.admin_email.trim(),
          admin_name: values.admin_name.trim(),
          admin_password: values.admin_password,
          industry: values.industry?.trim() || null,
        },
        { headers: { "Idempotency-Key": idempotencyKey } }
      );
      const loginHandoff = {
        email: values.admin_email.trim(),
        tenantSlug: data.tenant_slug,
      };
      // Keep PII out of the URL, referrer, and server access logs. This is a
      // same-tab, one-time convenience handoff; the password is never stored.
      try {
        window.sessionStorage.setItem(
          "registration-login-handoff",
          JSON.stringify(loginHandoff)
        );
      } catch {
        // Login remains available for manual entry when storage is unavailable.
      }
      setRegistered(loginHandoff);
    } catch (error) {
      const status = (error as { response?: { status?: number } }).response
        ?.status;
      // A definite 4xx business response is safe to correct and submit as a new
      // request. Network/5xx outcomes may have committed, so retries reuse the key.
      if (status && status >= 400 && status < 500) {
        idempotencyKeyRef.current = null;
      }
      setErrorMessage(
        extractErrorMessage(error, "注册未完成，请检查信息后重试")
      );
    } finally {
      setSubmitting(false);
    }
  };

  if (registered) {
    return (
      <Card className="w-full max-w-120 shadow-xl" variant="borderless">
        <Result
          status="success"
          title="品牌账号已创建"
          subTitle={`管理员邮箱 ${registered.email}，工作区标识 ${registered.tenantSlug}。请核对后登录。`}
          extra={
            <Link href="/login">
              <Button type="primary">核对信息并登录</Button>
            </Link>
          }
        />
      </Card>
    );
  }

  return (
    <Card className="w-full max-w-150 shadow-xl" variant="borderless">
      <div className="mb-6 text-center">
        <Title level={2} className="!mb-2">
          开通品牌账号
        </Title>
        <Text type="secondary">
          填写邀请码和管理员信息，提交后即可登录一码通品牌后台。
        </Text>
      </div>

      {errorMessage && (
        <Alert
          className="mb-5"
          type="error"
          showIcon
          message="暂时无法完成注册"
          description={
            <div>
              <div>{errorMessage}</div>
              <div className="mt-1">
                请根据提示修改信息后再次提交。如果邀请码已过期或已用完，请联系发放方重新生成。
              </div>
            </div>
          }
        />
      )}

      <Form<RegisterValues>
        data-testid="admin-registration-form"
        data-hydrated={hydrated}
        form={form}
        layout="vertical"
        size="large"
        onFinish={onFinish}
        onValuesChange={() => {
          if (errorMessage) {
            setErrorMessage("");
            idempotencyKeyRef.current = null;
          }
        }}
      >
        <Form.Item
          name="invite_code"
          label="邀请码"
          rules={[
            { required: true, message: "请输入邀请码" },
            { min: 6, message: "邀请码至少 6 位" },
          ]}
        >
          <Input
            prefix={<KeyOutlined />}
            placeholder="请输入平台发放的邀请码"
            autoComplete="one-time-code"
          />
        </Form.Item>

        <Form.Item
          name="name"
          label="品牌或企业名称"
          rules={[{ required: true, message: "请输入品牌或企业名称" }]}
        >
          <Input prefix={<ShopOutlined />} autoComplete="organization" />
        </Form.Item>

        <Form.Item name="industry" label="所属行业（可选）">
          <Input placeholder="例如：食品饮料" />
        </Form.Item>

        <Form.Item
          name="admin_name"
          label="管理员姓名"
          rules={[{ required: true, message: "请输入管理员姓名" }]}
        >
          <Input prefix={<UserOutlined />} autoComplete="name" />
        </Form.Item>

        <Form.Item
          name="admin_email"
          label="管理员邮箱"
          rules={[
            { required: true, message: "请输入管理员邮箱" },
            { type: "email", message: "请输入有效的邮箱地址" },
          ]}
        >
          <Input prefix={<MailOutlined />} autoComplete="email" />
        </Form.Item>

        <Form.Item
          name="admin_password"
          label="设置登录密码"
          rules={[
            { required: true, message: "请设置登录密码" },
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
          label="确认登录密码"
          dependencies={["admin_password"]}
          rules={[
            { required: true, message: "请再次输入登录密码" },
            ({ getFieldValue }) => ({
              validator(_, value) {
                if (!value || getFieldValue("admin_password") === value) {
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
          提交并开通品牌账号
        </Button>
      </Form>

      <div className="mt-5 text-center">
        <Text type="secondary">
          已有账号？<Link href="/login">返回登录</Link>
        </Text>
      </div>
    </Card>
  );
}

export default function RegisterPage() {
  return (
    <div
      className="flex min-h-screen items-center justify-center px-4 py-8"
      style={{ background: "var(--admin-bg-layout)" }}
    >
      <Suspense fallback={<Spin size="large" />}>
        <RegisterForm />
      </Suspense>
    </div>
  );
}
