"use client";

import { useEffect, useState } from "react";
import {
  Table,
  Button,
  Space,
  Input,
  Modal,
  Form,
  Select,
  message,
  Tag,
  Typography,
} from "antd";
import { PlusOutlined, SearchOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { usePaginatedList } from "@/lib/hooks";

const { Title } = Typography;

interface Product {
  id: string;
  name: string;
  brand_id: string;
  brand_name?: string;
  category?: string;
  status: string;
  created_at: string;
}

interface Brand {
  id: string;
  name: string;
}

export default function ProductsPage() {
  const [brands, setBrands] = useState<Brand[]>([]);
  const [search, setSearch] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [editItem, setEditItem] = useState<Product | null>(null);
  const [form] = Form.useForm();

  const { items: products, total, page, loading, setPage, refresh } = usePaginatedList<Product>(
    async ({ page, page_size }) => {
      try {
        const params: Record<string, string | number> = { page, page_size };
        if (search) params.search = search;
        const { data } = await api.get("/products", { params });
        return { items: data.items || [], total: data.total || 0 };
      } catch {
        message.error("加载产品列表失败");
        return { items: [], total: 0 };
      }
    },
    [search]
  );

  const fetchBrands = async () => {
    try {
      const { data } = await api.get("/brands", { params: { page_size: 100 } });
      setBrands(data.items || []);
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    fetchBrands();
  }, []);

  const openCreate = () => {
    setEditItem(null);
    form.resetFields();
    setModalOpen(true);
  };

  const openEdit = (product: Product) => {
    setEditItem(product);
    form.setFieldsValue(product);
    setModalOpen(true);
  };

  const handleCreate = async (values: Record<string, string>) => {
    try {
      if (editItem) {
        await api.patch(`/products/${editItem.id}`, values);
        message.success("产品更新成功");
      } else {
        await api.post("/products", values);
        message.success("产品创建成功");
      }
      setModalOpen(false);
      form.resetFields();
      setPage(1);
      refresh();
    } catch {
      message.error(editItem ? "更新失败" : "创建失败");
    }
  };

  const columns: ColumnsType<Product> = [
    { title: "产品名称", dataIndex: "name", key: "name" },
    { title: "品牌", dataIndex: "brand_name", key: "brand_name" },
    { title: "品类", dataIndex: "category", key: "category" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (status: string) => (
        <Tag color={status === "active" ? "green" : "default"}>
          {status === "active" ? "启用" : status}
        </Tag>
      ),
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
      render: (_: unknown, record: Product) => (
        <Button type="link" size="small" onClick={() => openEdit(record)}>编辑</Button>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">
          产品管理
        </Title>
        <Space>
          <Input
            placeholder="搜索产品名称"
            prefix={<SearchOutlined />}
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            allowClear
          />
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={openCreate}
          >
            新建产品
          </Button>
        </Space>
      </div>
      <Table
        columns={columns}
        dataSource={products}
        rowKey="id"
        loading={loading}
        pagination={{
          current: page,
          total,
          pageSize: 20,
          onChange: setPage,
          showTotal: (t) => `共 ${t} 条`,
        }}
      />
      <Modal
        title={editItem ? "编辑产品" : "新建产品"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item
            name="name"
            label="产品名称"
            rules={[{ required: true, message: "请输入产品名称" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="brand_id"
            label="品牌"
            rules={[{ required: true, message: "请选择品牌" }]}
          >
            <Select
              placeholder="选择品牌"
              options={brands.map((b) => ({ value: b.id, label: b.name }))}
            />
          </Form.Item>
          <Form.Item name="category" label="品类">
            <Input />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
