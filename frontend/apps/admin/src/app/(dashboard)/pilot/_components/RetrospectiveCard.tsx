"use client";

import {
  App,
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Form,
  Input,
  Row,
  Statistic,
  Tag,
} from "antd";
import dayjs, { type Dayjs } from "dayjs";
import { useState } from "react";

import api, { extractErrorMessage } from "@/lib/api";

import {
  type ActionItem,
  formatDuration,
  type RetrospectiveRead,
  RETRO_STATUS_LABEL,
  RETRO_STATUS_TAG,
  type ScorecardMetric,
} from "./types";

interface RetrospectiveCardProps {
  retro: RetrospectiveRead;
  onChanged: () => void;
}

function metricDisplay(metric: ScorecardMetric | undefined): {
  value: string | number;
  suffix: string;
} {
  if (!metric) return { value: "—", suffix: "" };
  if (
    metric.status === "insufficient_data" ||
    metric.value === null ||
    metric.value === undefined
  ) {
    return { value: "数据不足", suffix: "" };
  }
  const suffix =
    metric.unit === "percent"
      ? "%"
      : metric.unit === "yuan"
        ? "元"
        : metric.unit === "count"
          ? ""
          : "";
  if (metric.unit === "percent")
    return { value: metric.value.toFixed(2), suffix };
  if (metric.unit === "yuan") return { value: metric.value.toFixed(2), suffix };
  if (metric.unit === "seconds") {
    return { value: formatDuration(metric.value), suffix: "" };
  }
  return { value: metric.value, suffix };
}

/** 单期复盘卡片（beads: yimatong-bgag.5，PRD §4.5）。
 * scorecard 始终只读（后端冻结）；goal/issues/actions/next_review_date/supplementary_notes 可填。
 */
export default function RetrospectiveCard({
  retro,
  onChanged,
}: RetrospectiveCardProps) {
  const { message } = App.useApp();
  const [editing, setEditing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm();

  const snap = retro.scorecard_snapshot;
  const metrics = [
    {
      key: "onboarding_to_launch",
      title: "开通→上线时长",
      m: snap.onboarding_to_launch,
    },
    {
      key: "launch_to_first_scan",
      title: "上线→首扫时长",
      m: snap.launch_to_first_scan,
    },
    { key: "valid_visits", title: "有效访问", m: snap.valid_visits },
    { key: "claim_rate", title: "权益确认率", m: snap.claim_rate },
    { key: "wecom_rate", title: "企微确认率", m: snap.wecom_rate },
    { key: "net_gmv", title: "净 GMV", m: snap.net_gmv },
  ];

  async function handleSave(markCompleted: boolean) {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      const payload: Record<string, unknown> = {
        goal: values.goal ?? null,
        issues: values.issues ?? null,
        next_review_date: values.next_review_date
          ? (values.next_review_date as Dayjs).format("YYYY-MM-DD")
          : null,
        mark_completed: markCompleted,
      };
      await api.patch(`/retrospectives/${retro.id}`, payload);
      message.success(markCompleted ? "复盘已完成" : "已保存");
      setEditing(false);
      onChanged();
    } catch (err) {
      if (
        markCompleted ||
        (err as { errorFields?: unknown }).errorFields === undefined
      ) {
        message.error(extractErrorMessage(err, "保存失败"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  function startEdit() {
    form.setFieldsValue({
      goal: retro.goal ?? "",
      issues: retro.issues ?? "",
      next_review_date: retro.next_review_date
        ? dayjs(retro.next_review_date)
        : undefined,
    });
    setEditing(true);
  }

  const isCompleted = retro.status === "completed";

  return (
    <Card
      title={
        <span>
          第 {retro.period_day} 天复盘{" "}
          <Tag color={RETRO_STATUS_TAG[retro.derived_status]}>
            {RETRO_STATUS_LABEL[retro.derived_status]}
          </Tag>
        </span>
      }
      size="small"
      extra={
        isCompleted ? null : editing ? (
          <>
            <Button
              size="small"
              onClick={() => setEditing(false)}
              disabled={submitting}
            >
              取消
            </Button>
            <Button
              size="small"
              type="primary"
              onClick={() => handleSave(false)}
              loading={submitting}
              style={{ marginLeft: 8 }}
            >
              保存
            </Button>
            <Button
              size="small"
              type="primary"
              danger
              onClick={() => handleSave(true)}
              loading={submitting}
              style={{ marginLeft: 8 }}
            >
              完成复盘
            </Button>
          </>
        ) : (
          <Button size="small" type="link" onClick={startEdit}>
            填写
          </Button>
        )
      }
    >
      <Row gutter={[16, 16]}>
        {metrics.map((metric) => {
          const display = metricDisplay(metric.m);
          return (
            <Col span={8} key={metric.key}>
              <Statistic
                title={metric.title}
                value={display.value}
                suffix={display.suffix}
              />
            </Col>
          );
        })}
      </Row>

      {editing ? (
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item label="本期目标" name="goal">
            <Input.TextArea rows={2} placeholder="本期验证目标" />
          </Form.Item>
          <Form.Item label="问题与判断" name="issues">
            <Input.TextArea rows={3} placeholder="数据观察、异常、判断" />
          </Form.Item>
          <Form.Item label="下次验证日期" name="next_review_date">
            <DatePicker style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      ) : (
        <Descriptions column={1} size="small" style={{ marginTop: 16 }}>
          <Descriptions.Item label="本期目标">
            {retro.goal || "—"}
          </Descriptions.Item>
          <Descriptions.Item label="问题与判断">
            {retro.issues || "—"}
          </Descriptions.Item>
          <Descriptions.Item label="动作清单">
            {retro.actions && retro.actions.length > 0 ? (
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {retro.actions.map((a: ActionItem, i: number) => (
                  <li key={a.content ? `${a.content}-${i}` : i}>
                    {a.content}
                    {a.due_date ? `（截至 ${a.due_date}）` : ""}
                  </li>
                ))}
              </ul>
            ) : (
              "—"
            )}
          </Descriptions.Item>
          <Descriptions.Item label="补充说明">
            {retro.supplementary_notes || "—"}
          </Descriptions.Item>
        </Descriptions>
      )}
    </Card>
  );
}
