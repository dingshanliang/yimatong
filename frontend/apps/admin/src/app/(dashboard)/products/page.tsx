"use client";

import { useEffect, useState } from "react";
import { App, Button, Form, Input, Modal, Select, Space, Table, Tag, Typography } from "antd";
import { PlusOutlined, SearchOutlined, RobotOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { useCrud } from "@/lib/hooks";
import { AIDrawer } from "./_components/AIDrawer";
import type { Product, Brand } from "./_components/types";

const { Title } = Typography;

export default function ProductsPage() {
  const { message } = App.useApp();
  const [brands, setBrands] = useState<Brand[]>([]);
  const [search, setSearch] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [editItem, setEditItem] = useState<Product | null>(null);
  const [form] = Form.useForm();
  const [aiDrawerOpen, setAiDrawerOpen] = useState(false);

  const { items: products, total, page, loading, setPage, setFilter, create, update } = useCrud<Product>("/products");

  useEffect(() => {
    (async () => { try { const { data } = await api.get("/brands", { params: { page_size: 100 } }); setBrands(data.items || []); } catch { /* ignore */ } })();
  }, []);

  const openCreate = () => { setEditItem(null); form.resetFields(); setModalOpen(true); };
  const openEdit = (product: Product) => { setEditItem(product); form.setFieldsValue(product); setModalOpen(true); };

  const handleCreate = async (values: Record<string, string>) => {
    try {
      if (editItem) { await update(editItem.id, values); message.success("产品更新成功"); }
      else { await create(values); message.success("产品创建成功"); }
      setModalOpen(false); form.resetFields();
    } catch { message.error(editItem ? "更新失败" : "创建失败"); }
  };

  const columns: ColumnsType<Product> = [
    { title: "产品名称", dataIndex: "name", key: "name" },
    { title: "品牌", dataIndex: "brand_name", key: "brand_name" },
    { title: "品类", dataIndex: "category", key: "category" },
    { title: "状态", dataIndex: "status", key: "status", render: (status: string) => <Tag color={status === "active" ? "green" : "default"}>{status === "active" ? "启用" : status}</Tag> },
    { title: "创建时间", dataIndex: "created_at", key: "created_at", render: (v: string) => new Date(v).toLocaleDateString("zh-CN") },
    { title: "操作", key: "actions", render: (_: unknown, record: Product) => <Button type="link" size="small" onClick={() => openEdit(record)}>编辑</Button> },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">产品管理</Title>
        <Space>
          <Input placeholder="搜索产品名称" prefix={<SearchOutlined />} value={search}
            onChange={(e) => { const val = e.target.value; setSearch(val); setFilter(val ? { search: val } : {}); }} allowClear />
          <Button icon={<RobotOutlined />} onClick={() => setAiDrawerOpen(true)}>AI 智能识别</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建产品</Button>
        </Space>
      </div>
      <Table columns={columns} dataSource={products} rowKey="id" loading={loading}
        pagination={{ current: page, total, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }} />
      <Modal title={editItem ? "编辑产品" : "新建产品"} open={modalOpen} onCancel={() => setModalOpen(false)} onOk={() => form.submit()}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="产品名称" rules={[{ required: true, message: "请输入产品名称" }]}><Input data-testid="product-name-input" /></Form.Item>
          <Form.Item name="brand_id" label="品牌" rules={[{ required: true, message: "请选择品牌" }]}>
            <Select placeholder="选择品牌" options={brands.map((b) => ({ value: b.id, label: b.name }))} data-testid="product-brand-select" />
          </Form.Item>
          <Form.Item name="category" label="品类"><Input data-testid="product-category-input" /></Form.Item>
          <Form.Item name="description" label="描述"><Input.TextArea rows={3} data-testid="product-description-input" /></Form.Item>
        </Form>
      </Modal>
      <AIDrawer open={aiDrawerOpen} onClose={() => setAiDrawerOpen(false)} form={form} />
    </div>
  );
}
