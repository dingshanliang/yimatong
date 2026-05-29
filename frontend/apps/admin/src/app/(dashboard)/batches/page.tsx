"use client";

import { useEffect, useState, useCallback } from "react";
import { usePaginatedList } from "@/lib/hooks";
import {
  Table, Button, Space, Modal, Form, Input, Select, DatePicker, Tag, Typography, message,
} from "antd";
import { PlusOutlined, UploadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import dayjs from "dayjs";

const { Title } = Typography;

interface ProductionBatch {
  id: string;
  product_id: string;
  sku_id: string;
  batch_code: string;
  production_date: string;
  expiry_date: string;
  status: string;
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

const BATCH_STATUS_MAP: Record<string, { label: string; color: string }> = {
  active: { label: "有效", color: "green" },
  recalled: { label: "已召回", color: "red" },
  expired: { label: "已过期", color: "gray" },
};

export default function BatchesPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [skus, setSKUs] = useState<SKU[]>([]);
  const [filterProduct, setFilterProduct] = useState<string | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const [selectedProduct, setSelectedProduct] = useState<string | undefined>(undefined);

  const { items: batches, total, page, loading, setPage, refresh } = usePaginatedList<ProductionBatch>(
    async ({ page, page_size }) => {
      try {
        const params: Record<string, string | number> = { page, page_size };
        if (filterProduct) params.product_id = filterProduct;
        const { data } = await api.get("/production-batches", { params });
        return { items: data.items || [], total: data.total || 0 };
      } catch {
        message.error("加载生产批次列表失败");
        return { items: [], total: 0 };
      }
    },
    [filterProduct]
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
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchProducts(); }, [fetchProducts]);

  const handleCreate = async (values: Record<string, unknown>) => {
    try {
      const payload = {
        ...values,
        production_date: (values.production_date as dayjs.Dayjs).format("YYYY-MM-DD"),
        expiry_date: (values.expiry_date as dayjs.Dayjs).format("YYYY-MM-DD"),
      };
      await api.post("/production-batches", payload);
      message.success("生产批次创建成功");
      setModalOpen(false);
      form.resetFields();
      setSelectedProduct(undefined);
      setPage(1);
      refresh();
    } catch {
      message.error("创建失败");
    }
  };

  const columns: ColumnsType<ProductionBatch> = [
    { title: "批次号", dataIndex: "batch_code", key: "batch_code" },
    { title: "生产日期", dataIndex: "production_date", key: "production_date" },
    { title: "过期日期", dataIndex: "expiry_date", key: "expiry_date" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = BATCH_STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">生产批次管理</Title>
        <Space>
          <Select
            placeholder="按产品筛选"
            allowClear
            style={{ width: 200 }}
            value={filterProduct}
            onChange={(v) => { setFilterProduct(v); setPage(1); }}
            options={products.map((p) => ({ value: p.id, label: p.name }))}
            showSearch
            optionFilterProp="label"
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
            新建批次
          </Button>
        </Space>
      </div>
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
      <Modal
        title="新建生产批次"
        open={modalOpen}
        onCancel={() => { setModalOpen(false); setSelectedProduct(undefined); }}
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
              onChange={(v) => { setSelectedProduct(v); fetchSKUs(v); }}
            />
          </Form.Item>
          <Form.Item name="sku_id" label="关联 SKU" rules={[{ required: true, message: "请选择 SKU" }]}>
            <Select
              placeholder="选择 SKU"
              options={skus.map((s) => ({ value: s.id, label: s.name }))}
              disabled={!selectedProduct}
            />
          </Form.Item>
          <Form.Item name="batch_code" label="批次号" rules={[{ required: true, message: "请输入批次号" }]}>
            <Input placeholder="例如 PB-2026-001" />
          </Form.Item>
          <Space>
            <Form.Item name="production_date" label="生产日期" rules={[{ required: true, message: "请选择" }]}>
              <DatePicker />
            </Form.Item>
            <Form.Item name="expiry_date" label="过期日期" rules={[{ required: true, message: "请选择" }]}>
              <DatePicker />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  );
}
