"use client";

import { useState } from "react";
import { Alert, App, Button, Form, Input, Modal, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import api, { extractErrorMessage } from "@/lib/api";
import { useCrud } from "@/lib/hooks";
import type { RiskAccess } from "@/lib/risk-access";
import { STATUS_COLORS } from "@/lib/status-colors";

type CampaignPause = Record<string, unknown> & {
  id: string;
  campaign_id: string;
  risk_rule_id: string;
  prior_status: string;
  status: "active" | "resumed";
  version: number;
  paused_at?: string;
  resumed_at?: string;
  resume_reason?: string;
};

const CAMPAIGN_STATUS_LABELS: Record<string, string> = {
  draft: "草稿",
  active: "进行中",
  paused: "已暂停",
  ended: "已结束",
};

function campaignStatusLabel(status: string) {
  return CAMPAIGN_STATUS_LABELS[status] ?? "当前状态";
}

export function PausesTab({ access }: { access: RiskAccess }) {
  const { message } = App.useApp();
  const { items, total, page, loading, setPage, mutate, error, retry } =
    useCrud<CampaignPause>("/risk-rules/pauses", { enabled: access.canRead });
  const [selected, setSelected] = useState<CampaignPause | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm<{ reason: string }>();

  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        title="风控暂停记录加载失败"
        action={
          <Button size="small" onClick={() => void retry()}>
            重试
          </Button>
        }
      />
    );
  }

  const resume = async ({ reason }: { reason: string }) => {
    if (!selected) return;
    setSubmitting(true);
    try {
      const response = await api.post<{ campaign_status: string }>(
        `/risk-rules/pauses/${selected.id}/resume`,
        { expected_version: selected.version, reason },
        { headers: { "Idempotency-Key": crypto.randomUUID() } }
      );
      const campaignStatus = response.data.campaign_status;
      if (campaignStatus === selected.prior_status) {
        message.success(
          `风控暂停已解除，活动已恢复为${campaignStatusLabel(campaignStatus)}`
        );
      } else {
        message.success(
          `风控暂停已解除；活动保留后续人工设置的${campaignStatusLabel(campaignStatus)}状态`
        );
      }
      setSelected(null);
      form.resetFields();
      await mutate();
    } catch (error) {
      message.error(extractErrorMessage(error, "恢复失败，请刷新后重试"));
    } finally {
      setSubmitting(false);
    }
  };

  const columns: ColumnsType<CampaignPause> = [
    { title: "活动", dataIndex: "campaign_id", key: "campaign_id" },
    { title: "触发规则", dataIndex: "risk_rule_id", key: "risk_rule_id" },
    {
      title: "暂停前状态",
      dataIndex: "prior_status",
      key: "prior_status",
      render: (status: string) => campaignStatusLabel(status),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (status: CampaignPause["status"]) => (
        <Tag
          color={
            status === "active" ? STATUS_COLORS.warning : STATUS_COLORS.success
          }
        >
          {status === "active" ? "风险暂停中" : "已恢复"}
        </Tag>
      ),
    },
    {
      title: "操作",
      key: "action",
      render: (_value, pause) =>
        access.canManage && pause.status === "active" ? (
          <Button size="small" onClick={() => setSelected(pause)}>
            恢复活动
          </Button>
        ) : null,
    },
  ];

  return (
    <>
      <Table
        rowKey="id"
        columns={columns}
        dataSource={items}
        loading={loading}
        pagination={{ current: page, total, pageSize: 20, onChange: setPage }}
      />
      <Modal
        title="恢复活动"
        open={selected !== null}
        confirmLoading={submitting}
        onCancel={() => setSelected(null)}
        onOk={() => form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={resume}>
          <Form.Item
            name="reason"
            label="恢复原因"
            rules={[{ required: true, max: 500 }]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
