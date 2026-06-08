"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { App, Button, Divider, Form, Input, Modal, Popconfirm, Progress, Select, Space, Switch, Table, Tag, Typography } from "antd";
import { PlusOutlined, SearchOutlined, RobotOutlined, ProfileOutlined, DeleteOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import ImageUploadInput from "@/components/ImageUploadInput";
import api, { extractErrorMessage } from "@/lib/api";
import { useCrud } from "@/lib/hooks";
import { useCategories } from "@/lib/use-categories";
import { formatDate } from "@/lib/format";
import { AIDrawer } from "./_components/AIDrawer";
import type { Product, Brand } from "./_components/types";

const { Title } = Typography;

type ProductFormValues = {
  name: string;
  brand_id: string;
  category?: string;
  origin?: string;
  image_url?: string;
};

export default function ProductsPage() {
  const router = useRouter();
  const { message } = App.useApp();
  const [brands, setBrands] = useState<Brand[]>([]);
  const [search, setSearch] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [editItem, setEditItem] = useState<Product | null>(null);
  const [form] = Form.useForm();
  const [brandForm] = Form.useForm();
  const [aiDrawerOpen, setAiDrawerOpen] = useState(false);
  const [brandModalOpen, setBrandModalOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [brandCreating, setBrandCreating] = useState(false);
  const [categorySearch, setCategorySearch] = useState("");

  const { items: products, total, page, loading, setPage, setFilter, update, remove } = useCrud<Product>("/products");
  const { categories: tenantCategories } = useCategories();

  const fetchBrands = useCallback(async () => {
    try {
      const { data } = await api.get("/brands", { params: { page_size: 100 } });
      setBrands(data.items || []);
    } catch {
      // Brand loading failure is surfaced when the user tries to submit.
    }
  }, []);

  useEffect(() => {
    void fetchBrands();
  }, [fetchBrands]);

  const categoryOptions = useMemo(() => {
    const base = tenantCategories.slice();
    const productCategories = products.map((p) => p.category).filter(Boolean) as string[];
    for (const cat of productCategories) {
      if (!base.some((c) => c.toLowerCase() === cat.toLowerCase())) {
        base.push(cat);
      }
    }
    const typed = categorySearch.trim();
    if (typed && !base.some((c) => c.toLowerCase() === typed.toLowerCase())) {
      base.push(typed);
    }
    return base.map((value) => ({ value, label: value }));
  }, [categorySearch, products, tenantCategories]);

  const openCreate = () => {
    setEditItem(null);
    setCategorySearch("");
    form.resetFields();
    setModalOpen(true);
  };

  const openEdit = (product: Product) => {
    setEditItem(product);
    setCategorySearch(product.category || "");
    form.setFieldsValue({
      name: product.name,
      brand_id: product.brand_id,
      category: product.category,
      origin: product.origin,
      image_url: product.image_url,
    });
    setModalOpen(true);
  };

  const handleSubmit = async (values: ProductFormValues) => {
    const payload = {
      name: values.name,
      brand_id: values.brand_id,
      category: values.category,
      origin: values.origin,
      image_url: values.image_url,
    };
    setSaving(true);
    try {
      if (editItem) {
        await update(editItem.id, payload);
        message.success("产品基础信息已更新");
        setModalOpen(false);
        form.resetFields();
        return;
      }
      const { data } = await api.post<Product>("/products", payload);
      message.success("产品已创建，请继续完善资料");
      setModalOpen(false);
      form.resetFields();
      router.push(`/products/${data.id}`);
    } catch {
      message.error(editItem ? "更新失败" : "创建失败");
    } finally {
      setSaving(false);
    }
  };

  const openQuickBrandCreate = () => {
    brandForm.resetFields();
    setBrandModalOpen(true);
  };

  const handleQuickBrandCreate = async (values: { name: string }) => {
    setBrandCreating(true);
    try {
      const { data } = await api.post<Brand>("/brands", { name: values.name });
      setBrands((prev) => (prev.some((brand) => brand.id === data.id) ? prev : [data, ...prev]));
      form.setFieldValue("brand_id", data.id);
      message.success("品牌已创建并选中");
      setBrandModalOpen(false);
      brandForm.resetFields();
    } catch {
      message.error("品牌创建失败");
    } finally {
      setBrandCreating(false);
    }
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
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (status: string, record: Product) => {
        if (status === "draft") {
          return <Tag color="orange">草稿</Tag>;
        }
        return (
          <Switch
            checked={status === "active"}
            checkedChildren="启用"
            unCheckedChildren="禁用"
            onChange={async (checked) => {
              try {
                await update(record.id, { status: checked ? "active" : "inactive" });
                message.success(checked ? "已启用" : "已禁用");
              } catch {
                message.error("状态更新失败");
              }
            }}
          />
        );
      },
    },
    { title: "创建时间", dataIndex: "created_at", key: "created_at", render: (v?: string) => formatDate(v) },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: Product) => (
        <Space>
          <Button type="link" size="small" onClick={() => openEdit(record)}>基础信息</Button>
          <Button type="link" size="small" icon={<ProfileOutlined />} onClick={() => router.push(`/products/${record.id}`)}>工作台</Button>
          <Popconfirm
            title="确认删除"
            description={`删除产品「${record.name}」？有关联资源时将被阻止。`}
            onConfirm={async () => {
              try {
                await remove(record.id);
                message.success("产品已删除");
              } catch (err) {
                message.error(extractErrorMessage(err, "删除失败，请检查是否有关联资源"));
              }
            }}
            okText="删除"
            okButtonProps={{ danger: true }}
          >
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
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
      <Modal
        title={editItem ? "编辑产品基础信息" : "新建产品档案"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
        okText={editItem ? "保存基础信息" : "创建并进入工作台"}
        confirmLoading={saving}
        width={560}
        forceRender
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit}>
          <Form.Item name="name" label="产品名称" rules={[{ required: true, message: "请输入产品名称" }]}><Input data-testid="product-name-input" /></Form.Item>
          <Form.Item name="brand_id" label="品牌" rules={[{ required: true, message: "请选择品牌" }]}>
            <Select
              placeholder="选择品牌"
              showSearch
              optionFilterProp="label"
              options={brands.map((b) => ({ value: b.id, label: b.name }))}
              data-testid="product-brand-select"
              notFoundContent={<Button type="link" className="!px-0" onClick={openQuickBrandCreate}>新建品牌</Button>}
              popupRender={(menu) => (
                <>
                  {menu}
                  <Divider className="!my-2" />
                  <Button type="link" className="!px-0" icon={<PlusOutlined />} onMouseDown={(event) => event.preventDefault()} onClick={openQuickBrandCreate}>
                    新建品牌
                  </Button>
                </>
              )}
            />
          </Form.Item>
          <Form.Item name="category" label="品类">
            <Select
              showSearch
              allowClear
              placeholder="选择或输入品类"
              options={categoryOptions}
              onSearch={(value) => {
                setCategorySearch(value);
                form.setFieldValue("category", value || undefined);
              }}
              onClear={() => {
                setCategorySearch("");
                form.setFieldValue("category", undefined);
              }}
              filterOption={(input, option) => String(option?.label || "").toLowerCase().includes(input.toLowerCase())}
              data-testid="product-category-input"
            />
          </Form.Item>
          <Form.Item name="origin" label="产地"><Input placeholder="例如 黑龙江省哈尔滨市五常市" /></Form.Item>
          <Form.Item
            name="image_url"
            label="产品主图"
            rules={[{ type: "url", message: "请输入以 http:// 或 https:// 开头的图片链接" }]}
          >
            <ImageUploadInput
              module="product-image"
              previewAlt="产品主图预览"
              variant="uploadFirst"
              emptyText="用于扫码页和产品资料展示，支持 PNG、JPG、WebP，单张不超过 5MB"
            />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title="快速新建品牌"
        open={brandModalOpen}
        onCancel={() => setBrandModalOpen(false)}
        onOk={() => brandForm.submit()}
        confirmLoading={brandCreating}
        okText="创建品牌"
        forceRender
      >
        <Form form={brandForm} layout="vertical" onFinish={handleQuickBrandCreate}>
          <Form.Item name="name" label="品牌名称" rules={[{ required: true, message: "请输入品牌名称" }]}>
            <Input placeholder="例如 青岭良仓" />
          </Form.Item>
        </Form>
      </Modal>
      <AIDrawer open={aiDrawerOpen} onClose={() => setAiDrawerOpen(false)} form={form} />
    </div>
  );
}
