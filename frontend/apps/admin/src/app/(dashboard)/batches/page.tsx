"use client";

import { useEffect, useState, useCallback } from "react";
import { useCrud } from "@/lib/hooks";
import { App, Button, DatePicker, Form, Input, Modal, Select, Space, Table, Tag, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import dayjs from "dayjs";

const { Title } = Typography;

interface ProductionBatch {
  id: string;
  product_id: string;
  product_name?: string;
  sku_id: string;
  sku_name?: string;
  batch_code: string;
  production_date: string;
  expiry_date: string;
  origin?: string;
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
  const { message } = App.useApp();
  const [products, setProducts] = useState<Product[]>([]);
  const [skus, setSKUs] = useState<SKU[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editItem, setEditItem] = useState<ProductionBatch | null>(null);
  const [form] = Form.useForm();
  const [selectedProduct, setSelectedProduct] = useState<string | undefined>(undefined);

  const { items: batches, total, page, loading, setPage, setFilter, create, update } = useCrud<ProductionBatch>("/production-batches");

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

  const openCreate = () => {
    setEditItem(null);
    form.resetFields();
    setSelectedProduct(undefined);
    setSKUs([]);
    setModalOpen(true);
  };

  const openEdit = async (batch: ProductionBatch) => {
    setEditItem(batch);
    setSelectedProduct(batch.product_id);
    await fetchSKUs(batch.product_id);
    form.setFieldsValue({
      ...batch,
      production_date: dayjs(batch.production_date),
      expiry_date: dayjs(batch.expiry_date),
    });
    setModalOpen(true);
  };

  const handleSubmit = async (values: Record<string, unknown>) => {
    try {
      const payload: Record<string, unknown> = {
        ...values,
        production_date: (values.production_date as dayjs.Dayjs).format("YYYY-MM-DD"),
        expiry_date: (values.expiry_date as dayjs.Dayjs).format("YYYY-MM-DD"),
      };
      if (editItem) {
        const updatePayload = { ...payload };
        delete updatePayload.product_id;
        delete updatePayload.sku_id;
        await update(editItem.id, updatePayload);
        message.success("生产批次更新成功");
      } else {
        await create(payload);
        message.success("生产批次创建成功");
      }
      setModalOpen(false);
      form.resetFields();
      setSelectedProduct(undefined);
    } catch {
      message.error("创建失败");
    }
  };

  const columns: ColumnsType<ProductionBatch> = [
    { title: "产品", dataIndex: "product_name", key: "product_name", render: (v?: string) => v || "-" },
    { title: "SKU", dataIndex: "sku_name", key: "sku_name", render: (v?: string) => v || "-" },
    { title: "批次号", dataIndex: "batch_code", key: "batch_code" },
    { title: "生产日期", dataIndex: "production_date", key: "production_date" },
    { title: "过期日期", dataIndex: "expiry_date", key: "expiry_date" },
    { title: "产地", dataIndex: "origin", key: "origin", render: (v?: string) => v || "-" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = BATCH_STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record) => <Button type="link" size="small" onClick={() => openEdit(record)}>编辑</Button>,
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
            onChange={(v) => setFilter(v ? { product_id: v } : {})}
            options={products.map((p) => ({ value: p.id, label: p.name }))}
            showSearch
            optionFilterProp="label"
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
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
        title={editItem ? "编辑生产批次" : "新建生产批次"}
        open={modalOpen}
        onCancel={() => { setModalOpen(false); setSelectedProduct(undefined); setEditItem(null); }}
        onOk={() => form.submit()}
        width={500}
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit}>
          <Form.Item name="product_id" label="关联产品" rules={[{ required: true, message: "请选择产品" }]}>
            <Select
              placeholder="选择产品"
              options={products.map((p) => ({ value: p.id, label: p.name }))}
              showSearch
              optionFilterProp="label"
              onChange={(v) => { setSelectedProduct(v); fetchSKUs(v); }}
              disabled={!!editItem}
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
          <Form.Item name="origin" label="批次产地">
            <Input placeholder="例如 黑龙江省哈尔滨市五常市" />
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
