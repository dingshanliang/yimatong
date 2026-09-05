"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  Alert,
  App,
  Button,
  Empty,
  Input,
  Popconfirm,
  Space,
  Switch,
  Table,
  Typography,
} from "antd";
import {
  DeleteOutlined,
  PlusOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import BrandFormModal from "./_components/BrandFormModal";
import { extractErrorMessage } from "@/lib/api";
import { useCrud } from "@/lib/hooks";
import { formatDate } from "@/lib/format";
import { useAuthStore } from "@/lib/auth";
import { catalogAccessForPrincipal } from "@/lib/catalog-access";
import { useTenantPlanReadOnly } from "../_components/TenantPlanReadOnly";

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
  const user = useAuthStore((state) => state.user);
  const access = catalogAccessForPrincipal(user);

  if (!access.canRead) {
    return <Alert type="warning" showIcon title="当前账号无权访问品牌目录" />;
  }

  return (
    <BrandsCatalog canWrite={access.canWrite} canDelete={access.canDelete} />
  );
}

function BrandsCatalog({
  canWrite,
  canDelete,
}: {
  canWrite: boolean;
  canDelete: boolean;
}) {
  const router = useRouter();
  const { message } = App.useApp();
  const planReadOnly = useTenantPlanReadOnly();
  const [search, setSearch] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [editItem, setEditItem] = useState<Brand | null>(null);

  const openEdit = (brand: Brand) => {
    setEditItem(brand);
    setModalOpen(true);
  };

  const handleSuccess = () => {
    setModalOpen(false);
    mutate();
  };

  const {
    items: brands,
    total,
    page,
    loading,
    error,
    setPage,
    setFilter,
    update,
    remove,
    mutate,
    retry,
  } = useCrud<Brand>("/brands");
  const writesDisabled = planReadOnly || !canWrite;

  const handleSearch = (value: string) => {
    setSearch(value);
    setFilter(value ? { name: value } : {});
  };

  const openCreate = () => {
    setEditItem(null);
    setModalOpen(true);
  };

  const columns: ColumnsType<Brand> = [
    {
      title: "品牌名称",
      dataIndex: "name",
      key: "name",
      render: (v: string, record: Brand) => (
        <Button
          type="link"
          className="!px-0"
          onClick={() => router.push(`/brands/${record.id}`)}
        >
          {v}
        </Button>
      ),
    },
    {
      title: "描述",
      dataIndex: "description",
      key: "description",
      ellipsis: true,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string, record: Brand) => (
        <Switch
          checked={s === "active"}
          disabled={writesDisabled}
          checkedChildren="启用"
          unCheckedChildren="停用"
          onChange={async (checked) => {
            try {
              await update(record.id, {
                status: checked ? "active" : "inactive",
              });
              message.success(checked ? "已启用" : "已停用");
            } catch (err) {
              message.error(extractErrorMessage(err, "状态更新失败"));
            }
          }}
        />
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
        <Space>
          <Button
            type="link"
            size="small"
            onClick={() => router.push(`/brands/${record.id}`)}
          >
            查看
          </Button>
          <Button
            type="link"
            size="small"
            disabled={writesDisabled}
            onClick={() => openEdit(record)}
          >
            编辑
          </Button>
          {canDelete && (
            <Popconfirm
              title="确认删除"
              description={`删除品牌「${record.name}」？有关联资源时将被阻止。`}
              onConfirm={async () => {
                try {
                  await remove(record.id);
                  message.success("品牌已删除");
                } catch (err) {
                  message.error(
                    extractErrorMessage(err, "删除失败，请检查是否有关联资源")
                  );
                }
              }}
              okText="删除"
              okButtonProps={{ danger: true }}
            >
              <Button
                type="link"
                size="small"
                danger
                disabled={writesDisabled}
                icon={<DeleteOutlined />}
              >
                删除
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">
          品牌管理
        </Title>
        <Space>
          <Input
            placeholder="搜索品牌名称"
            prefix={<SearchOutlined />}
            value={search}
            onChange={(e) => handleSearch(e.target.value)}
            allowClear
          />
          <Button
            type="primary"
            icon={<PlusOutlined />}
            disabled={writesDisabled}
            onClick={openCreate}
          >
            新建品牌
          </Button>
        </Space>
      </div>
      {Boolean(error) && (
        <Alert
          className="mb-4"
          type="error"
          showIcon
          title="品牌列表加载失败"
          action={<Button onClick={() => void retry()}>重试</Button>}
        />
      )}
      {!error && (
        <Table
          columns={columns}
          dataSource={brands}
          rowKey="id"
          loading={loading}
          locale={{ emptyText: <Empty description="暂无品牌" /> }}
          pagination={{
            current: page,
            total,
            pageSize: 20,
            onChange: setPage,
            showTotal: (t) => `共 ${t} 条`,
          }}
        />
      )}
      <BrandFormModal
        open={modalOpen}
        initialValues={editItem || undefined}
        mode={editItem ? "edit" : "create"}
        readOnly={writesDisabled}
        onSuccess={handleSuccess}
        onCancel={() => setModalOpen(false)}
      />
    </div>
  );
}
