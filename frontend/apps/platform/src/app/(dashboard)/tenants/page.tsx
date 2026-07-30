"use client";

import { useMemo, useState } from "react";
import {
  App,
  Button,
  Card,
  Dropdown,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  PlusOutlined,
  MoreOutlined,
  EyeOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  DeleteOutlined,
  KeyOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import dayjs from "dayjs";
import useSWR from "swr";
import api from "@/lib/api";
import { extractErrorMessage } from "@/lib/api";
import { STATUS_MAP, PLAN_MAP } from "@/lib/constants";

const { Title } = Typography;

interface Tenant {
  id: string;
  name: string;
  slug: string;
  status: "active" | "suspended" | "terminated";
  plan: "free" | "starter" | "pro" | "enterprise";
  plan_expires_at: string | null;
  industry: string | null;
  created_at: string;
}

interface TenantFormValues {
  name: string;
  plan: string;
  industry?: string;
  notes?: string;
  admin_email: string;
  admin_name: string;
}

interface TenantOpeningResponse extends Tenant {
  initial_admin_state: "pending_activation";
  activation_url: string | null;
  activation_retryable: boolean;
}

export default function TenantsPage() {
  const router = useRouter();
  const { modal, message } = App.useApp();

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [planFilter, setPlanFilter] = useState<string | undefined>();
  const [search, setSearch] = useState<string | undefined>();
  const [searchInput, setSearchInput] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);

  const swrKey = useMemo(() => {
    const p = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (statusFilter) p.set("status", statusFilter);
    if (planFilter) p.set("plan", planFilter);
    if (search) p.set("search", search);
    return `/platform/tenants?${p.toString()}`;
  }, [page, pageSize, statusFilter, planFilter, search]);

  const { data, mutate, isLoading } = useSWR<{
    items: Tenant[];
    total: number;
  }>(swrKey);

  const [form] = Form.useForm<TenantFormValues>();

  const handleCreate = async (values: TenantFormValues) => {
    setCreating(true);
    try {
      const { data } = await api.post<TenantOpeningResponse>(
        "/platform/tenants",
        values
      );
      setCreateOpen(false);
      form.resetFields();
      mutate();
      if (data.activation_url) {
        modal.success({
          title: "租户已创建，管理员待激活",
          width: 560,
          content: (
            <div>
              <Typography.Paragraph>
                请通过已核实的微信把下面的一次性链接发给初始管理员，由对方自行设置密码。
              </Typography.Paragraph>
              <Typography.Paragraph copyable={{ text: data.activation_url }}>
                {data.activation_url}
              </Typography.Paragraph>
            </div>
          ),
        });
      } else {
        modal.warning({
          title: "租户已创建，激活链接尚未生成",
          content:
            "租户数据已经完整保存。请稍后在租户详情中重新生成管理员激活链接。",
        });
      }
    } catch (err) {
      message.error(extractErrorMessage(err, "创建失败"));
    } finally {
      setCreating(false);
    }
  };

  const handleStatusChange = async (tenant: Tenant, newStatus: string) => {
    const statusLabels: Record<string, string> = {
      suspended: "暂停",
      active: "恢复",
      terminated: "终止",
    };
    modal.confirm({
      title: `确认${statusLabels[newStatus]}租户`,
      content: `确定要${statusLabels[newStatus]}租户「${tenant.name}」吗？`,
      onOk: async () => {
        try {
          await api.patch(`/platform/tenants/${tenant.id}/status`, {
            status: newStatus,
          });
          message.success("操作成功");
          mutate();
        } catch (err) {
          message.error(extractErrorMessage(err, "操作失败"));
        }
      },
    });
  };

  const handleActivationLink = async (tenant: Tenant) => {
    try {
      const { data } = await api.post<{ activation_url: string }>(
        `/platform/tenants/${tenant.id}/initial-admin-activation`
      );
      modal.success({
        title: "管理员激活链接已生成",
        width: 560,
        content: (
          <div>
            <Typography.Paragraph>
              请通过已核实的微信把链接发给初始管理员。重新生成后，旧链接会立即失效。
            </Typography.Paragraph>
            <Typography.Paragraph copyable={{ text: data.activation_url }}>
              {data.activation_url}
            </Typography.Paragraph>
          </div>
        ),
      });
    } catch (err) {
      message.error(extractErrorMessage(err, "激活链接生成失败"));
    }
  };

  const columns: ColumnsType<Tenant> = [
    {
      title: "名称",
      dataIndex: "name",
      key: "name",
      render: (name: string, record: Tenant) => (
        <a onClick={() => router.push(`/tenants/${record.id}`)}>{name}</a>
      ),
    },
    {
      title: "Slug",
      dataIndex: "slug",
      key: "slug",
      width: 120,
      ellipsis: true,
    },
    {
      title: "套餐",
      dataIndex: "plan",
      key: "plan",
      width: 100,
      render: (plan: string) => {
        const p = PLAN_MAP[plan];
        return <Tag color={p?.color}>{p?.label ?? plan}</Tag>;
      },
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 80,
      render: (status: string) => {
        const s = STATUS_MAP[status];
        return <Tag color={s?.color}>{s?.label ?? status}</Tag>;
      },
    },
    {
      title: "行业",
      dataIndex: "industry",
      key: "industry",
      width: 100,
      ellipsis: true,
    },
    {
      title: "过期时间",
      dataIndex: "plan_expires_at",
      key: "plan_expires_at",
      width: 140,
      render: (v: string | null) =>
        v ? (
          dayjs(v).isBefore(dayjs().add(30, "day")) ? (
            <Tag color="red">{dayjs(v).format("YYYY-MM-DD")}</Tag>
          ) : (
            dayjs(v).format("YYYY-MM-DD")
          )
        ) : (
          <Tag>永久</Tag>
        ),
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 120,
      render: (v: string) => dayjs(v).format("YYYY-MM-DD"),
    },
    {
      title: "操作",
      key: "actions",
      width: 60,
      render: (_: unknown, record: Tenant) => {
        const items: {
          key: string;
          icon: React.ReactNode;
          label: string;
          onClick: () => void;
          danger?: boolean;
        }[] = [
          {
            key: "view",
            icon: <EyeOutlined />,
            label: "查看详情",
            onClick: () => router.push(`/tenants/${record.id}`),
          },
          {
            key: "activation",
            icon: <KeyOutlined />,
            label: "生成管理员激活链接",
            onClick: () => handleActivationLink(record),
          },
        ];
        if (record.status === "active") {
          items.push({
            key: "suspend",
            icon: <PauseCircleOutlined />,
            label: "暂停",
            onClick: () => handleStatusChange(record, "suspended"),
          });
        }
        if (record.status === "suspended") {
          items.push({
            key: "resume",
            icon: <PlayCircleOutlined />,
            label: "恢复",
            onClick: () => handleStatusChange(record, "active"),
          });
        }
        if (record.status !== "terminated") {
          items.push({
            key: "terminate",
            icon: <DeleteOutlined />,
            label: "终止",
            danger: true,
            onClick: () => handleStatusChange(record, "terminated"),
          });
        }
        return (
          <Dropdown menu={{ items }} trigger={["click"]}>
            <Button type="text" size="small" icon={<MoreOutlined />} />
          </Dropdown>
        );
      },
    },
  ];

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 16,
        }}
      >
        <Title level={4} style={{ margin: 0 }}>
          租户管理
        </Title>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setCreateOpen(true)}
        >
          创建租户
        </Button>
      </div>

      <Card>
        <Space style={{ marginBottom: 16 }} wrap>
          <Input.Search
            placeholder="搜索名称或 Slug"
            allowClear
            style={{ width: 250 }}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onSearch={(v) => {
              setSearch(v || undefined);
              setPage(1);
            }}
          />
          <Select
            placeholder="状态筛选"
            allowClear
            style={{ width: 120 }}
            value={statusFilter}
            onChange={(v) => {
              setStatusFilter(v);
              setPage(1);
            }}
            options={Object.entries(STATUS_MAP).map(([k, v]) => ({
              value: k,
              label: v.label,
            }))}
          />
          <Select
            placeholder="套餐筛选"
            allowClear
            style={{ width: 120 }}
            value={planFilter}
            onChange={(v) => {
              setPlanFilter(v);
              setPage(1);
            }}
            options={Object.entries(PLAN_MAP).map(([k, v]) => ({
              value: k,
              label: v.label,
            }))}
          />
        </Space>

        <Table<Tenant>
          rowKey="id"
          columns={columns}
          dataSource={data?.items ?? []}
          loading={isLoading}
          pagination={{
            current: page,
            pageSize,
            total: data?.total,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 个租户`,
            onChange: (p, ps) => {
              setPage(p);
              setPageSize(ps);
            },
          }}
          scroll={{ x: 900 }}
          size="middle"
        />
      </Card>

      <Modal
        title="创建租户"
        open={createOpen}
        onCancel={() => {
          setCreateOpen(false);
          form.resetFields();
        }}
        onOk={() => form.submit()}
        confirmLoading={creating}
        width={560}
        afterOpenChange={(open) => {
          if (!open) form.resetFields();
        }}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item
            name="name"
            label="租户名称"
            rules={[{ required: true, message: "请输入名称" }]}
          >
            <Input placeholder="例：某某食品科技有限公司" />
          </Form.Item>
          <Form.Item name="plan" label="套餐" initialValue="free">
            <Select
              options={Object.entries(PLAN_MAP).map(([k, v]) => ({
                value: k,
                label: v.label,
              }))}
            />
          </Form.Item>
          <Form.Item name="industry" label="行业">
            <Input placeholder="例：食品饮料" />
          </Form.Item>

          <Typography.Text
            strong
            style={{ display: "block", marginBottom: 16 }}
          >
            管理员账号
          </Typography.Text>
          <Form.Item
            name="admin_name"
            label="管理员姓名"
            rules={[{ required: true }]}
          >
            <Input placeholder="例：张三" />
          </Form.Item>
          <Form.Item
            name="admin_email"
            label="管理员邮箱"
            rules={[{ required: true }, { type: "email" }]}
          >
            <Input placeholder="例：admin@example.com" />
          </Form.Item>
          <Typography.Paragraph type="secondary">
            创建后会生成一次性激活链接。管理员通过链接自行设置密码，平台不会接触客户密码。
          </Typography.Paragraph>
        </Form>
      </Modal>
    </div>
  );
}
