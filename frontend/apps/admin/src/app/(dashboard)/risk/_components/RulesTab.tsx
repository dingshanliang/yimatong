"use client";

import { useState } from "react";
import { useCrud } from "@/lib/hooks";
import api, { extractErrorMessage } from "@/lib/api";
import type { RiskAccess } from "@/lib/risk-access";
import {
  App,
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
} from "antd";
import { DeleteOutlined, EditOutlined, PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { STATUS_COLORS } from "@/lib/status-colors";
import { RULE_TYPES, ACTIONS, RULE_CONFIG_TEMPLATES } from "./constants";

type Rule = Record<string, unknown> & { id: string; version: number };

interface RuleFormValues {
  name: string;
  rule_type: string;
  action: string;
  config_json?: string;
}

export function RulesTab({ access }: { access: RiskAccess }) {
  const { message } = App.useApp();
  const { items, total, page, loading, setPage, mutate } = useCrud<Rule>(
    "/risk-rules",
    { enabled: access.canRead }
  );
  const [open, setOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<Rule | null>(null);
  const [form] = Form.useForm<RuleFormValues>();

  const openCreate = () => {
    setEditingRule(null);
    form.resetFields();
    setOpen(true);
  };

  const openEdit = (rule: Rule) => {
    setEditingRule(rule);
    form.setFieldsValue({
      name: String(rule.name ?? ""),
      rule_type: String(rule.rule_type ?? ""),
      action: String(rule.action ?? ""),
      config_json: JSON.stringify(rule.config ?? {}, null, 2),
    });
    setOpen(true);
  };

  const closeModal = () => {
    setOpen(false);
    setEditingRule(null);
    form.resetFields();
  };

  const handleTypeChange = (ruleType: string) => {
    // 选择/切换类型即填充该类型的配置模板，运营在其上微调即可。
    const template = RULE_CONFIG_TEMPLATES[ruleType];
    if (template) {
      form.setFieldValue("config_json", JSON.stringify(template, null, 2));
    }
  };

  const handleSubmit = async (values: RuleFormValues) => {
    let config: unknown;
    try {
      config = values.config_json ? JSON.parse(values.config_json) : {};
    } catch {
      message.error("规则配置不是合法 JSON");
      return;
    }
    const headers = { "Idempotency-Key": crypto.randomUUID() };
    try {
      if (editingRule) {
        await api.patch(
          `/risk-rules/${editingRule.id}`,
          {
            name: values.name,
            action: values.action,
            config,
            expected_version: editingRule.version,
          },
          { headers }
        );
        message.success("规则已更新");
      } else {
        await api.post(
          "/risk-rules",
          {
            name: values.name,
            rule_type: values.rule_type,
            action: values.action,
            config,
          },
          { headers }
        );
        message.success("规则创建成功");
      }
      closeModal();
      await mutate();
    } catch (e: unknown) {
      message.error(
        extractErrorMessage(e, editingRule ? "更新失败" : "创建失败")
      );
    }
  };

  const toggleEnabled = async (rule: Rule, enabled: boolean) => {
    try {
      await api.patch(
        `/risk-rules/${rule.id}`,
        { enabled, expected_version: rule.version },
        { headers: { "Idempotency-Key": crypto.randomUUID() } }
      );
      message.success(enabled ? "已启用" : "已禁用");
    } catch (e: unknown) {
      message.error(extractErrorMessage(e, "规则启停失败，请重试"));
    } finally {
      // 无论成败都刷新列表：保证 expected_version 始终为服务端最新版本，避免后续操作连续 409
      await mutate();
    }
  };

  const handleDelete = async (rule: Rule) => {
    try {
      await api.delete(
        `/risk-rules/${rule.id}?expected_version=${rule.version}`,
        { headers: { "Idempotency-Key": crypto.randomUUID() } }
      );
      // 后端删除是软删（停用并保留历史），如实告知用户。
      message.info("规则已停用（系统保留历史记录，不做物理删除）");
    } catch (e: unknown) {
      message.error(extractErrorMessage(e, "删除失败，请重试"));
    } finally {
      await mutate();
    }
  };

  const columns: ColumnsType<Rule> = [
    { title: "规则名称", dataIndex: "name", key: "name" },
    {
      title: "类型",
      dataIndex: "rule_type",
      key: "rule_type",
      render: (t: string) => RULE_TYPES.find((o) => o.value === t)?.label || t,
    },
    {
      title: "动作",
      dataIndex: "action",
      key: "action",
      render: (a: string) => {
        const color =
          a === "block"
            ? STATUS_COLORS.error
            : a === "warn"
              ? STATUS_COLORS.warning
              : STATUS_COLORS.processing;
        return (
          <Tag color={color}>
            {ACTIONS.find((o) => o.value === a)?.label || a}
          </Tag>
        );
      },
    },
    {
      title: "启用",
      dataIndex: "enabled",
      key: "enabled",
      render: (v: boolean, record) => {
        // block 类规则停用影响线上拦截，需二次确认；确认前 Switch 保持受控不翻转
        const disablingBlock = record.action === "block" && v;
        const sw = (
          <Switch
            checked={v}
            disabled={!access.canManage}
            onChange={
              disablingBlock
                ? undefined
                : (checked) => void toggleEnabled(record, checked)
            }
          />
        );
        if (!disablingBlock) return sw;
        return (
          <Popconfirm
            title="确认停用该拦截规则？"
            description="停用后命中该规则的扫码请求将不再被拦截。"
            okText="确认停用"
            cancelText="取消"
            onConfirm={() => {
              void toggleEnabled(record, false);
            }}
          >
            {sw}
          </Popconfirm>
        );
      },
    },
    ...(access.canManage
      ? [
          {
            title: "操作",
            key: "actions",
            width: 140,
            render: (_: unknown, record: Rule) => (
              <Space size={4}>
                <Button
                  size="small"
                  type="link"
                  icon={<EditOutlined />}
                  onClick={() => openEdit(record)}
                >
                  编辑
                </Button>
                <Popconfirm
                  title="确认删除该规则？"
                  description="删除为软删：规则将停用并保留历史记录。"
                  okText="确认删除"
                  okButtonProps={{ danger: true }}
                  cancelText="取消"
                  onConfirm={() => {
                    void handleDelete(record);
                  }}
                >
                  <Button
                    size="small"
                    type="link"
                    danger
                    icon={<DeleteOutlined />}
                  >
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]
      : []),
  ];

  return (
    <>
      {access.canManage ? (
        <div className="mb-4 flex justify-end">
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建规则
          </Button>
        </div>
      ) : null}
      <Table
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={loading}
        pagination={{
          current: page,
          total,
          pageSize: 20,
          onChange: setPage,
          showTotal: (t) => `共 ${t} 条`,
        }}
      />
      <Modal
        title={editingRule ? "编辑风控规则" : "新建风控规则"}
        open={open}
        onCancel={closeModal}
        onOk={() => form.submit()}
        width={550}
      >
        <Form<RuleFormValues>
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
        >
          <Form.Item name="name" label="规则名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Space className="w-full" orientation="vertical">
            <div className="grid grid-cols-2 gap-4">
              <Form.Item
                name="rule_type"
                label="规则类型"
                rules={[{ required: true }]}
                extra={editingRule ? "规则类型创建后不可更改" : undefined}
              >
                <Select
                  options={RULE_TYPES}
                  disabled={editingRule !== null}
                  onChange={handleTypeChange}
                />
              </Form.Item>
              <Form.Item
                name="action"
                label="执行动作"
                rules={[{ required: true }]}
              >
                <Select options={ACTIONS} />
              </Form.Item>
            </div>
          </Space>
          <Form.Item name="config_json" label="规则配置 (JSON)">
            <Input.TextArea
              rows={4}
              placeholder="选择规则类型后自动填充配置模板，可在此调整"
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
