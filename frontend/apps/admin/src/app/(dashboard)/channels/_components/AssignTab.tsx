"use client";

import { useEffect, useState } from "react";
import { useCrud } from "@/lib/hooks";
import api from "@/lib/api";
import { Button, Form, InputNumber, message, Modal, Select, Table, Tabs } from "antd";
import type { ColumnsType } from "antd/es/table";

type Batch = Record<string, unknown> & { id: string; batch_code: string; quantity: number };
type Allocation = Record<string, unknown> & { id: string; batch_id: string; store_id: string; quantity: number };
type Option = { label: string; value: string };

/* ── 批次→渠道分配 ── */

function BatchAssignTab() {
  const { items: batches, total, page, loading, setPage, mutate } = useCrud<Batch>("/code-batches");
  const [open, setOpen] = useState(false);
  const [selectedBatch, setSelectedBatch] = useState<string | null>(null);
  const [form] = Form.useForm();

  const [distOptions, setDistOptions] = useState<Option[]>([]);
  const [regionOptions, setRegionOptions] = useState<Option[]>([]);

  useEffect(() => {
    api.get("/channels/distributors?page_size=100").then(({ data }) => {
      const items = data?.items || [];
      setDistOptions(items.map((d: Record<string, unknown>) => ({ label: String(d.name), value: String(d.id) })));
    }).catch(() => {});
    api.get("/channels/regions?page_size=100").then(({ data }) => {
      const items = data?.items || [];
      setRegionOptions(items.map((r: Record<string, unknown>) => ({ label: String(r.name), value: String(r.id) })));
    }).catch(() => {});
  }, []);

  const handleAssign = async (values: Record<string, unknown>) => {
    if (!selectedBatch) return;
    try {
      await api.post(`/channels/code-batches/${selectedBatch}/assign`, values);
      message.success("码段分配成功");
      setOpen(false);
      form.resetFields();
      mutate();
    } catch {
      message.error("分配失败");
    }
  };

  const columns: ColumnsType<Batch> = [
    { title: "批次编码", dataIndex: "batch_code", key: "batch_code" },
    { title: "码数量", dataIndex: "quantity", key: "quantity" },
    { title: "状态", dataIndex: "status", key: "status" },
    {
      title: "操作", key: "actions",
      render: (_: unknown, record: Batch) => (
        <Button size="small" type="link" onClick={() => { setSelectedBatch(record.id); setOpen(true); }}>
          分配渠道
        </Button>
      ),
    },
  ];

  return (
    <>
      <Table columns={columns} dataSource={batches} rowKey="id" loading={loading}
        pagination={{ current: page, total, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }} />
      <Modal title="分配码段到渠道" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={500}>
        <Form form={form} layout="vertical" onFinish={handleAssign}>
          <Form.Item name="distributor_id" label="经销商">
            <Select placeholder="选择经销商" allowClear options={distOptions} />
          </Form.Item>
          <Form.Item name="region_id" label="区域">
            <Select placeholder="选择区域" allowClear options={regionOptions} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

/* ── 门店级分配 ── */

function StoreAllocationTab() {
  const [allocs, setAllocs] = useState<Allocation[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const [storeOptions, setStoreOptions] = useState<Option[]>([]);
  const [batchOptions, setBatchOptions] = useState<Option[]>([]);

  const fetchAllocs = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/channels/code-allocations");
      setAllocs(Array.isArray(data) ? data : []);
    } catch { /* */ }
    finally { setLoading(false); }
  };

  useEffect(() => {
    api.get("/channels/stores?page_size=100").then(({ data }) => {
      const items = data?.items || [];
      setStoreOptions(items.map((s: Record<string, unknown>) => ({ label: String(s.name), value: String(s.id) })));
    }).catch(() => {});
    api.get("/code-batches?page_size=100").then(({ data }) => {
      const items = data?.items || [];
      setBatchOptions(items.map((b: Record<string, unknown>) => ({ label: String(b.batch_code || b.id).slice(0, 20), value: String(b.id) })));
    }).catch(() => {});
    fetchAllocs();
  }, []);

  const handleAllocate = async (values: Record<string, unknown>) => {
    try {
      await api.post("/channels/code-allocations", values);
      message.success("门店分配成功");
      setOpen(false);
      form.resetFields();
      fetchAllocs();
    } catch {
      message.error("分配失败");
    }
  };

  const columns: ColumnsType<Allocation> = [
    { title: "批次 ID", dataIndex: "batch_id", key: "batch_id", render: (v: string) => v?.slice(0, 8) + "..." },
    { title: "门店 ID", dataIndex: "store_id", key: "store_id", render: (v: string) => v?.slice(0, 8) + "..." },
    { title: "数量", dataIndex: "quantity", key: "quantity" },
    { title: "分配时间", dataIndex: "allocated_at", key: "allocated_at", render: (v: string) => v || "—" },
  ];

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button type="primary" onClick={() => setOpen(true)}>新建门店分配</Button>
      </div>
      <Table columns={columns} dataSource={allocs} rowKey="id" loading={loading} pagination={false} size="small" />
      <Modal title="门店码段分配" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={500}>
        <Form form={form} layout="vertical" onFinish={handleAllocate}>
          <Form.Item name="batch_id" label="码批次" rules={[{ required: true }]}>
            <Select placeholder="选择批次" options={batchOptions} showSearch optionFilterProp="label" />
          </Form.Item>
          <Form.Item name="store_id" label="门店" rules={[{ required: true }]}>
            <Select placeholder="选择门店" options={storeOptions} showSearch optionFilterProp="label" />
          </Form.Item>
          <Form.Item name="quantity" label="分配数量" rules={[{ required: true }]}>
            <InputNumber min={1} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

/* ── 主 Tab ── */

const subTabs = [
  { key: "batch", label: "批次→渠道", children: <BatchAssignTab /> },
  { key: "store", label: "门店级分配", children: <StoreAllocationTab /> },
];

export function AssignTab() {
  return <Tabs defaultActiveKey="batch" items={subTabs} />;
}
