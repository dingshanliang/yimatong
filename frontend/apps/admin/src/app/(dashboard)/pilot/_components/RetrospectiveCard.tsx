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
  Radio,
  Row,
  Select,
  Statistic,
  Tag,
} from "antd";
import { MinusCircleOutlined } from "@ant-design/icons";
import dayjs, { type Dayjs } from "dayjs";
import { useState } from "react";

import api, { extractErrorMessage } from "@/lib/api";
import { STATUS_COLORS } from "@/lib/status-colors";

import {
  ACTION_DISPOSITION_OPTIONS,
  type ActionDisposition,
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

/** 把表单 actions 行转为后端 PATCH payload（透传 carryover 字段）。 */
function buildActionsPayload(
  rows: (Record<string, unknown> | undefined)[] | undefined
): ActionItem[] | null {
  if (!rows) return null;
  return rows
    .filter((r): r is Record<string, unknown> => r !== undefined)
    .map((r) => ({
      content: String(r.content ?? ""),
      owner_id: (r.owner_id as string) || null,
      due_date: r.due_date ? (r.due_date as Dayjs).format("YYYY-MM-DD") : null,
      status: (r.status as string) || "pending",
      carryover: Boolean(r.carryover),
      carryover_disposition:
        (r.carryover_disposition as ActionDisposition) || null,
    }));
}

/** 动作行（Form.List 子项）。独立组件以合法调用 Form.useWatch 检测 carryover。 */
interface ActionRowProps {
  field: { key: number; name: number };
  form: ReturnType<typeof Form.useForm>[0];
  onRemove: () => void;
}

function ActionRow({ field, form, onRemove }: ActionRowProps) {
  // 顶层调用 useWatch（合规）：监测该行 carryover 字段，决定是否渲染处置 Radio
  const carryover = Form.useWatch(["actions", field.name, "carryover"], form);
  const disposition = Form.useWatch(
    ["actions", field.name, "carryover_disposition"],
    form
  );
  // PRD §8：未处置的承接动作不允许删除（必须显式 abandon），避免 UI/后端不一致
  const removalBlocked = Boolean(carryover) && !disposition;
  return (
    <div
      style={{
        marginBottom: 8,
        padding: 8,
        border: "1px solid var(--ymt-color-border-base)",
        borderRadius: 4,
      }}
    >
      <Row gutter={8}>
        <Col span={12}>
          <Form.Item
            name={[field.name, "content"]}
            rules={[{ required: true, message: "请输入动作内容" }]}
            noStyle
          >
            <Input placeholder="动作内容" />
          </Form.Item>
        </Col>
        <Col span={6}>
          <Form.Item name={[field.name, "owner_id"]} noStyle>
            <Input placeholder="负责人 ID（可选）" />
          </Form.Item>
        </Col>
        <Col span={5}>
          <Form.Item name={[field.name, "status"]} noStyle>
            <Select
              options={[
                { value: "pending", label: "待处理" },
                { value: "completed", label: "已完成" },
              ]}
            />
          </Form.Item>
        </Col>
        <Col span={1}>
          <MinusCircleOutlined
            onClick={removalBlocked ? undefined : onRemove}
            style={{
              marginTop: 8,
              color: removalBlocked
                ? "var(--ymt-color-text-tertiary)"
                : undefined,
              cursor: removalBlocked ? "not-allowed" : "pointer",
            }}
          />
        </Col>
      </Row>
      <Row gutter={8} style={{ marginTop: 4 }}>
        <Col span={8}>
          <Form.Item name={[field.name, "due_date"]} noStyle>
            <DatePicker style={{ width: "100%" }} placeholder="期望完成日" />
          </Form.Item>
        </Col>
        {carryover ? (
          <Col span={16}>
            <Form.Item
              name={[field.name, "carryover_disposition"]}
              label="承接处置"
              rules={[{ required: true, message: "承接动作必须处置" }]}
            >
              <Radio.Group
                options={ACTION_DISPOSITION_OPTIONS}
                optionType="button"
                buttonStyle="solid"
              />
            </Form.Item>
          </Col>
        ) : null}
        <Form.Item name={[field.name, "carryover"]} hidden initialValue={false}>
          <input type="hidden" />
        </Form.Item>
      </Row>
    </div>
  );
}

/** 单期复盘卡片（beads: yimatong-bgag.5/10，PRD §4.3/§4.5/§5/§8）。
 * scorecard 始终只读（后端冻结）；goal/issues/actions/next_review_date 可填；
 * completed 态可追加 supplementary_notes（PRD §5）；actions 支持增删改 + carryover 处置（PRD §8）。
 */
export default function RetrospectiveCard({
  retro,
  onChanged,
}: RetrospectiveCardProps) {
  const { message } = App.useApp();
  const [editing, setEditing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [notesEditing, setNotesEditing] = useState(false);
  const [notesSubmitting, setNotesSubmitting] = useState(false);
  const [notesForm] = Form.useForm();
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
      const actionsPayload = buildActionsPayload(values.actions as never);
      if (actionsPayload !== null) payload.actions = actionsPayload;
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

  async function handleSaveNotes() {
    try {
      const values = await notesForm.validateFields();
      setNotesSubmitting(true);
      await api.patch(`/retrospectives/${retro.id}`, {
        supplementary_notes: values.supplementary_notes ?? null,
      });
      message.success("补充说明已追加");
      setNotesEditing(false);
      notesForm.resetFields();
      onChanged();
    } catch (err) {
      if ((err as { errorFields?: unknown }).errorFields === undefined) {
        message.error(extractErrorMessage(err, "追加失败"));
      }
    } finally {
      setNotesSubmitting(false);
    }
  }

  function startEdit() {
    form.setFieldsValue({
      goal: retro.goal ?? "",
      issues: retro.issues ?? "",
      next_review_date: retro.next_review_date
        ? dayjs(retro.next_review_date)
        : undefined,
      actions: (retro.actions ?? []).map((a) => ({
        content: a.content,
        owner_id: a.owner_id ?? undefined,
        due_date: a.due_date ? dayjs(a.due_date) : undefined,
        status: a.status ?? "pending",
        carryover: a.carryover ?? false,
        carryover_disposition: a.carryover_disposition ?? null,
      })),
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
          <Form.Item label="动作清单">
            <Form.List name="actions">
              {(fields, { add, remove }) => (
                <>
                  {fields.map((field) => (
                    <ActionRow
                      key={field.key}
                      field={field}
                      form={form}
                      onRemove={() => remove(field.name)}
                    />
                  ))}
                  <Button
                    type="dashed"
                    onClick={() => add({ content: "", status: "pending" })}
                  >
                    + 添加动作
                  </Button>
                </>
              )}
            </Form.List>
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
                    {a.status === "completed" ? (
                      <Tag
                        color={STATUS_COLORS.success}
                        style={{ marginLeft: 6 }}
                      >
                        已完成
                      </Tag>
                    ) : (
                      <Tag
                        color={STATUS_COLORS.processing}
                        style={{ marginLeft: 6 }}
                      >
                        待处理
                      </Tag>
                    )}
                    {a.carryover ? (
                      <Tag
                        color={STATUS_COLORS.warning}
                        style={{ marginLeft: 6 }}
                      >
                        承接
                        {a.carryover_disposition
                          ? `·${a.carryover_disposition}`
                          : "·待处置"}
                      </Tag>
                    ) : null}
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

      {/* PRD §5：completed 态可追加补充说明（快照冻结后唯一可写字段） */}
      {isCompleted && !editing ? (
        notesEditing ? (
          <Form form={notesForm} layout="vertical" style={{ marginTop: 16 }}>
            <Form.Item
              label="追加补充说明"
              name="supplementary_notes"
              rules={[{ required: true, message: "请输入补充说明" }]}
            >
              <Input.TextArea rows={2} placeholder="追加的补充说明" />
            </Form.Item>
            <div>
              <Button
                size="small"
                onClick={() => setNotesEditing(false)}
                disabled={notesSubmitting}
              >
                取消
              </Button>
              <Button
                size="small"
                type="primary"
                onClick={handleSaveNotes}
                loading={notesSubmitting}
                style={{ marginLeft: 8 }}
              >
                追加
              </Button>
            </div>
          </Form>
        ) : (
          <Button
            size="small"
            type="link"
            onClick={() => setNotesEditing(true)}
          >
            追加补充说明
          </Button>
        )
      ) : null}
    </Card>
  );
}
