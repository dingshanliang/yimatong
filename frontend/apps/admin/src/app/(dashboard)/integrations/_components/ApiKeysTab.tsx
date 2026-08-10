"use client";

import { CopyOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  App,
  Button,
  Checkbox,
  DatePicker,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs, { type Dayjs } from "dayjs";
import { useCallback, useEffect, useState } from "react";

import api, { extractErrorMessage } from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { TenantPlanReadOnlyError } from "@/lib/plan-entitlement";
import { STATUS_COLORS } from "@/lib/status-colors";
import {
  API_KEY_ROLE_LABELS,
  API_KEY_ROLE_OPTIONS,
  canManageApiKeys,
  type ApiKeyRole,
} from "./constants";

const { Text } = Typography;

export interface ApiKeyListItem {
  id: string;
  name: string;
  key_prefix: string;
  role: ApiKeyRole;
  permissions: string[];
  revoked: boolean;
  expires_at: string | null;
  last_used_at: string | null;
}

interface ApiKeyListPage {
  items: ApiKeyListItem[];
  total: number;
  page: number;
  page_size: number;
}

interface IssuedApiKey extends Omit<
  ApiKeyListItem,
  "revoked" | "last_used_at"
> {
  key: string;
}

export interface ApiKeyFormValues {
  name: string;
  role: ApiKeyRole;
  expiry_mode: "90_days" | "custom" | "never";
  expires_at?: Dayjs;
  acknowledge_permanent?: boolean;
  permanent_reason?: string;
}

interface OneTimeSecret {
  kind: "created" | "rotated";
  name: string;
  key: string;
  keyPrefix: string;
}

interface ApiErrorShape {
  response?: {
    status?: number;
    data?: { code?: string; detail?: unknown };
  };
}

export function apiKeyFailureMessage(error: unknown, action: string): string {
  if (error instanceof TenantPlanReadOnlyError) {
    return "当前套餐已到期，只能吊销已有密钥；续期后才能新建或轮换。";
  }
  const response = (error as ApiErrorShape)?.response;
  if (response?.status === 401) {
    return "登录状态或租户状态已失效，请重新登录后重试。";
  }
  if (
    response?.status === 403 &&
    response.data?.code === "TENANT_PLAN_EXPIRED"
  ) {
    return "当前套餐已到期，只能吊销已有密钥；续期后才能新建或轮换。";
  }
  if (response?.status === 403) {
    return "当前账号没有管理 API 密钥的权限。";
  }
  if (response?.status === 429) {
    return "API 密钥操作过于频繁，请等待一分钟后重试。";
  }
  if (response?.status === 503) {
    return "API 密钥安全服务暂时不可用，请稍后使用同一操作重试。";
  }
  if (response?.status === 409) {
    if (
      typeof response.data?.detail === "string" &&
      response.data.detail.includes("active limit")
    ) {
      return "当前租户已达到 20 把有效 API 密钥上限，请先吊销不再使用的密钥。";
    }
    return "密钥状态已变化，请刷新列表后重试。";
  }
  return extractErrorMessage(error, `${action}失败，请稍后重试。`);
}

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format("YYYY-MM-DD HH:mm") : "—";
}

function expiresAtFromForm(values: ApiKeyFormValues): string | null {
  if (values.expiry_mode === "never") return null;
  if (values.expiry_mode === "custom") {
    return values.expires_at?.toISOString() ?? null;
  }
  return dayjs().add(90, "day").toISOString();
}

export function apiKeyCreatePayload(values: ApiKeyFormValues): {
  name: string;
  role: ApiKeyRole;
  expires_at: string | null;
  permanent_acknowledged?: true;
  permanent_reason?: string;
} {
  if (!values.name.trim()) {
    throw new Error("API key purpose name is required");
  }
  if (values.expiry_mode === "never" && !values.acknowledge_permanent) {
    throw new Error("Permanent API key risk must be acknowledged");
  }
  const permanentReason = values.permanent_reason?.trim() ?? "";
  if (
    values.expiry_mode === "never" &&
    (permanentReason.length < 10 || permanentReason.length > 200)
  ) {
    throw new Error("Permanent API key reason must be 10 to 200 characters");
  }
  if (
    values.expiry_mode === "custom" &&
    (!values.expires_at ||
      !values.expires_at.isAfter(dayjs()) ||
      values.expires_at.isAfter(dayjs().add(365, "day")))
  ) {
    throw new Error("API key expiry must be within the next 365 days");
  }
  const payload = {
    name: values.name.trim(),
    role: values.role,
    expires_at: expiresAtFromForm(values),
  };
  return values.expiry_mode === "never"
    ? {
        ...payload,
        permanent_acknowledged: true,
        permanent_reason: permanentReason,
      }
    : payload;
}

export function ApiKeysTab() {
  const { message } = App.useApp();
  const user = useAuthStore((state) => state.user);
  const canManage = canManageApiKeys(user);
  const [items, setItems] = useState<ApiKeyListItem[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [lifecycleError, setLifecycleError] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createIdempotencyKey, setCreateIdempotencyKey] = useState<
    string | null
  >(null);
  const [rotateIdempotencyKeys, setRotateIdempotencyKeys] = useState<
    Record<string, string>
  >({});
  const [submitting, setSubmitting] = useState(false);
  const [busyKeyId, setBusyKeyId] = useState<string | null>(null);
  const [oneTimeSecret, setOneTimeSecret] = useState<OneTimeSecret | null>(
    null
  );
  const [form] = Form.useForm<ApiKeyFormValues>();
  const expiryMode = Form.useWatch("expiry_mode", form) ?? "90_days";

  const loadKeys = useCallback(async () => {
    if (!canManage) return;
    setLoading(true);
    setLoadError(null);
    try {
      const { data } = await api.get<ApiKeyListPage>(
        `/webhooks/api-keys?page=${page}&page_size=${pageSize}`
      );
      setItems(data.items);
      setTotal(data.total);
    } catch (error) {
      setLoadError(apiKeyFailureMessage(error, "加载 API 密钥"));
    } finally {
      setLoading(false);
    }
  }, [canManage, page, pageSize]);

  useEffect(() => {
    void loadKeys();
  }, [loadKeys]);

  if (!canManage) return null;

  const handleCreate = async (values: ApiKeyFormValues) => {
    setSubmitting(true);
    setCreateError(null);
    const attemptKey = createIdempotencyKey ?? crypto.randomUUID();
    if (createIdempotencyKey == null) setCreateIdempotencyKey(attemptKey);
    try {
      const { data } = await api.post<IssuedApiKey>(
        "/webhooks/api-keys",
        apiKeyCreatePayload(values),
        { headers: { "Idempotency-Key": attemptKey } }
      );
      setOneTimeSecret({
        kind: "created",
        name: data.name,
        key: data.key,
        keyPrefix: data.key_prefix,
      });
      setCreateOpen(false);
      setCreateIdempotencyKey(null);
      form.resetFields();
      await loadKeys();
    } catch (error) {
      setCreateError(apiKeyFailureMessage(error, "创建 API 密钥"));
    } finally {
      setSubmitting(false);
    }
  };

  const handleRotate = async (item: ApiKeyListItem) => {
    setBusyKeyId(item.id);
    setLifecycleError(null);
    const attemptKey = rotateIdempotencyKeys[item.id] ?? crypto.randomUUID();
    if (rotateIdempotencyKeys[item.id] == null) {
      setRotateIdempotencyKeys((current) => ({
        ...current,
        [item.id]: attemptKey,
      }));
    }
    try {
      const { data } = await api.post<IssuedApiKey>(
        `/webhooks/api-keys/${item.id}/rotate`,
        undefined,
        { headers: { "Idempotency-Key": attemptKey } }
      );
      setOneTimeSecret({
        kind: "rotated",
        name: data.name,
        key: data.key,
        keyPrefix: data.key_prefix,
      });
      setRotateIdempotencyKeys((current) => {
        const next = { ...current };
        delete next[item.id];
        return next;
      });
      await loadKeys();
    } catch (error) {
      setLifecycleError(apiKeyFailureMessage(error, "轮换 API 密钥"));
    } finally {
      setBusyKeyId(null);
    }
  };

  const handleRevoke = async (item: ApiKeyListItem) => {
    setBusyKeyId(item.id);
    setLifecycleError(null);
    try {
      await api.delete(`/webhooks/api-keys/${item.id}`);
      message.success("API 密钥已吊销");
      await loadKeys();
    } catch (error) {
      setLifecycleError(apiKeyFailureMessage(error, "吊销 API 密钥"));
    } finally {
      setBusyKeyId(null);
    }
  };

  const copySecret = async () => {
    if (!oneTimeSecret) return;
    try {
      await navigator.clipboard.writeText(oneTimeSecret.key);
      message.success("一次性密钥已复制");
    } catch {
      message.warning(
        "浏览器未允许复制，请手动选择密钥复制。把它保存到安全的凭证管理工具中。"
      );
    }
  };

  const columns: ColumnsType<ApiKeyListItem> = [
    { title: "名称", dataIndex: "name", key: "name", ellipsis: true },
    {
      title: "用途权限",
      dataIndex: "role",
      key: "role",
      render: (role: ApiKeyRole) => (
        <Tag color={STATUS_COLORS.processing}>
          {API_KEY_ROLE_LABELS[role] ?? role}
        </Tag>
      ),
    },
    {
      title: "密钥标识",
      dataIndex: "key_prefix",
      key: "key_prefix",
      render: (prefix: string) => <Text code>{prefix}</Text>,
    },
    {
      title: "到期时间",
      dataIndex: "expires_at",
      key: "expires_at",
      render: (value: string | null) =>
        value ? (
          formatDateTime(value)
        ) : (
          <Tag color={STATUS_COLORS.warning}>永久有效</Tag>
        ),
    },
    {
      title: "最近使用",
      dataIndex: "last_used_at",
      key: "last_used_at",
      render: formatDateTime,
    },
    {
      title: "操作",
      key: "actions",
      width: 156,
      render: (_, item) => (
        <Space size={4}>
          <Popconfirm
            title="轮换后旧密钥立即失效"
            description="请确认调用方可以马上更新为新密钥。"
            okText="确认轮换"
            cancelText="取消"
            onConfirm={() => handleRotate(item)}
          >
            <Button size="small" loading={busyKeyId === item.id}>
              轮换
            </Button>
          </Popconfirm>
          <Popconfirm
            title="吊销后无法恢复"
            description="使用此密钥的外部系统会立即停止访问。"
            okText="确认吊销"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => handleRevoke(item)}
          >
            <Button danger size="small" loading={busyKeyId === item.id}>
              吊销
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Space orientation="vertical" size="middle" className="w-full">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Text type="secondary">
          为外部系统分配最小必要权限。密钥原文只在创建或轮换成功后展示一次。
        </Text>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            setCreateError(null);
            setCreateIdempotencyKey(crypto.randomUUID());
            setCreateOpen(true);
          }}
        >
          新建 API 密钥
        </Button>
      </div>

      {loadError && (
        <Alert
          type="error"
          showIcon
          title="API 密钥列表加载失败"
          description={loadError}
          action={
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => void loadKeys()}
            >
              重新加载
            </Button>
          }
        />
      )}
      {lifecycleError && (
        <Alert
          type="warning"
          showIcon
          closable
          title="API 密钥操作未完成"
          description={lifecycleError}
          onClose={() => setLifecycleError(null)}
        />
      )}

      {!loadError && !loading && total === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="还没有 API 密钥"
        >
          <Button
            type="primary"
            onClick={() => {
              setCreateIdempotencyKey(crypto.randomUUID());
              setCreateOpen(true);
            }}
          >
            创建第一把密钥
          </Button>
        </Empty>
      ) : (
        <Table<ApiKeyListItem>
          columns={columns}
          dataSource={items}
          rowKey="id"
          loading={loading}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            onChange: (nextPage, nextPageSize) => {
              setPage(nextPageSize === pageSize ? nextPage : 1);
              setPageSize(nextPageSize);
            },
          }}
          size="middle"
        />
      )}

      <Modal
        title="新建 API 密钥"
        open={createOpen}
        confirmLoading={submitting}
        okText="创建密钥"
        cancelText="取消"
        onCancel={() => {
          setCreateOpen(false);
          setCreateError(null);
          setCreateIdempotencyKey(null);
          form.resetFields();
        }}
        onOk={() => form.submit()}
        destroyOnHidden
        width={520}
      >
        {createError && (
          <Alert
            type="warning"
            showIcon
            title="API 密钥未创建"
            description={createError}
            className="mb-4"
          />
        )}
        <Form<ApiKeyFormValues>
          form={form}
          layout="vertical"
          onFinish={handleCreate}
          initialValues={{ role: "data_reader", expiry_mode: "90_days" }}
        >
          <Form.Item
            name="name"
            label="用途名称"
            rules={[
              { required: true, whitespace: true, message: "请输入用途名称" },
            ]}
          >
            <Input placeholder="例如：CRM 数据同步" maxLength={100} />
          </Form.Item>
          <Form.Item
            name="role"
            label="用途权限"
            rules={[{ required: true, message: "请选择用途权限" }]}
          >
            <Select options={[...API_KEY_ROLE_OPTIONS]} />
          </Form.Item>
          <Form.Item
            name="expiry_mode"
            label="有效期"
            rules={[{ required: true }]}
          >
            <Select
              options={[
                { value: "90_days", label: "90 天（建议）" },
                { value: "custom", label: "指定到期时间" },
                { value: "never", label: "永久有效" },
              ]}
            />
          </Form.Item>
          {expiryMode === "custom" && (
            <Form.Item
              name="expires_at"
              label="到期时间"
              rules={[
                { required: true, message: "请选择到期时间" },
                {
                  validator: (_, value: Dayjs | undefined) =>
                    value?.isAfter(dayjs()) &&
                    !value.isAfter(dayjs().add(365, "day"))
                      ? Promise.resolve()
                      : Promise.reject(
                          new Error("到期时间必须晚于当前时间且不超过 365 天")
                        ),
                },
              ]}
            >
              <DatePicker
                showTime
                className="w-full"
                maxDate={dayjs().add(365, "day")}
                disabledDate={(current) =>
                  current != null && current.endOf("day").isBefore(dayjs())
                }
              />
            </Form.Item>
          )}
          {expiryMode === "never" && (
            <Alert
              type="warning"
              showIcon
              className="mb-4"
              title="永久密钥会长期扩大泄露风险"
              description={
                "只有无法定期轮换的受控系统才应使用；请设置外部凭证保管、访问监控和人工轮换计划。"
              }
            />
          )}
          {expiryMode === "never" && (
            <Form.Item
              name="permanent_reason"
              label="永久使用原因"
              rules={[
                {
                  required: true,
                  whitespace: true,
                  message: "请输入永久使用原因",
                },
                { min: 10, max: 200, message: "请输入 10–200 个字符" },
              ]}
            >
              <Input.TextArea rows={3} maxLength={200} showCount />
            </Form.Item>
          )}
          {expiryMode === "never" && (
            <Form.Item
              name="acknowledge_permanent"
              valuePropName="checked"
              rules={[
                {
                  validator: (_, checked) =>
                    checked
                      ? Promise.resolve()
                      : Promise.reject(new Error("请确认已了解永久密钥风险")),
                },
              ]}
            >
              <Checkbox>我已了解风险并确认使用永久密钥</Checkbox>
            </Form.Item>
          )}
        </Form>
      </Modal>

      <Modal
        title={
          oneTimeSecret?.kind === "rotated" ? "新密钥已生成" : "API 密钥已创建"
        }
        open={oneTimeSecret != null}
        closable={false}
        keyboard={false}
        mask={{ closable: false }}
        destroyOnHidden
        footer={
          <Space>
            <Button icon={<CopyOutlined />} onClick={() => void copySecret()}>
              复制密钥
            </Button>
            <Button type="primary" onClick={() => setOneTimeSecret(null)}>
              我已安全保存，关闭
            </Button>
          </Space>
        }
      >
        <Alert
          type="warning"
          showIcon
          title="这是唯一一次显示完整密钥"
          description="关闭后无法再次查看。如未保存，请先复制到安全的凭证管理工具。"
          className="mb-4"
        />
        <Space orientation="vertical" size="small" className="w-full">
          <Text type="secondary">
            {oneTimeSecret?.name} · 标识 {oneTimeSecret?.keyPrefix}
          </Text>
          <Input
            aria-label="一次性 API 密钥"
            value={oneTimeSecret?.key ?? ""}
            readOnly
          />
        </Space>
      </Modal>
    </Space>
  );
}
