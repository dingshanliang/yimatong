"use client";

import { useState } from "react";
import {
  Table, Button, Space, Input, Modal, Form, Tag, Typography, message,
} from "antd";
import { PlusOutlined, SearchOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { usePaginatedList } from "@/lib/hooks";

const { Title } = Typography;

interface Brand {
  id: string;
  name: string;
  logo_url?: string;
  description?: string;
  status: string;
  created_at: string;
}

export default function BrandsPage() {
  const [search, setSearch] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [editItem, setEditItem] = useState<Brand | null>(null);
  const [form] = Form.useForm();

  const { items: brands, total, page, loading, setPage, refresh } = usePaginatedList<Brand>(
    async ({ page, page_size }) => {
      try {
        const params: Record<string, string | number> = { page, page_size };
        if (search) params.name = search;
        const { data } = await api.get("/brands", { params });
        return { items: data.items || [], total: data.total || 0 };
      } catch {
        message.error("加载品牌列表失败");
        return { items: [], total: 0 };
      }
    },
    [search]
  );

  const openCreate = () => {
    setEditItem(null);
    form.resetFields();
    setModalOpen(true);
  };

  const openEdit = (brand: Brand) => {
    setEditItem(brand);
    form.setFieldsValue(brand);
    setModalOpen(true);
  };

  const handleSubmit = async (values: Record<string, string>) => {
    try {
      if (editItem) {
        await api.patch(`/brands/${editItem.id}`, values);
        message.success("品牌更新成功");
      } else {
        await api.post("/brands", values);
        message.success("品牌创建成功");
      }
      setModalOpen(false);
      form.resetFields();
      setPage(1);
      refresh();
    } catch {
      message.error(editItem ? "更新失败" : "创建失败");
    }
  };

  const columns: ColumnsType<Brand> = [
    { title: "品牌名称", dataIndex: "name", key: "name" },
    { title: "描述", dataIndex: "description", key: "description", ellipsis: true },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => (
        <Tag color={s === "active" ? "green" : "default"}>{s === "active" ? "启用" : "停用"}</Tag>
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
      render: (_: unknown, record: Brand) => (
        <Button type="link" size="small" onClick={() => openEdit(record)}>编辑</Button>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">品牌管理</Title>
        <Space>
          <Input
            placeholder="搜索品牌名称"
            prefix={<SearchOutlined />}
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            allowClear
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建品牌
          </Button>
        </Space>
      </div>
      <Table
        columns={columns}
        dataSource={brands}
        rowKey="id"
        loading={loading}
        pagination={{
          current: page, total, pageSize: 20, onChange: setPage,
          showTotal: (t) => `共 ${t} 条`,
        }}
      />
      <Modal
        title={editItem ? "编辑品牌" : "新建品牌"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit}>
          <Form.Item name="name" label="品牌名称" rules={[{ required: true, message: "请输入品牌名称" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="logo_url" label="Logo URL">
            <Input placeholder="https://..." />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
