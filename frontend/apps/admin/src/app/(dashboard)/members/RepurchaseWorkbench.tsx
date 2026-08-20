"use client";

import { ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  Alert,
  App,
  Button,
  Card,
  DatePicker,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Segmented,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import dayjs, { type Dayjs } from "dayjs";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import api from "@/lib/api";
import { STATUS_COLORS } from "@/lib/status-colors";

const { RangePicker } = DatePicker;
const { Text, Title } = Typography;

type Metric = {
  key: string;
  label: string;
  value: number | null;
  availability: "complete" | "partial" | "unavailable";
  coverage_status: string;
  data_cutoff_at?: string | null;
};

type WorkItem = {
  id: string;
  category: string;
  business_ref: string;
  title: string;
  impact_summary: string;
  priority: "urgent" | "high" | "normal";
  status:
    "pending" | "in_progress" | "waiting_external" | "resolved" | "no_action";
  owner: { name: string; email?: string | null };
  owner_account_id: string;
  due_at: string;
  overdue: boolean;
  conclusion?: string | null;
};

type Workbench = {
  time_basis: "occurrence" | "cohort";
  start_at: string;
  end_at: string;
  maturity_days: number;
  metrics: Metric[];
  operations: Record<string, number>;
  source_coverage: {
    status: "complete" | "partial" | "unavailable";
    active_connections: number;
    incomplete_orders: number;
    data_cutoff_at?: string | null;
  };
  trust_rubric: Array<{ key: string; label: string; included: boolean }>;
  recent_orders: Array<{
    order_ref: string;
    source_system: string;
    status: string;
    net_product_sales_fen: number;
    coverage_status: string;
    occurred_at: string;
  }>;
  work_items: WorkItem[];
};

const categoryLabel: Record<string, string> = {
  coupon_issue_failure: "发券失败",
  coupon_expiry_unreached: "临期未触达",
  notification_failure: "通知失败",
  payment_redemption_conflict: "支付核销冲突",
  refund_unsynced: "退款未同步",
  membership_mapping_conflict: "会员映射冲突",
  unattributed_order: "未归因订单",
  source_coverage: "来源覆盖异常",
};

const statusLabel: Record<string, string> = {
  pending: "待处理",
  in_progress: "处理中",
  waiting_external: "等待外部",
  resolved: "已解决",
  no_action: "无需处理",
};

function metricValue(metric: Metric) {
  if (
    metric.availability === "unavailable" ||
    (metric.availability === "partial" && metric.value === 0)
  )
    return "—";
  if (metric.value === null) return "—";
  if (metric.key === "attributed_net_sales_fen")
    return `¥${(metric.value / 100).toLocaleString("zh-CN")}`;
  if (metric.key.endsWith("percent")) return `${metric.value}%`;
  return metric.value.toLocaleString("zh-CN");
}

export function RepurchaseWorkbench({ members }: { members: ReactNode }) {
  const [data, setData] = useState<Workbench | null>(null);
  const [loading, setLoading] = useState(true);
  const [basis, setBasis] = useState<"occurrence" | "cohort">("occurrence");
  const [range, setRange] = useState<[Dayjs, Dayjs]>([
    dayjs().subtract(30, "day"),
    dayjs(),
  ]);
  const [selected, setSelected] = useState<WorkItem | null>(null);
  const [transitionOpen, setTransitionOpen] = useState(false);
  const [assignmentOpen, setAssignmentOpen] = useState(false);
  const [correctionOpen, setCorrectionOpen] = useState(false);
  const [form] = Form.useForm();
  const [assignmentForm] = Form.useForm();
  const [correctionForm] = Form.useForm();
  const { message } = App.useApp();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data: response } = await api.get(
        "/members/repurchase-workbench",
        {
          params: {
            time_basis: basis,
            start_at: range[0].startOf("day").toISOString(),
            end_at: range[1].endOf("day").toISOString(),
          },
        }
      );
      setData(response);
    } catch {
      message.error("会员复购工作台暂时无法更新");
    } finally {
      setLoading(false);
    }
  }, [basis, message, range]);

  useEffect(() => {
    void load();
  }, [load]);

  const submitTransition = async (values: {
    status: string;
    reason: string;
    conclusion?: string;
  }) => {
    if (!selected) return;
    try {
      await api.patch(
        `/members/repurchase-workbench/work-items/${selected.id}`,
        { ...values, evidence: {} }
      );
      message.success("处置状态已更新并留痕");
      setTransitionOpen(false);
      form.resetFields();
      await load();
    } catch {
      message.error("处置状态更新失败");
    }
  };

  const requestResync = useCallback(
    async (item: WorkItem) => {
      try {
        await api.post(
          `/members/repurchase-workbench/work-items/${item.id}/actions`,
          {
            action: "resync",
            reason: "运营人员从复购工作台请求重新同步",
            before: { status: item.status },
            after: { requested: true },
            evidence: { business_ref: item.business_ref },
          }
        );
        message.success("重新同步请求已受理并留痕");
      } catch {
        message.error("重新同步请求失败");
      }
    },
    [message]
  );

  const submitAssignment = async (values: {
    owner_account_id: string;
    due_at: Dayjs;
    reason: string;
  }) => {
    if (!selected) return;
    try {
      await api.patch(
        `/members/repurchase-workbench/work-items/${selected.id}/assignment`,
        {
          ...values,
          due_at: values.due_at.toISOString(),
          evidence: { business_ref: selected.business_ref },
        }
      );
      message.success("责任人与截止时间已更新并留痕");
      setAssignmentOpen(false);
      assignmentForm.resetFields();
      await load();
    } catch {
      message.error("责任分派更新失败");
    }
  };

  const submitCorrection = async (values: {
    reason: string;
    before: string;
    after: string;
    evidence?: string;
  }) => {
    if (!selected) return;
    try {
      await api.post(
        `/members/repurchase-workbench/work-items/${selected.id}/actions`,
        {
          action: "correction",
          reason: values.reason,
          before: { recorded_value: values.before },
          after: { corrected_value: values.after },
          evidence: { reference: values.evidence || selected.business_ref },
        }
      );
      message.success("受控更正已提交，修改前后值已留痕");
      setCorrectionOpen(false);
      correctionForm.resetFields();
    } catch {
      message.error("受控更正提交失败");
    }
  };

  const taskColumns = useMemo<ColumnsType<WorkItem>>(
    () => [
      {
        title: "处置事项",
        render: (_, item) => (
          <div>
            <div className="font-medium">{item.title}</div>
            <Text type="secondary">
              {categoryLabel[item.category]} · {item.business_ref}
            </Text>
          </div>
        ),
      },
      { title: "影响", dataIndex: "impact_summary", ellipsis: true },
      {
        title: "优先级",
        dataIndex: "priority",
        width: 90,
        render: (value: WorkItem["priority"]) => (
          <Tag
            color={
              value === "urgent"
                ? STATUS_COLORS.error
                : value === "high"
                  ? STATUS_COLORS.warning
                  : STATUS_COLORS.processing
            }
          >
            {value === "urgent" ? "紧急" : value === "high" ? "高" : "普通"}
          </Tag>
        ),
      },
      {
        title: "责任人",
        render: (_, item) => item.owner.name || item.owner.email || "—",
      },
      {
        title: "截止时间",
        width: 165,
        render: (_, item) => (
          <Text type={item.overdue ? "danger" : undefined}>
            {dayjs(item.due_at).format("YYYY-MM-DD HH:mm")}
            {item.overdue ? " · 已超期" : ""}
          </Text>
        ),
      },
      {
        title: "状态",
        width: 100,
        render: (_, item) => <Tag>{statusLabel[item.status]}</Tag>,
      },
      {
        title: "操作",
        width: 260,
        render: (_, item) => (
          <Space size={4}>
            <Button
              size="small"
              onClick={() => {
                setSelected(item);
                setTransitionOpen(true);
              }}
            >
              处置
            </Button>
            <Button
              size="small"
              type="link"
              onClick={() => {
                setSelected(item);
                assignmentForm.setFieldsValue({
                  owner_account_id: item.owner_account_id,
                  due_at: dayjs(item.due_at),
                });
                setAssignmentOpen(true);
              }}
            >
              分派
            </Button>
            <Button
              size="small"
              type="link"
              onClick={() => void requestResync(item)}
            >
              重同步
            </Button>
            <Button
              size="small"
              type="link"
              onClick={() => {
                setSelected(item);
                setCorrectionOpen(true);
              }}
            >
              更正
            </Button>
          </Space>
        ),
      },
    ],
    [assignmentForm, requestResync]
  );

  const coverage = data?.source_coverage.status;
  const coverageMessage =
    coverage === "complete"
      ? "可信交易来源已连接且当前统计范围完整。"
      : coverage === "partial"
        ? "当前只展示已知可信部分；覆盖不完整的数据不会冒充品牌全部经营结果。"
        : "尚无可用于正式交易指标的可信来源；相关指标显示为不可用，不显示为零。";

  const overview = (
    <Space direction="vertical" size={16} className="w-full">
      <Alert
        showIcon
        type={coverage === "complete" ? "success" : "warning"}
        title={coverageMessage}
      />
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-6">
        {data?.metrics.map((metric) => (
          <Card key={metric.key} size="small" loading={loading}>
            <Statistic title={metric.label} value={metricValue(metric)} />
            <Text type="secondary" className="text-xs">
              {metric.availability === "partial"
                ? "已知可信部分"
                : metric.availability === "unavailable"
                  ? "数据不可用"
                  : "正式口径"}
            </Text>
          </Card>
        ))}
      </div>
      <Card
        size="small"
        title="需要处理"
        extra={
          <Text type="secondary">
            {data?.work_items.filter(
              (item) => !["resolved", "no_action"].includes(item.status)
            ).length || 0}{" "}
            项未闭环
          </Text>
        }
      >
        <Table
          columns={taskColumns}
          dataSource={data?.work_items || []}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={{ pageSize: 8 }}
          locale={{ emptyText: "当前没有待处置异常" }}
        />
      </Card>
    </Space>
  );

  const operationCards = (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
      {[
        ["可用复购券", data?.operations.available_coupons],
        ["发券同步失败", data?.operations.coupon_sync_failures],
        ["关键通知失败", data?.operations.notification_failures],
        ["退款事实", data?.operations.refunds],
        ["未归因订单", data?.operations.unattributed_orders],
        ["覆盖异常", data?.operations.coverage_issues],
      ].map(([label, value]) => (
        <Card size="small" key={String(label)}>
          <Statistic title={String(label)} value={Number(value || 0)} />
        </Card>
      ))}
    </div>
  );

  const orderColumns: ColumnsType<Workbench["recent_orders"][number]> = [
    { title: "来源订单号", dataIndex: "order_ref" },
    { title: "来源", dataIndex: "source_system" },
    {
      title: "状态",
      dataIndex: "status",
      render: (value) => <Tag>{value}</Tag>,
    },
    {
      title: "商品净额",
      dataIndex: "net_product_sales_fen",
      render: (value) => `¥${(value / 100).toLocaleString("zh-CN")}`,
    },
    {
      title: "覆盖",
      dataIndex: "coverage_status",
      render: (value) => (
        <Tag
          color={
            value === "complete" ? STATUS_COLORS.success : STATUS_COLORS.warning
          }
        >
          {value === "complete" ? "完整" : "不完整"}
        </Tag>
      ),
    },
    {
      title: "业务发生时间",
      dataIndex: "occurred_at",
      render: (value) => dayjs(value).format("YYYY-MM-DD HH:mm"),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <Title level={4} className="!mb-1">
            会员复购工作台
          </Title>
          <Text type="secondary">
            先处理影响消费者资产与正式指标的异常，再查看经营结果。
          </Text>
        </div>
        <Space wrap>
          <Segmented
            value={basis}
            onChange={(value) => setBasis(value as "occurrence" | "cohort")}
            options={[
              { label: "经营发生", value: "occurrence" },
              { label: "成熟会员批次", value: "cohort" },
            ]}
          />
          <RangePicker
            value={range}
            onChange={(dates) =>
              dates?.[0] && dates?.[1] && setRange([dates[0], dates[1]])
            }
            allowClear={false}
          />
          <Button
            icon={<ReloadOutlined />}
            onClick={() => void load()}
            loading={loading}
          >
            刷新
          </Button>
        </Space>
      </div>
      <Text type="secondary" className="mb-3 block text-xs">
        统计范围：{range[0].format("YYYY-MM-DD")} 至{" "}
        {range[1].format("YYYY-MM-DD")} · 数据截至{" "}
        {data?.source_coverage.data_cutoff_at
          ? dayjs(data.source_coverage.data_cutoff_at).format(
              "YYYY-MM-DD HH:mm"
            )
          : "暂无可信交易事实"}
      </Text>
      <Tabs
        items={[
          { key: "overview", label: "总览与待办", children: overview },
          { key: "members", label: "品牌会员", children: members },
          {
            key: "coupon",
            label: "复购券与触达",
            children: (
              <Space direction="vertical" className="w-full">
                {operationCards}
                <Alert
                  type="info"
                  showIcon
                  title="券与触达是运营过程，不与六项正式经营结果混算。"
                />
              </Space>
            ),
          },
          {
            key: "orders",
            label: "订单与退款",
            children: (
              <Table
                columns={orderColumns}
                dataSource={data?.recent_orders || []}
                rowKey="order_ref"
                loading={loading}
                size="small"
                locale={{
                  emptyText: <Empty description="当前范围暂无可信订单事实" />,
                }}
              />
            ),
          },
          {
            key: "quality",
            label: "归因与数据质量",
            children: (
              <div className="grid gap-4 lg:grid-cols-2">
                <Card size="small" title="来源覆盖">
                  <Descriptions size="small" column={1}>
                    <Descriptions.Item label="覆盖状态">
                      {coverageMessage}
                    </Descriptions.Item>
                    <Descriptions.Item label="已连接来源">
                      {data?.source_coverage.active_connections ?? 0}
                    </Descriptions.Item>
                    <Descriptions.Item label="不完整订单">
                      {data?.source_coverage.incomplete_orders ?? 0}
                    </Descriptions.Item>
                  </Descriptions>
                </Card>
                <Card size="small" title="事实可信度">
                  {data?.trust_rubric.map((item) => (
                    <div
                      key={item.key}
                      className="flex justify-between border-b border-border py-2 last:border-0"
                    >
                      <span>{item.label}</span>
                      <Tag
                        color={
                          item.included
                            ? STATUS_COLORS.success
                            : STATUS_COLORS.neutral
                        }
                      >
                        {item.included ? "进入正式指标" : "仅供参考"}
                      </Tag>
                    </div>
                  ))}
                </Card>
              </div>
            ),
          },
        ]}
      />
      <Modal
        title={selected ? `处置：${selected.business_ref}` : "更新处置"}
        open={transitionOpen}
        onCancel={() => setTransitionOpen(false)}
        onOk={() => form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={submitTransition}>
          <Form.Item
            name="status"
            label="下一状态"
            rules={[{ required: true }]}
          >
            <Segmented
              block
              options={[
                { label: "处理中", value: "in_progress" },
                { label: "等待外部", value: "waiting_external" },
                { label: "已解决", value: "resolved" },
                { label: "无需处理", value: "no_action" },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="reason"
            label="处理原因"
            rules={[{ required: true, min: 2 }]}
          >
            <Input.TextArea
              rows={3}
              placeholder="说明采取了什么行动或为何等待外部"
            />
          </Form.Item>
          <Form.Item name="conclusion" label="最终结论（结束处置时必填）">
            <Input.TextArea rows={3} placeholder="记录结果与可复核证据位置" />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={selected ? `分派：${selected.business_ref}` : "调整责任"}
        open={assignmentOpen}
        onCancel={() => setAssignmentOpen(false)}
        onOk={() => assignmentForm.submit()}
      >
        <Form
          form={assignmentForm}
          layout="vertical"
          onFinish={submitAssignment}
        >
          <Form.Item
            name="owner_account_id"
            label="负责人账号 ID"
            rules={[{ required: true }]}
          >
            <Input placeholder="选择或粘贴当前租户内的负责人账号" />
          </Form.Item>
          <Form.Item
            name="due_at"
            label="截止时间"
            rules={[{ required: true }]}
          >
            <DatePicker showTime className="w-full" />
          </Form.Item>
          <Form.Item
            name="reason"
            label="调整原因"
            rules={[{ required: true, min: 2 }]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={selected ? `受控更正：${selected.business_ref}` : "受控更正"}
        open={correctionOpen}
        onCancel={() => setCorrectionOpen(false)}
        onOk={() => correctionForm.submit()}
      >
        <Alert
          className="mb-4"
          type="warning"
          showIcon
          title="更正不会覆盖历史，修改前后值、原因与证据都会保留。"
        />
        <Form
          form={correctionForm}
          layout="vertical"
          onFinish={submitCorrection}
        >
          <Form.Item name="before" label="修改前" rules={[{ required: true }]}>
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="after" label="修改后" rules={[{ required: true }]}>
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item
            name="reason"
            label="更正原因"
            rules={[{ required: true, min: 2 }]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="evidence" label="证据位置">
            <Input placeholder="订单号、日志、工单或文件引用" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
