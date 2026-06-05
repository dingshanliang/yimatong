"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { App, Button, Input, Space, Table, Tag, Typography } from "antd";
import { PlusOutlined, SearchOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import BrandFormModal from "./_components/BrandFormModal";
import { useCrud } from "@/lib/hooks";
import { formatDate } from "@/lib/format";

const { Title } = Typography;

interface Brand {
  id: string;
  name: string;
  logo_url?: string;
  description?: string;
  status: string;
  created_at?: string;
}

export default function BrandsPage() {
  const router = useRouter();
  const [search, setSearch] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [editItem, setEditItem] = useState<Brand | null>(null);

  const {
    items: brands, total, page, loading, setPage,
    setFilter, mutate,
  } = useCrud<Brand>("/brands");

  const handleSearch = (value: string) => {
    setSearch(value);
    setFilter(value ? { name: value } : {});
  };

  const openCreate = () => {
    setEditItem(null);
    setModalOpen(true);
  };

  const openEdit = (brand: Brand) => {
    setEditItem(brand);
    setModalOpen(true);
  };

  const handleSuccess = () => {
    setModalOpen(false);
    mutate();
  };

  const columns: ColumnsType<Brand> = [
    {
      title: "品牌名称",
      dataIndex: "name",
      key: "name",
      render: (v: string, record: Brand) => (
        <Button type="link" className="!px-0" onClick={() => router.push(`/brands/${record.id}`)}>
          {v}
        </Button>
      ),
    },
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
      render: (v?: string) => formatDate(v),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: Brand) => (
        <Button type="link" size="small" onClick={() => router.push(`/brands/${record.id}`)}>
          查看
        </Button>
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
      <BrandFormModal
        open={modalOpen}
        initialValues={editItem || undefined}
        mode={editItem ? "edit" : "create"}
        onSuccess={handleSuccess}
        onCancel={() => setModalOpen(false)}
      />
    </div>
  );
}
