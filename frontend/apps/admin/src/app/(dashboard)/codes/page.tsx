"use client";

import { useEffect, useState, useCallback } from "react";
import { usePaginatedList } from "@/lib/hooks";
import { App, Button, Empty, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Table, Tag, Typography } from "antd";
import { QrcodeOutlined, PlusOutlined, DownloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title } = Typography;

interface CodeBatch {
  id: string;
  batch_code: string;
  quantity: number;
  product_id: string;
  sku_id: string;
  code_type: string;
  status: string;
  created_at: string;
}

interface Product {
  id: string;
  name: string;
}

interface SKU {
  id: string;
  name: string;
  product_id: string;
}

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: "待生成", color: "default" },
  generating: { label: "生成中", color: "blue" },
  completed: { label: "已完成", color: "green" },
  failed: { label: "失败", color: "red" },
  created: { label: "已生成", color: "default" },
  activated: { label: "已激活", color: "blue" },
  bound: { label: "已绑定", color: "green" },
  exported: { label: "已导出", color: "purple" },
};

const CODE_TYPE_OPTIONS = [
  { value: "single", label: "单码" },
  { value: "paired", label: "双码（内码+外码）" },
  { value: "outer", label: "外码（引流）" },
  { value: "inner", label: "内码（验真）" },
];

export default function CodesPage() {
  const { message } = App.useApp();
  const [products, setProducts] = useState<Product[]>([]);
  const [skus, setSKUs] = useState<SKU[]>([]);
  const [status, setStatus] = useState<string | undefined>(undefined);
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm();
  const [selectedProduct, setSelectedProduct] = useState<string | undefined>(undefined);

  const { items: batches, total, page, loading, setPage, refresh } = usePaginatedList<CodeBatch>(
    async ({ page, page_size }) => {
      try {
        const params: Record<string, string | number> = { page, page_size };
        if (status) params.status = status;
        const { data } = await api.get("/code-batches", { params });
        return { items: data.items || [], total: data.total || 0 };
      } catch {
        message.error("加载码批次列表失败");
        return { items: [], total: 0 };
      }
    },
    [status]
  );

  const fetchProducts = useCallback(async () => {
    try {
      const { data } = await api.get("/products", { params: { page_size: 100 } });
      setProducts(data.items || []);
    } catch { /* ignore */ }
  }, []);

  const fetchSKUs = useCallback(async (productId?: string) => {
    if (!productId) { setSKUs([]); return; }
    try {
      const { data } = await api.get("/skus", { params: { product_id: productId, page_size: 100 } });
      setSKUs(data.items || []);
    } catch {
      setSKUs([]);
    }
  }, []);

  useEffect(() => { fetchProducts(); }, [fetchProducts]);

  const handleCreate = async (values: Record<string, unknown>) => {
    try {
      await api.post("/code-batches", values);
      message.success("码批次创建成功");
      setCreateOpen(false);
      form.resetFields();
      setSelectedProduct(undefined);
      setSKUs([]);
      setPage(1);
      refresh();
    } catch {
      message.error("创建失败");
    }
  };

  const handleActivate = async (id: string) => {
    try {
      await api.post(`/code-batches/${id}/activate`);
      message.success("码批次已激活");
      refresh();
    } catch {
      message.error("激活失败");
    }
  };

  const handleExport = async (id: string) => {
    try {
      const { data } = await api.post(`/code-batches/${id}/export`);
      message.success(`导出任务已创建: ${data.task_id || id.slice(0, 8)}`);
    } catch {
      message.error("导出失败");
    }
  };

  const columns: ColumnsType<CodeBatch> = [
    { title: "批次号", dataIndex: "batch_code", key: "batch_code" },
    { title: "数量", dataIndex: "quantity", key: "quantity" },
    {
      title: "码类型",
      dataIndex: "code_type",
      key: "code_type",
      render: (t: string) => CODE_TYPE_OPTIONS.find((o) => o.value === t)?.label || t,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      render: (v: string) => new Date(v).toLocaleDateString("zh-CN"),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: CodeBatch) => (
        <Space>
          {record.status === "completed" && (
            <Popconfirm title="确认激活此码批次？" onConfirm={() => handleActivate(record.id)}>
              <Button size="small" type="primary">激活</Button>
            </Popconfirm>
          )}
          {(record.status === "activated" || record.status === "completed") && (
            <Button size="small" icon={<DownloadOutlined />} onClick={() => handleExport(record.id)}>
              导出
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">码管理</Title>
        <Space>
          <Select
            placeholder="按状态筛选"
            allowClear
            style={{ width: 150 }}
            value={status}
            onChange={(v) => { setStatus(v); setPage(1); }}
            options={Object.entries(STATUS_MAP).map(([value, { label }]) => ({ value, label }))}
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            生成码批次
          </Button>
        </Space>
      </div>
      {batches.length === 0 && !loading ? (
        <Empty
          image={<QrcodeOutlined style={{ fontSize: 48, color: "#ccc" }} />}
          description="暂无码批次，请先生成码批次"
        />
      ) : (
        <Table
          columns={columns}
          dataSource={batches}
          rowKey="id"
          loading={loading}
          pagination={{
            current: page, total, pageSize: 20, onChange: setPage,
            showTotal: (t) => `共 ${t} 条`,
          }}
        />
      )}
      <Modal
        title="生成码批次"
        open={createOpen}
        onCancel={() => { setCreateOpen(false); setSelectedProduct(undefined); setSKUs([]); }}
        onOk={() => form.submit()}
        width={500}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="product_id" label="关联产品" rules={[{ required: true, message: "请选择产品" }]}>
            <Select
              placeholder="选择产品"
              options={products.map((p) => ({ value: p.id, label: p.name }))}
              showSearch
              optionFilterProp="label"
              onChange={(v) => { setSelectedProduct(v); fetchSKUs(v); form.setFieldValue("sku_id", undefined); }}
              data-testid="code-batch-product-select"
            />
          </Form.Item>
          <Form.Item name="sku_id" label="关联 SKU" rules={[{ required: true, message: "请选择 SKU" }]}>
            <Select
              placeholder="选择 SKU"
              options={skus.map((s) => ({ value: s.id, label: s.name }))}
              showSearch
              optionFilterProp="label"
              disabled={!selectedProduct}
              data-testid="code-batch-sku-select"
            />
          </Form.Item>
          <Form.Item name="batch_code" label="批次号" rules={[{ required: true, message: "请输入批次号" }]}>
            <Input placeholder="例如 PB-2026-001" data-testid="code-batch-code-input" />
          </Form.Item>
          <Form.Item name="quantity" label="生成数量" rules={[{ required: true, message: "请输入数量" }]}>
            <InputNumber min={1} max={100000} style={{ width: "100%" }} placeholder="1-100000" data-testid="code-batch-quantity-input" />
          </Form.Item>
          <Form.Item name="code_type" label="码类型" initialValue="single">
            <Select options={CODE_TYPE_OPTIONS} data-testid="code-batch-type-select" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
