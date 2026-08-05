"use client";

import { useState } from "react";
import {
  App,
  Button,
  Form,
  InputNumber,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  CopyOutlined,
  PauseCircleOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import dayjs from "dayjs";
import useSWR from "swr";

import api, { extractErrorMessage } from "@/lib/api";
import { registrationUrl } from "./registration-url";

const { Paragraph, Text, Title } = Typography;

type InviteCodeStatus = "active" | "inactive" | "expired" | "depleted";

interface InviteCode {
  id: string;
  code: string;
  tenant_type: string;
  max_uses: number;
  used_count: number;
  status: InviteCodeStatus;
  expires_at: string | null;
  created_at: string;
  registration_url: string;
}

interface InviteCodeList {
  items: InviteCode[];
  total: number;
  page: number;
  page_size: number;
}

interface CreateInviteValues {
  max_uses: number;
  expires_in_days: number;
}

const STATUS_META: Record<InviteCodeStatus, { label: string; color: string }> =
  {
    active: { label: "可使用", color: "success" },
    inactive: { label: "已停用", color: "default" },
    expired: { label: "已过期", color: "warning" },
    depleted: { label: "已用完", color: "default" },
  };

export function effectiveInviteStatus(invite: InviteCode): InviteCodeStatus {
  if (invite.status !== "active") return invite.status;
  if (invite.used_count >= invite.max_uses) return "depleted";
  if (invite.expires_at && dayjs(invite.expires_at).isBefore(dayjs())) {
    return "expired";
  }
  return "active";
}

export default function InviteCodesPage() {
  const { message, modal } = App.useApp();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm<CreateInviteValues>();

  const listKey = `/invite-codes?page=${page}&page_size=${pageSize}`;

  const { data, isLoading, mutate } = useSWR<InviteCodeList>(listKey);

  const copyRegistrationUrl = async (invite: InviteCode) => {
    try {
      await navigator.clipboard.writeText(registrationUrl(invite));
      message.success("注册链接已复制，可发送给客户");
    } catch {
      message.error("复制失败，请检查浏览器剪贴板权限");
    }
  };

  const handleCreate = async (values: CreateInviteValues) => {
    setCreating(true);
    try {
      const { data: invite } = await api.post<InviteCode>("/invite-codes", {
        tenant_type: "brand",
        max_uses: values.max_uses,
        expires_in_days: values.expires_in_days,
      });
      setCreateOpen(false);
      form.resetFields();
      await mutate();
      modal.success({
        title: "邀请码已创建",
        width: 560,
        content: (
          <div>
            <Paragraph>
              请将下面的注册链接发送给客户。客户完成注册后，可直接使用设置的邮箱和密码登录品牌后台。
            </Paragraph>
            <Paragraph copyable={{ text: registrationUrl(invite) }}>
              {registrationUrl(invite)}
            </Paragraph>
          </div>
        ),
      });
    } catch (error) {
      message.error(extractErrorMessage(error, "邀请码创建失败"));
    } finally {
      setCreating(false);
    }
  };

  const deactivate = (invite: InviteCode) => {
    modal.confirm({
      title: "停用这个邀请码？",
      content: "停用后，尚未完成注册的客户将无法继续使用该链接。",
      okText: "确认停用",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        try {
          await api.patch(`/invite-codes/${invite.id}/status?active=false`);
          message.success("邀请码已停用");
          await mutate();
        } catch (error) {
          message.error(extractErrorMessage(error, "停用失败"));
        }
      },
    });
  };

  const columns: ColumnsType<InviteCode> = [
    {
      title: "邀请码",
      dataIndex: "code",
      key: "code",
      render: (code: string) => <Text code>{code}</Text>,
    },
    {
      title: "状态",
      key: "status",
      width: 100,
      render: (_, invite) => {
        const meta = STATUS_META[effectiveInviteStatus(invite)];
        return <Tag color={meta.color}>{meta.label}</Tag>;
      },
    },
    {
      title: "使用情况",
      key: "usage",
      width: 120,
      render: (_, invite) => `${invite.used_count} / ${invite.max_uses}`,
    },
    {
      title: "有效期至",
      dataIndex: "expires_at",
      key: "expires_at",
      width: 170,
      render: (value: string | null) =>
        value ? dayjs(value).format("YYYY-MM-DD HH:mm") : "长期有效",
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 170,
      render: (value: string) => dayjs(value).format("YYYY-MM-DD HH:mm"),
    },
    {
      title: "操作",
      key: "actions",
      width: 210,
      render: (_, invite) => {
        const inviteStatus = effectiveInviteStatus(invite);
        return (
          <Space size={4}>
            <Button
              type="link"
              icon={<CopyOutlined />}
              disabled={inviteStatus !== "active"}
              onClick={() => copyRegistrationUrl(invite)}
            >
              复制注册链接
            </Button>
            {inviteStatus === "active" && (
              <Button
                type="link"
                danger
                icon={<PauseCircleOutlined />}
                onClick={() => deactivate(invite)}
              >
                停用
              </Button>
            )}
          </Space>
        );
      },
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <Title level={4} className="!mb-1">
            邀请码
          </Title>
          <Text type="secondary">
            为品牌客户创建受控注册链接，并跟踪可用次数与有效期。
          </Text>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setCreateOpen(true)}
        >
          创建邀请码
        </Button>
      </div>

      <Table<InviteCode>
        rowKey="id"
        columns={columns}
        dataSource={data?.items ?? []}
        loading={isLoading}
        scroll={{ x: 980 }}
        pagination={{
          current: page,
          pageSize,
          total: data?.total ?? 0,
          showSizeChanger: true,
          showTotal: (total) => `共 ${total} 个邀请码`,
          onChange: (nextPage, nextPageSize) => {
            setPage(nextPageSize !== pageSize ? 1 : nextPage);
            setPageSize(nextPageSize);
          },
        }}
      />

      <Modal
        title="创建品牌客户邀请码"
        open={createOpen}
        onCancel={() => {
          setCreateOpen(false);
          form.resetFields();
        }}
        onOk={() => form.submit()}
        confirmLoading={creating}
        okText="创建并生成注册链接"
        cancelText="取消"
        destroyOnHidden
      >
        <Paragraph type="secondary">
          邀请码仅用于客户自助开通品牌租户。注册码在达到使用次数或有效期后自动失效。
        </Paragraph>
        <Form<CreateInviteValues>
          form={form}
          layout="vertical"
          initialValues={{ max_uses: 1, expires_in_days: 30 }}
          onFinish={handleCreate}
        >
          <Form.Item
            name="max_uses"
            label="可注册客户数"
            rules={[{ required: true, message: "请输入可注册客户数" }]}
          >
            <InputNumber min={1} max={1000} precision={0} className="w-full" />
          </Form.Item>
          <Form.Item
            name="expires_in_days"
            label="链接有效天数"
            rules={[{ required: true, message: "请输入链接有效天数" }]}
          >
            <InputNumber min={1} max={365} precision={0} className="w-full" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
