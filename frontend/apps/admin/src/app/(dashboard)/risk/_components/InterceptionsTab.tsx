"use client";

import { useCrud } from "@/lib/hooks";
import { STATUS_COLORS, STATUS_TOKEN_COLORS } from "@/lib/status-colors";
import {
  Alert,
  Badge,
  Button,
  Descriptions,
  Modal,
  Switch,
  Table,
  Tag,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useState } from "react";

type Interception = Record<string, unknown> & {
  id: string;
  risk_rule_id: string;
  campaign_id?: string;
  action: string;
  auto_triggered: boolean;
  action_taken?: string;
  action_detail?: {
    steps?: Array<{ action: string; status: string; error?: string }>;
  };
  context?: Record<string, unknown>;
  consumer_id?: string;
};

const columns: ColumnsType<Interception> = [
  {
    title: "规则 ID",
    dataIndex: "risk_rule_id",
    key: "risk_rule_id",
    width: 100,
    render: (v: string) => v?.slice(0, 8) + "...",
  },
  {
    title: "活动 ID",
    dataIndex: "campaign_id",
    key: "campaign_id",
    width: 100,
    render: (v: string) => (v ? v.slice(0, 8) + "..." : "—"),
  },
  {
    title: "动作",
    dataIndex: "action",
    key: "action",
    width: 80,
    render: (a: string) => (
      <Tag color={a === "block" ? STATUS_COLORS.error : STATUS_COLORS.warning}>
        {a === "block" ? "拦截" : "预警"}
      </Tag>
    ),
  },
  {
    title: "触发方式",
    dataIndex: "auto_triggered",
    key: "auto_triggered",
    width: 100,
    render: (v: boolean) =>
      v ? (
        <Badge color={STATUS_TOKEN_COLORS.processing} text="自动" />
      ) : (
        <Badge color={STATUS_TOKEN_COLORS.neutral} text="手动" />
      ),
  },
  {
    title: "处置动作",
    dataIndex: "action_taken",
    key: "action_taken",
    width: 100,
    render: (v: string) => {
      if (!v) return "—";
      const map: Record<string, { color: string; label: string }> = {
        block: { color: STATUS_COLORS.error, label: "冻结+暂停" },
        warn: { color: STATUS_COLORS.warning, label: "预警通知" },
      };
      const info = map[v] || { color: STATUS_COLORS.neutral, label: v };
      return <Tag color={info.color}>{info.label}</Tag>;
    },
  },
  {
    title: "消费者",
    dataIndex: "consumer_id",
    key: "consumer_id",
    width: 100,
    render: (v: string) => v?.slice(0, 8) + "..." || "—",
  },
];

export function InterceptionsTab() {
  const [autoOnly, setAutoOnly] = useState(false);
  const { items, total, page, loading, setPage, setFilter, error, retry } =
    useCrud<Interception>("/risk-rules/interceptions");
  const [detail, setDetail] = useState<Interception | null>(null);

  const handleAutoToggle = (checked: boolean) => {
    setAutoOnly(checked);
    if (checked) {
      setFilter({ auto_triggered: "true" });
    } else {
      setFilter({});
    }
  };

  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        title="拦截记录加载失败"
        action={
          <Button size="small" onClick={() => void retry()}>
            重试
          </Button>
        }
      />
    );
  }

  return (
    <>
      <div className="flex items-center gap-4 mb-4">
        <span className="text-sm text-text-muted">只看自动触发</span>
        <Switch size="small" checked={autoOnly} onChange={handleAutoToggle} />
      </div>
      <Table
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={loading}
        onRow={(record) => ({
          onClick: () => setDetail(record),
          style: { cursor: "pointer" },
        })}
        pagination={{
          current: page,
          total,
          pageSize: 20,
          onChange: setPage,
          showTotal: (t) => `共 ${t} 条`,
        }}
      />
      <Modal
        open={!!detail}
        title="拦截记录详情"
        onCancel={() => setDetail(null)}
        footer={null}
        width={600}
      >
        {detail && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="ID">
              {detail.id.slice(0, 12)}...
            </Descriptions.Item>
            <Descriptions.Item label="触发方式">
              {detail.auto_triggered ? "自动触发" : "手动调用"}
            </Descriptions.Item>
            <Descriptions.Item label="处置动作">
              {detail.action_taken || "无"}
            </Descriptions.Item>
            {detail.action_detail?.steps?.map((step, i) => (
              <Descriptions.Item
                key={i}
                label={`步骤 ${i + 1}: ${step.action}`}
              >
                <Tag
                  color={
                    step.status === "success"
                      ? STATUS_COLORS.success
                      : step.status === "failed"
                        ? STATUS_COLORS.error
                        : STATUS_COLORS.neutral
                  }
                >
                  {step.status}
                </Tag>
                {step.error && (
                  <span
                    className="text-xs ml-2"
                    style={{ color: STATUS_TOKEN_COLORS.error }}
                  >
                    {step.error}
                  </span>
                )}
              </Descriptions.Item>
            ))}
            {detail.context && (
              <Descriptions.Item label="评估上下文">
                <pre className="text-xs max-h-40 overflow-auto">
                  {JSON.stringify(detail.context, null, 2)}
                </pre>
              </Descriptions.Item>
            )}
          </Descriptions>
        )}
      </Modal>
    </>
  );
}
