"use client";

import { useState } from "react";
import { App, Button, Form, Input, Modal, Space, Table, Tag, Typography } from "antd";
import { PlusOutlined, SearchOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { useCrud } from "@/lib/hooks";

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
  const { message } = App.useApp();
  const [search, setSearch] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [editItem, setEditItem] = useState<Brand | null>(null);
  const [form] = Form.useForm();

  const {
    items: brands, total, page, loading, setPage,
    setFilter, create, update,
  } = useCrud<Brand>("/brands");

  const handleSearch = (value: string) => {
    setSearch(value);
    setFilter(value ? { name: value } : {});
  };

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
        await update(editItem.id, values);
        message.success("品牌更新成功");
      } else {
        await create(values);
        message.success("品牌创建成功");
      }
      setModalOpen(false);
      form.resetFields();
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
            onChange={(e) => handleSearch(e.target.value)}
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
