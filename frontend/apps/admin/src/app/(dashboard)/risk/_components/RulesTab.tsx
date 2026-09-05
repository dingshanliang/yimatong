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
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { STATUS_COLORS } from "@/lib/status-colors";
import { RULE_TYPES, ACTIONS } from "./constants";

type Rule = Record<string, unknown> & { id: string; version: number };

export function RulesTab({ access }: { access: RiskAccess }) {
  const { message } = App.useApp();
  const { items, total, page, loading, setPage, mutate } = useCrud<Rule>(
    "/risk-rules",
    { enabled: access.canRead }
  );
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const handleCreate = async (values: Record<string, unknown>) => {
    try {
      const config = values.config_json
        ? JSON.parse(values.config_json as string)
        : {};
      await api.post(
        "/risk-rules",
        { ...values, config, config_json: undefined },
        { headers: { "Idempotency-Key": crypto.randomUUID() } }
      );
      await mutate();
      message.success("规则创建成功");
      setOpen(false);
      form.resetFields();
    } catch (e: unknown) {
      message.error(extractErrorMessage(e, "创建失败"));
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
  ];

  return (
    <>
      {access.canManage ? (
        <div className="mb-4 flex justify-end">
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setOpen(true)}
          >
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
        title="新建风控规则"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        width={550}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="规则名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Space className="w-full" orientation="vertical">
            <div className="grid grid-cols-2 gap-4">
              <Form.Item
                name="rule_type"
                label="规则类型"
                rules={[{ required: true }]}
              >
                <Select options={RULE_TYPES} />
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
              placeholder='{"max_requests": 5, "window_minutes": 60}'
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
