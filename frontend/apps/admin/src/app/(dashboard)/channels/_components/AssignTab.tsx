"use client";

import { useState } from "react";
import { useCrud } from "@/lib/hooks";
import { Button, Form, Input, message, Modal, Table } from "antd";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

type Batch = Record<string, unknown> & { id: string };

export function AssignTab() {
  const { items: batches, total: batchTotal, page: batchPage, loading, setPage: setBatchPage, mutate } = useCrud<Batch>("/code-batches");
  const [assignOpen, setAssignOpen] = useState(false);
  const [selectedBatch, setSelectedBatch] = useState<string | null>(null);
  const [form] = Form.useForm();

  const handleAssign = async (values: Batch) => {
    if (!selectedBatch) return;
    try {
      await api.post(`/channels/code-batches/${selectedBatch}/assign`, values);
      message.success("码段分配成功");
      setAssignOpen(false);
      form.resetFields();
      mutate();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "分配失败");
    }
  };

  const columns: ColumnsType<Batch> = [
    { title: "批次名称", dataIndex: "name", key: "name" },
    { title: "码数量", dataIndex: "quantity", key: "quantity" },
    { title: "状态", dataIndex: "status", key: "status" },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: Batch) => (
        <Button size="small" onClick={() => { setSelectedBatch(record.id as string); setAssignOpen(true); }}>
          分配渠道
        </Button>
      ),
    },
  ];

  return (
    <>
      <Table
        columns={columns}
        dataSource={batches}
        rowKey="id"
        loading={loading}
        pagination={{ current: batchPage, total: batchTotal, pageSize: 20, onChange: setBatchPage, showTotal: (t) => `共 ${t} 条` }}
      />
      <Modal title="分配码段到渠道" open={assignOpen} onCancel={() => setAssignOpen(false)} onOk={() => form.submit()} width={500}>
        <Form form={form} layout="vertical" onFinish={handleAssign}>
          <Form.Item name="distributor_id" label="经销商 ID">
            <Input placeholder="粘贴经销商 UUID" />
          </Form.Item>
          <Form.Item name="region_id" label="区域 ID">
            <Input placeholder="粘贴区域 UUID" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
