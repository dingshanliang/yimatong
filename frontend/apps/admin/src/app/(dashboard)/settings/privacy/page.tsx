"use client";

import { DownloadOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  Alert,
  App,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import dayjs from "dayjs";
import { useCallback, useEffect, useState } from "react";

import api from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { STATUS_COLORS } from "@/lib/status-colors";

const { Text, Title } = Typography;

type RightsRequest = {
  id: string;
  request_number: string;
  request_type: string;
  status: string;
  owner_account_id?: string | null;
  due_at: string;
  overdue: boolean;
  outcome?: string | null;
};

type SensitiveExport = {
  id: string;
  requester_account_id: string;
  approver_account_id?: string | null;
  status: string;
  reason: string;
  recipient_purpose: string;
  requested_fields: string[];
  row_count?: number | null;
  expires_at?: string | null;
  downloaded_at?: string | null;
};

const requestTypeLabel: Record<string, string> = {
  access: "查阅",
  copy: "复制",
  correct: "更正",
  delete: "删除",
  restrict: "限制处理",
  withdraw_consent: "撤回同意",
  close_membership: "注销会员",
};
const statusLabel: Record<string, string> = {
  submitted: "待核验",
  assigned: "已分派",
  restricted: "已限制",
  completed: "已完成",
  rejected: "已拒绝",
  pending_approval: "待第二人审批",
  approved: "已批准待生成",
  prepared: "可一次下载",
  downloaded: "已下载",
  deleted: "已删除",
};

export default function PrivacyGovernancePage() {
  const user = useAuthStore((state) => state.user);
  const { message } = App.useApp();
  const [rights, setRights] = useState<RightsRequest[]>([]);
  const [exports, setExports] = useState<SensitiveExport[]>([]);
  const [loading, setLoading] = useState(true);
  const [requestOpen, setRequestOpen] = useState(false);
  const [requestForm] = Form.useForm();
  const [token, setToken] = useState<{
    exportId: string;
    value: string;
  } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [rightsResponse, exportsResponse] = await Promise.all([
        api.get("/privacy/rights-requests"),
        api.get("/privacy/sensitive-exports"),
      ]);
      setRights(rightsResponse.data.items || []);
      setExports(exportsResponse.data.items || []);
    } catch {
      message.error("隐私治理队列暂时无法更新");
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void load();
  }, [load]);

  const actOnRequest = async (
    item: RightsRequest,
    action: "assign" | "restrict" | "complete"
  ) => {
    const outcome =
      action === "complete"
        ? "已完成核验并按申请范围处理，证据见本申请事件记录。"
        : undefined;
    try {
      await api.patch(`/privacy/rights-requests/${item.id}`, {
        action,
        reason:
          action === "assign"
            ? "隐私负责人认领处理"
            : action === "restrict"
              ? "核验期间先限制日常运营处理"
              : "处理完成",
        owner_account_id: action === "assign" ? user?.account_id : undefined,
        outcome,
        evidence: { request_number: item.request_number },
      });
      message.success("权利申请状态已更新并留痕");
      await load();
    } catch {
      message.error("当前账号无权执行该隐私操作，或状态已变化");
    }
  };

  const requestExport = async (values: {
    reason: string;
    recipient_purpose: string;
  }) => {
    try {
      await api.post("/privacy/sensitive-exports", {
        ...values,
        requested_fields: ["membership_number", "nickname", "phone"],
        filters: { membership_status: "active" },
      });
      message.success("敏感导出已提交，等待第二名授权人员审批");
      setRequestOpen(false);
      requestForm.resetFields();
      await load();
    } catch {
      message.error("敏感导出申请失败");
    }
  };

  const exportAction = async (
    item: SensitiveExport,
    action: "approve" | "prepare"
  ) => {
    try {
      const { data } = await api.post(
        `/privacy/sensitive-exports/${item.id}/${action}`,
        {
          reason:
            action === "approve"
              ? "独立复核处理目的、范围和字段后批准"
              : "审批通过后生成加密一次性文件",
        }
      );
      if (action === "prepare")
        setToken({ exportId: item.id, value: data.download_token });
      message.success(
        action === "approve"
          ? "已完成独立审批"
          : "文件已加密生成，请妥善保存本次下载令牌"
      );
      await load();
    } catch {
      message.error(
        action === "approve"
          ? "审批失败：申请人不能自批且需独立权限"
          : "文件生成失败"
      );
    }
  };

  const download = async () => {
    if (!token) return;
    try {
      const response = await api.post<Blob>(
        `/privacy/sensitive-exports/${token.exportId}/download`,
        { download_token: token.value, reason: "申请人登录后执行一次性下载" },
        { responseType: "blob" }
      );
      const url = URL.createObjectURL(response.data);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `member-sensitive-${token.exportId}.csv`;
      anchor.click();
      URL.revokeObjectURL(url);
      setToken(null);
      message.success("下载完成，本次令牌已失效");
      await load();
    } catch {
      message.error("下载失败或令牌已经使用/过期");
    }
  };

  const rightsColumns: ColumnsType<RightsRequest> = [
    { title: "受理号", dataIndex: "request_number" },
    {
      title: "申请事项",
      dataIndex: "request_type",
      render: (value) => requestTypeLabel[value] || value,
    },
    {
      title: "状态",
      dataIndex: "status",
      render: (value) => <Tag>{statusLabel[value] || value}</Tag>,
    },
    {
      title: "截止时间",
      render: (_, item) => (
        <Text type={item.overdue ? "danger" : undefined}>
          {dayjs(item.due_at).format("YYYY-MM-DD")}
          {item.overdue ? " · 已超期" : ""}
        </Text>
      ),
    },
    {
      title: "负责人",
      render: (_, item) => (item.owner_account_id ? "已分派" : "待分派"),
    },
    {
      title: "操作",
      render: (_, item) => (
        <Space size={4}>
          {!item.owner_account_id && (
            <Button
              size="small"
              onClick={() => void actOnRequest(item, "assign")}
            >
              分派给我
            </Button>
          )}
          {!["completed", "rejected"].includes(item.status) && (
            <Button
              size="small"
              type="link"
              onClick={() => void actOnRequest(item, "restrict")}
            >
              限制处理
            </Button>
          )}
          {!["completed", "rejected"].includes(item.status) && (
            <Button
              size="small"
              type="link"
              onClick={() => void actOnRequest(item, "complete")}
            >
              完成
            </Button>
          )}
        </Space>
      ),
    },
  ];

  const exportColumns: ColumnsType<SensitiveExport> = [
    { title: "申请原因", dataIndex: "reason" },
    { title: "接收用途", dataIndex: "recipient_purpose" },
    {
      title: "字段",
      render: (_, item) =>
        item.requested_fields
          .map((field) => (field === "phone" ? "完整手机号" : field))
          .join("、"),
    },
    {
      title: "状态",
      dataIndex: "status",
      render: (value) => (
        <Tag
          color={
            value === "prepared"
              ? STATUS_COLORS.warning
              : value === "downloaded"
                ? STATUS_COLORS.success
                : undefined
          }
        >
          {statusLabel[value] || value}
        </Tag>
      ),
    },
    { title: "行数", render: (_, item) => item.row_count ?? "—" },
    {
      title: "操作",
      render: (_, item) => (
        <Space size={4}>
          {item.status === "pending_approval" &&
            item.requester_account_id !== user?.account_id && (
              <Button
                size="small"
                onClick={() => void exportAction(item, "approve")}
              >
                独立审批
              </Button>
            )}
          {item.status === "approved" &&
            item.requester_account_id === user?.account_id && (
              <Button
                size="small"
                onClick={() => void exportAction(item, "prepare")}
              >
                生成加密文件
              </Button>
            )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4">
        <Title level={4} className="!mb-1">
          个人信息与敏感导出
        </Title>
        <Text type="secondary">
          受理品牌范围权利申请；完整 PII
          和批量敏感导出使用独立权限、双人审批与一次性下载。
        </Text>
      </div>
      <Alert
        className="mb-4"
        showIcon
        type="info"
        title="页面默认只展示脱敏信息。依法需继续保存的数据会限制访问，不会恢复用于营销。"
      />
      <Tabs
        items={[
          {
            key: "rights",
            label: `权利申请 ${rights.filter((item) => !["completed", "rejected"].includes(item.status)).length}`,
            children: (
              <Card size="small">
                <Table
                  rowKey="id"
                  columns={rightsColumns}
                  dataSource={rights}
                  loading={loading}
                  size="small"
                />
              </Card>
            ),
          },
          {
            key: "exports",
            label: "敏感导出",
            children: (
              <Card
                size="small"
                extra={
                  <Button
                    icon={<SafetyCertificateOutlined />}
                    onClick={() => setRequestOpen(true)}
                  >
                    申请敏感导出
                  </Button>
                }
              >
                <Table
                  rowKey="id"
                  columns={exportColumns}
                  dataSource={exports}
                  loading={loading}
                  size="small"
                />
              </Card>
            ),
          },
        ]}
      />
      <Modal
        title="申请敏感会员导出"
        open={requestOpen}
        onCancel={() => setRequestOpen(false)}
        onOk={() => requestForm.submit()}
      >
        <Alert
          className="mb-4"
          type="warning"
          showIcon
          title="完整手机号导出必须由另一名具备审批权限的人员批准；文件 24 小时后删除。"
        />
        <Form form={requestForm} layout="vertical" onFinish={requestExport}>
          <Form.Item
            name="reason"
            label="业务原因"
            rules={[{ required: true, min: 2 }]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item
            name="recipient_purpose"
            label="接收用途"
            rules={[{ required: true, min: 2 }]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title="一次性下载"
        open={Boolean(token)}
        onCancel={() => setToken(null)}
        footer={
          <Button
            type="primary"
            icon={<DownloadOutlined />}
            onClick={() => void download()}
          >
            立即下载并使令牌失效
          </Button>
        }
      >
        <Alert
          type="warning"
          showIcon
          title="令牌只在当前响应中显示。不要通过聊天、邮件或 URL 传递。"
        />
        <Input
          className="mt-4"
          readOnly
          value={token?.value}
          aria-label="一次性下载令牌"
        />
      </Modal>
    </div>
  );
}
