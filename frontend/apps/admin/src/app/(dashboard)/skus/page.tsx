"use client";

import { useEffect, useState, useCallback } from "react";
import { usePaginatedList } from "@/lib/hooks";
import { App, Button, Form, Input, Modal, Select, Space, Table, Tag, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title } = Typography;

interface SKU {
  id: string;
  product_id: string;
  code: string;
  name: string;
  specifications?: Record<string, string>;
  status: string;
}

interface Product {
  id: string;
  name: string;
}

export default function SKUsPage() {
  const { message } = App.useApp();
  const [products, setProducts] = useState<Product[]>([]);
  const [filterProduct, setFilterProduct] = useState<string | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [editItem, setEditItem] = useState<SKU | null>(null);
  const [form] = Form.useForm();

  const { items: skus, total, page, loading, setPage, refresh } = usePaginatedList<SKU>(
    async ({ page, page_size }) => {
      try {
        const params: Record<string, string | number> = { page, page_size };
        if (filterProduct) params.product_id = filterProduct;
        const { data } = await api.get("/skus", { params });
        return { items: data.items || [], total: data.total || 0 };
      } catch {
        message.error("加载 SKU 列表失败");
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

  useEffect(() => { fetchProducts(); }, [fetchProducts]);

  const openCreate = () => {
    setEditItem(null);
    form.resetFields();
    setModalOpen(true);
  };

  const openEdit = (sku: SKU) => {
    setEditItem(sku);
    form.setFieldsValue({ ...sku, specifications: sku.specifications ? JSON.stringify(sku.specifications) : "" });
    setModalOpen(true);
  };

  const handleSubmit = async (values: Record<string, string>) => {
    try {
      const payload = { ...values };
      if (payload.specifications) {
        try { payload.specifications = JSON.parse(payload.specifications); }
        catch { /* keep as string if not valid JSON */ }
      }
      if (editItem) {
        await api.patch(`/skus/${editItem.id}`, payload);
        message.success("SKU 更新成功");
      } else {
        await api.post("/skus", payload);
        message.success("SKU 创建成功");
      }
      setModalOpen(false);
      form.resetFields();
      setPage(1);
      refresh();
    } catch {
      message.error(editItem ? "更新失败" : "创建失败");
    }
  };

  const columns: ColumnsType<SKU> = [
    { title: "SKU 编码", dataIndex: "code", key: "code" },
    { title: "名称", dataIndex: "name", key: "name" },
    {
      title: "规格",
      dataIndex: "specifications",
      key: "specifications",
      render: (v: Record<string, string>) => v ? Object.entries(v).map(([k, val]) => `${k}: ${val}`).join(", ") : "-",
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => (
        <Tag color={s === "active" ? "green" : "default"}>{s === "active" ? "启用" : "停用"}</Tag>
      ),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: SKU) => (
        <Button type="link" size="small" onClick={() => openEdit(record)}>编辑</Button>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">SKU 管理</Title>
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
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建 SKU
          </Button>
        </Space>
      </div>
      <Table
        columns={columns}
        dataSource={skus}
        rowKey="id"
        loading={loading}
        pagination={{
          current: page, total, pageSize: 20, onChange: setPage,
          showTotal: (t) => `共 ${t} 条`,
        }}
      />
      <Modal
        title={editItem ? "编辑 SKU" : "新建 SKU"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit}>
          {!editItem && (
            <Form.Item name="product_id" label="关联产品" rules={[{ required: true, message: "请选择产品" }]}>
              <Select
                placeholder="选择产品"
                options={products.map((p) => ({ value: p.id, label: p.name }))}
                showSearch
                optionFilterProp="label"
              />
            </Form.Item>
          )}
          <Form.Item name="code" label="SKU 编码" rules={[{ required: true, message: "请输入编码" }]}>
            <Input placeholder="例如 SKU-001" />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="specifications" label="规格（JSON）">
            <Input.TextArea rows={3} placeholder='{"颜色":"红色","尺寸":"500ml"}' />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
