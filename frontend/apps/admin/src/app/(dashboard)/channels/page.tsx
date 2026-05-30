"use client";

import { useState } from "react";
import { useCrud } from "@/lib/hooks";
import { Button, Form, Input, message, Modal, Table, Tabs, Tag, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title } = Typography;

/* ---------- Distributors ---------- */

const distColumns: ColumnsType<Record<string, unknown> & { id: string }> = [
  { title: "名称", dataIndex: "name", key: "name" },
  { title: "编码", dataIndex: "code", key: "code" },
  {
    title: "状态",
    dataIndex: "status",
    key: "status",
    render: (s: string) => <Tag color={s === "active" ? "green" : "default"}>{s === "active" ? "启用" : s || "—"}</Tag>,
  },
];

function DistributorTab() {
  const { items, total, page, loading, setPage, create } = useCrud<Record<string, unknown> & { id: string }>("/channels/distributors");
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const handleCreate = async (values: Record<string, unknown> & { id: string }) => {
    try {
      await create(values);
      message.success("经销商创建成功");
      setOpen(false);
      form.resetFields();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "创建失败");
    }
  };

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          新建经销商
        </Button>
      </div>
      <Table
        columns={distColumns}
        dataSource={items}
        rowKey="id"
        loading={loading}
        pagination={{ current: page, total, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
      />
      <Modal title="新建经销商" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={500}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="code" label="编码" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="contact_name" label="联系人">
            <Input />
          </Form.Item>
          <Form.Item name="contact_phone" label="联系电话">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

/* ---------- Regions ---------- */

function RegionTab() {
  const { items, total, page, loading, setPage, create } = useCrud<Record<string, unknown> & { id: string }>("/channels/regions");
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const handleCreate = async (values: Record<string, unknown> & { id: string }) => {
    try {
      await create(values);
      message.success("区域创建成功");
      setOpen(false);
      form.resetFields();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "创建失败");
    }
  };

  const columns: ColumnsType<Record<string, unknown> & { id: string }> = [
    { title: "名称", dataIndex: "name", key: "name" },
    { title: "编码", dataIndex: "code", key: "code" },
    { title: "城市", dataIndex: "city", key: "city" },
  ];

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          新建区域
        </Button>
      </div>
      <Table
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={loading}
        pagination={{ current: page, total, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
      />
      <Modal title="新建区域" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={500}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="code" label="编码" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="province" label="省份">
            <Input />
          </Form.Item>
          <Form.Item name="city" label="城市">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

/* ---------- Stores ---------- */

function StoreTab() {
  const { items, total, page, loading, setPage, create } = useCrud<Record<string, unknown> & { id: string }>("/channels/stores");
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const handleCreate = async (values: Record<string, unknown> & { id: string }) => {
    try {
      await create(values);
      message.success("门店创建成功");
      setOpen(false);
      form.resetFields();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "创建失败");
    }
  };

  const columns: ColumnsType<Record<string, unknown> & { id: string }> = [
    { title: "名称", dataIndex: "name", key: "name" },
    { title: "编码", dataIndex: "code", key: "code" },
    { title: "地址", dataIndex: "address", key: "address" },
  ];

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          新建门店
        </Button>
      </div>
      <Table
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={loading}
        pagination={{ current: page, total, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
      />
      <Modal title="新建门店" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={500}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="code" label="编码" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="address" label="地址">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

/* ---------- Batch Assignment ---------- */

function AssignTab() {
  const { items: batches, total: batchTotal, page: batchPage, loading, setPage: setBatchPage, mutate } = useCrud<Record<string, unknown> & { id: string }>("/code-batches");
  const [assignOpen, setAssignOpen] = useState(false);
  const [selectedBatch, setSelectedBatch] = useState<string | null>(null);
  const [form] = Form.useForm();

  const handleAssign = async (values: Record<string, unknown> & { id: string }) => {
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

  const columns: ColumnsType<Record<string, unknown> & { id: string }> = [
    { title: "批次名称", dataIndex: "name", key: "name" },
    { title: "码数量", dataIndex: "quantity", key: "quantity" },
    { title: "状态", dataIndex: "status", key: "status" },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: Record<string, unknown> & { id: string }) => (
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

/* ---------- Diversion Clues ---------- */

function DiversionTab() {
  const { items, total, page, loading, setPage } = useCrud<Record<string, unknown> & { id: string }>("/channels/diversion-clues");

  const columns: ColumnsType<Record<string, unknown> & { id: string }> = [
    { title: "码 ID", dataIndex: "public_id", key: "public_id" },
    { title: "预期区域", dataIndex: "expected_region", key: "expected_region" },
    { title: "实际城市", dataIndex: "detected_city", key: "detected_city" },
    {
      title: "状态",
      dataIndex: "resolved",
      key: "resolved",
      render: (v: boolean) => <Tag color={v ? "green" : "red"}>{v ? "已处理" : "待处理"}</Tag>,
    },
  ];

  return (
    <Table
      columns={columns}
      dataSource={items}
      rowKey="id"
      loading={loading}
      pagination={{ current: page, total, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
    />
  );
}

/* ---------- Main Page ---------- */

const tabItems = [
  { key: "distributors", label: "经销商", children: <DistributorTab /> },
  { key: "regions", label: "区域", children: <RegionTab /> },
  { key: "stores", label: "门店", children: <StoreTab /> },
  { key: "assign", label: "码段分配", children: <AssignTab /> },
  { key: "diversion", label: "窜货线索", children: <DiversionTab /> },
];

export default function ChannelsPage() {
  return (
    <div>
      <Title level={4} className="!mb-4">渠道管理</Title>
      <Tabs defaultActiveKey="distributors" items={tabItems} />
    </div>
  );
}
