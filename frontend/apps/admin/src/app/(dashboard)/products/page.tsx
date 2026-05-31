"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { App, Button, Form, Input, Modal, Progress, Select, Space, Table, Tag, Typography } from "antd";
import { PlusOutlined, SearchOutlined, RobotOutlined, ProfileOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import ImageUploadInput from "@/components/ImageUploadInput";
import api from "@/lib/api";
import { useCrud } from "@/lib/hooks";
import { formatDate } from "@/lib/format";
import { AIDrawer } from "./_components/AIDrawer";
import type { Product, Brand } from "./_components/types";

const { Title } = Typography;

export default function ProductsPage() {
  const router = useRouter();
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
    {
      title: "产品名称",
      dataIndex: "name",
      key: "name",
      render: (v: string, record) => (
        <Button type="link" className="!px-0" onClick={() => router.push(`/products/${record.id}`)}>
          {v}
        </Button>
      ),
    },
    { title: "品牌", dataIndex: "brand_name", key: "brand_name", render: (v?: string) => v || "-" },
    { title: "品类", dataIndex: "category", key: "category", render: (v?: string) => v || "-" },
    { title: "产地", dataIndex: "origin", key: "origin", render: (v?: string) => v || "-" },
    {
      title: "资料完整度",
      key: "profile",
      width: 140,
      render: (_: unknown, record) => {
        const completed = [record.name, record.brand_id, record.category, record.origin, record.image_url, record.description || record.story_content].filter(Boolean).length;
        return <Progress percent={Math.round((completed / 6) * 100)} size="small" />;
      },
    },
    { title: "状态", dataIndex: "status", key: "status", render: (status: string) => <Tag color={status === "active" ? "green" : "default"}>{status === "active" ? "启用" : status}</Tag> },
    { title: "创建时间", dataIndex: "created_at", key: "created_at", render: (v?: string) => formatDate(v) },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: Product) => (
        <Space>
          <Button type="link" size="small" onClick={() => openEdit(record)}>编辑</Button>
          <Button type="link" size="small" icon={<ProfileOutlined />} onClick={() => router.push(`/products/${record.id}`)}>工作台</Button>
        </Space>
      ),
    },
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
          <Form.Item name="origin" label="产地"><Input placeholder="例如 黑龙江省哈尔滨市五常市" /></Form.Item>
          <Form.Item
            name="image_url"
            label="产品主图（可选）"
            extra="用于溯源页产品展示。可直接上传，也可粘贴公开图片链接。"
            rules={[{ type: "url", message: "请输入以 http:// 或 https:// 开头的图片链接" }]}
          >
            <ImageUploadInput module="product-image" previewAlt="产品主图预览" />
          </Form.Item>
          <Form.Item name="story_title" label="故事标题"><Input placeholder="例如 来自核心产区的安心好物" /></Form.Item>
          <Form.Item name="story_content" label="品牌/产品故事"><Input.TextArea rows={4} /></Form.Item>
          <Form.Item name="description" label="描述"><Input.TextArea rows={3} data-testid="product-description-input" /></Form.Item>
        </Form>
      </Modal>
      <AIDrawer open={aiDrawerOpen} onClose={() => setAiDrawerOpen(false)} form={form} />
    </div>
  );
}
