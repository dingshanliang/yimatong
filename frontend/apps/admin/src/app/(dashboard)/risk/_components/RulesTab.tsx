"use client";

import { useState } from "react";
import { useCrud } from "@/lib/hooks";
import { App, Button, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { RULE_TYPES, ACTIONS } from "./constants";

type Rule = Record<string, unknown> & { id: string };

export function RulesTab() {
  const { message } = App.useApp();
  const { items, total, page, loading, setPage, create, update, remove } = useCrud<Rule>("/risk-rules");
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const handleCreate = async (values: Record<string, unknown>) => {
    try {
      const config = values.config_json ? JSON.parse(values.config_json as string) : {};
      await create({ ...values, config });
      message.success("规则创建成功");
      setOpen(false);
      form.resetFields();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "创建失败");
    }
  };

  const toggleEnabled = async (id: string, enabled: boolean) => {
    try {
      await update(id, { enabled });
      message.success(enabled ? "已启用" : "已禁用");
    } catch { message.error("操作失败"); }
  };

  const columns: ColumnsType<Rule> = [
    { title: "规则名称", dataIndex: "name", key: "name" },
    { title: "类型", dataIndex: "rule_type", key: "rule_type", render: (t: string) => RULE_TYPES.find((o) => o.value === t)?.label || t },
    { title: "动作", dataIndex: "action", key: "action", render: (a: string) => {
      const color = a === "block" ? "red" : a === "warn" ? "orange" : "blue";
      return <Tag color={color}>{ACTIONS.find((o) => o.value === a)?.label || a}</Tag>;
    }},
    { title: "启用", dataIndex: "enabled", key: "enabled", render: (v: boolean, record) => <Switch checked={v} onChange={(checked) => toggleEnabled(record.id as string, checked)} /> },
    { title: "操作", key: "actions", render: (_: unknown, record) => (
      <Popconfirm title="确认删除此规则？" onConfirm={async () => { await remove(record.id as string); message.success("已删除"); }}>
        <Button size="small" danger>删除</Button>
      </Popconfirm>
    )},
  ];

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>新建规则</Button>
      </div>
      <Table columns={columns} dataSource={items} rowKey="id" loading={loading}
        pagination={{ current: page, total, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
      />
      <Modal title="新建风控规则" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={550}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="规则名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Space className="w-full" orientation="vertical">
            <div className="grid grid-cols-2 gap-4">
              <Form.Item name="rule_type" label="规则类型" rules={[{ required: true }]}><Select options={RULE_TYPES} /></Form.Item>
              <Form.Item name="action" label="执行动作" rules={[{ required: true }]}><Select options={ACTIONS} /></Form.Item>
            </div>
          </Space>
          <Form.Item name="config_json" label="规则配置 (JSON)">
            <Input.TextArea rows={4} placeholder='{"max_count": 5, "window_minutes": 60}' />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
