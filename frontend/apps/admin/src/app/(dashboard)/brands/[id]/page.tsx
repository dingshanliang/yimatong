"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Empty,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { ArrowLeftOutlined, EditOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { formatDate } from "@/lib/format";
import { STATUS_COLORS } from "@/lib/status-colors";
import BrandFormModal from "../_components/BrandFormModal";
import { useAuthStore } from "@/lib/auth";
import { catalogAccessForPrincipal } from "@/lib/catalog-access";
import { useTenantPlanReadOnly } from "../../_components/TenantPlanReadOnly";

const { Title } = Typography;

interface BrandDetail {
  id: string;
  name: string;
  logo_url?: string;
  description?: string;
  status: string;
  created_at?: string;
  stats: {
    product_count: number;
    campaign_count: number;
    code_batch_count: number;
    batch_count: number;
  };
}

interface ProductItem {
  id: string;
  name: string;
  category?: string;
  status: string;
  created_at?: string;
}

interface CampaignItem {
  id: string;
  name: string;
  campaign_type: string;
  status: string;
  computed_status?: string;
  product_name?: string;
  start_at: string;
  end_at: string;
}

interface CodeBatchItem {
  id: string;
  batch_code: string;
  product_name?: string;
  sku_name?: string;
  quantity: number;
  status: string;
  code_type: string;
  created_at?: string;
}

interface BatchItem {
  id: string;
  batch_code: string;
  product_name?: string;
  sku_name?: string;
  production_date: string;
  expiry_date: string;
  status: string;
}

type TabItem = ProductItem | CampaignItem | CodeBatchItem | BatchItem;

const PRODUCT_STATUS_MAP: Record<string, { label: string; color: string }> = {
  active: { label: "启用", color: STATUS_COLORS.success },
  inactive: { label: "停用", color: STATUS_COLORS.neutral },
};

const CAMPAIGN_STATUS_MAP: Record<string, { label: string; color: string }> = {
  draft: { label: "草稿", color: STATUS_COLORS.neutral },
  pending: { label: "待开始", color: STATUS_COLORS.processing },
  active: { label: "进行中", color: STATUS_COLORS.success },
  paused: { label: "已暂停", color: STATUS_COLORS.warning },
  ended: { label: "已结束", color: STATUS_COLORS.neutral },
};

const CODE_STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: "待生成", color: STATUS_COLORS.neutral },
  generating: { label: "生成中", color: STATUS_COLORS.processing },
  completed: { label: "已生成", color: STATUS_COLORS.success },
  activated: { label: "已激活", color: STATUS_COLORS.processing },
  failed: { label: "失败", color: STATUS_COLORS.error },
};

const BATCH_STATUS_MAP: Record<string, { label: string; color: string }> = {
  active: { label: "有效", color: STATUS_COLORS.success },
  recalled: { label: "已召回", color: STATUS_COLORS.error },
  expired: { label: "已过期", color: STATUS_COLORS.neutral },
};

type TabKey = "products" | "campaigns" | "code-batches" | "batches";

interface TabState<T> {
  items: T[];
  total: number;
  page: number;
  loading: boolean;
  error?: boolean;
}

function createTabState<T>(): TabState<T> {
  return { items: [], total: 0, page: 1, loading: false };
}

function getErrorStatus(error: unknown): number | undefined {
  return (error as { response?: { status?: number } } | null | undefined)
    ?.response?.status;
}

export default function BrandDetailPage() {
  const user = useAuthStore((state) => state.user);
  const access = catalogAccessForPrincipal(user);

  if (!access.canRead) {
    return <Alert type="warning" showIcon title="当前账号无权访问品牌目录" />;
  }

  return <BrandDetail canWrite={access.canWrite} />;
}

function BrandDetail({ canWrite }: { canWrite: boolean }) {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const brandId = params.id;
  const { message } = App.useApp();
  const planReadOnly = useTenantPlanReadOnly();
  const writesDisabled = planReadOnly || !canWrite;

  const [brand, setBrand] = useState<BrandDetail | null>(null);
  const [brandLoading, setBrandLoading] = useState(false);
  const [brandLoadError, setBrandLoadError] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<TabKey>("products");

  const [products, setProducts] =
    useState<TabState<ProductItem>>(createTabState);
  const [campaigns, setCampaigns] =
    useState<TabState<CampaignItem>>(createTabState);
  const [codeBatches, setCodeBatches] =
    useState<TabState<CodeBatchItem>>(createTabState);
  const [batches, setBatches] = useState<TabState<BatchItem>>(createTabState);

  const fetchBrand = async () => {
    try {
      setBrandLoading(true);
      setBrandLoadError(false);
      const { data } = await api.get<BrandDetail>(`/brands/${brandId}`);
      setBrand(data);
    } catch (error) {
      const failed = getErrorStatus(error) !== 404;
      setBrandLoadError(failed);
      if (failed) message.error("获取品牌详情失败");
    } finally {
      setBrandLoading(false);
    }
  };

  const fetchTabData = async (tab: TabKey, page = 1) => {
    const endpointMap: Record<TabKey, string> = {
      products: `/brands/${brandId}/products`,
      campaigns: `/brands/${brandId}/campaigns`,
      "code-batches": `/brands/${brandId}/code-batches`,
      batches: `/brands/${brandId}/production-batches`,
    };

    const setters: Record<TabKey, (s: TabState<TabItem>) => void> = {
      products: (s) => setProducts({ ...s, items: s.items as ProductItem[] }),
      campaigns: (s) =>
        setCampaigns({ ...s, items: s.items as CampaignItem[] }),
      "code-batches": (s) =>
        setCodeBatches({ ...s, items: s.items as CodeBatchItem[] }),
      batches: (s) => setBatches({ ...s, items: s.items as BatchItem[] }),
    };

    const prev = {
      products,
      campaigns,
      "code-batches": codeBatches,
      batches,
    }[tab];

    setters[tab]({ ...prev, loading: true, error: false });

    try {
      const { data } = await api.get(endpointMap[tab], {
        params: { page, page_size: 20 },
      });
      setters[tab]({
        items: data.items || [],
        total: data.total || 0,
        page,
        loading: false,
      });
    } catch {
      message.error("获取数据失败");
      setters[tab]({ ...prev, loading: false, error: true });
    }
  };

  useEffect(() => {
    if (brandId) {
      fetchBrand();
      fetchTabData("products", 1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [brandId]);

  useEffect(() => {
    if (!brandId) return;
    const stateMap: Record<TabKey, TabState<unknown>> = {
      products,
      campaigns,
      "code-batches": codeBatches,
      batches,
    };
    if (activeTab !== "products" && stateMap[activeTab].items.length === 0) {
      fetchTabData(activeTab, 1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, brandId]);

  const productColumns: ColumnsType<ProductItem> = [
    {
      title: "产品名称",
      dataIndex: "name",
      key: "name",
      render: (v: string, record: ProductItem) => (
        <Button
          type="link"
          className="!px-0"
          onClick={() => router.push(`/products/${record.id}`)}
        >
          {v}
        </Button>
      ),
    },
    {
      title: "分类",
      dataIndex: "category",
      key: "category",
      render: (v?: string) => v || "-",
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = PRODUCT_STATUS_MAP[s] || {
          label: s,
          color: STATUS_COLORS.neutral,
        };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      render: (v?: string) => formatDate(v),
    },
  ];

  const campaignColumns: ColumnsType<CampaignItem> = [
    {
      title: "活动名称",
      dataIndex: "name",
      key: "name",
      render: (v: string, record: CampaignItem) => (
        <Button
          type="link"
          className="!px-0"
          onClick={() => router.push(`/campaigns/${record.id}`)}
        >
          {v}
        </Button>
      ),
    },
    { title: "活动类型", dataIndex: "campaign_type", key: "campaign_type" },
    {
      title: "状态",
      key: "status",
      render: (_, record: CampaignItem) => {
        const s = record.computed_status || record.status;
        const info = CAMPAIGN_STATUS_MAP[s] || {
          label: s,
          color: STATUS_COLORS.neutral,
        };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "关联产品",
      dataIndex: "product_name",
      key: "product_name",
      render: (v?: string) => v || "-",
    },
    {
      title: "起止时间",
      key: "time",
      render: (_, record: CampaignItem) =>
        `${record.start_at} 至 ${record.end_at}`,
    },
  ];

  const codeBatchColumns: ColumnsType<CodeBatchItem> = [
    {
      title: "批次号",
      dataIndex: "batch_code",
      key: "batch_code",
      render: (v: string, record: CodeBatchItem) => (
        <Button
          type="link"
          className="!px-0"
          onClick={() => router.push(`/codes/${record.id}`)}
        >
          {v}
        </Button>
      ),
    },
    {
      title: "产品",
      dataIndex: "product_name",
      key: "product_name",
      render: (v?: string) => v || "-",
    },
    {
      title: "SKU",
      dataIndex: "sku_name",
      key: "sku_name",
      render: (v?: string) => v || "-",
    },
    { title: "数量", dataIndex: "quantity", key: "quantity" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = CODE_STATUS_MAP[s] || {
          label: s,
          color: STATUS_COLORS.neutral,
        };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      render: (v?: string) => formatDate(v),
    },
  ];

  const batchColumns: ColumnsType<BatchItem> = [
    { title: "批次号", dataIndex: "batch_code", key: "batch_code" },
    {
      title: "产品",
      dataIndex: "product_name",
      key: "product_name",
      render: (v?: string) => v || "-",
    },
    {
      title: "SKU",
      dataIndex: "sku_name",
      key: "sku_name",
      render: (v?: string) => v || "-",
    },
    { title: "生产日期", dataIndex: "production_date", key: "production_date" },
    { title: "保质期至", dataIndex: "expiry_date", key: "expiry_date" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = BATCH_STATUS_MAP[s] || {
          label: s,
          color: STATUS_COLORS.neutral,
        };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
  ];

  const renderTable = <T extends object>(
    columns: ColumnsType<T>,
    state: TabState<T>,
    tab: TabKey
  ) => (
    <>
      {state.error && (
        <Alert
          className="mb-3"
          type="error"
          showIcon
          title="关联数据加载失败"
          action={
            <Button onClick={() => void fetchTabData(tab, state.page)}>
              重试
            </Button>
          }
        />
      )}
      {!state.error && (
        <Table
          columns={columns}
          dataSource={state.items}
          rowKey="id"
          loading={state.loading}
          locale={{ emptyText: <Empty description="暂无关联数据" /> }}
          pagination={{
            current: state.page,
            total: state.total,
            pageSize: 20,
            onChange: (p) => fetchTabData(tab, p),
            showTotal: (t) => `共 ${t} 条`,
          }}
        />
      )}
    </>
  );

  if (!brand && brandLoadError) {
    return (
      <Alert
        type="error"
        showIcon
        title="品牌详情加载失败"
        action={<Button onClick={() => void fetchBrand()}>重试</Button>}
      />
    );
  }

  if (!brand && !brandLoading) {
    return (
      <div className="p-6">
        <Button
          icon={<ArrowLeftOutlined />}
          onClick={() => router.push("/brands")}
        >
          返回列表
        </Button>
        <div
          className="mt-4"
          style={{ color: "var(--ymt-color-text-secondary)" }}
        >
          品牌不存在或已删除
        </div>
      </div>
    );
  }

  return (
    <div className="p-6">
      <Space className="mb-4">
        <Button
          icon={<ArrowLeftOutlined />}
          onClick={() => router.push("/brands")}
        >
          返回
        </Button>
        <Title level={4} className="!mb-0">
          {brand?.name || "品牌详情"}
        </Title>
      </Space>

      <Card className="mb-4" loading={brandLoading}>
        <div className="flex items-start justify-between">
          <Descriptions title="基础信息" column={2} className="flex-1">
            <Descriptions.Item label="品牌名称">
              {brand?.name}
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              {brand && (
                <Tag
                  color={
                    brand.status === "active"
                      ? STATUS_COLORS.success
                      : STATUS_COLORS.neutral
                  }
                >
                  {brand.status === "active" ? "启用" : "停用"}
                </Tag>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="描述">
              {brand?.description || "-"}
            </Descriptions.Item>
            <Descriptions.Item label="创建时间">
              {formatDate(brand?.created_at)}
            </Descriptions.Item>
          </Descriptions>
          <Button
            type="primary"
            icon={<EditOutlined />}
            disabled={writesDisabled}
            onClick={() => setEditModalOpen(true)}
          >
            编辑基础信息
          </Button>
        </div>
      </Card>

      <Space className="mb-4" size="large">
        <Card>
          <Statistic title="产品数量" value={brand?.stats.product_count || 0} />
        </Card>
        <Card>
          <Statistic
            title="营销活动"
            value={brand?.stats.campaign_count || 0}
          />
        </Card>
        <Card>
          <Statistic
            title="溯源码批次"
            value={brand?.stats.code_batch_count || 0}
          />
        </Card>
        <Card>
          <Statistic title="生产批次" value={brand?.stats.batch_count || 0} />
        </Card>
      </Space>

      <Tabs
        activeKey={activeTab}
        onChange={(k) => setActiveTab(k as TabKey)}
        items={[
          {
            key: "products",
            label: `产品列表 (${brand?.stats.product_count || 0})`,
            children: renderTable(productColumns, products, "products"),
          },
          {
            key: "campaigns",
            label: `营销活动 (${brand?.stats.campaign_count || 0})`,
            children: renderTable(campaignColumns, campaigns, "campaigns"),
          },
          {
            key: "code-batches",
            label: `溯源码批次 (${brand?.stats.code_batch_count || 0})`,
            children: renderTable(
              codeBatchColumns,
              codeBatches,
              "code-batches"
            ),
          },
          {
            key: "batches",
            label: `生产批次 (${brand?.stats.batch_count || 0})`,
            children: renderTable(batchColumns, batches, "batches"),
          },
        ]}
      />

      {brand && (
        <BrandFormModal
          open={editModalOpen}
          mode="edit"
          readOnly={writesDisabled}
          initialValues={brand}
          onSuccess={() => {
            setEditModalOpen(false);
            fetchBrand();
          }}
          onCancel={() => setEditModalOpen(false)}
        />
      )}
    </div>
  );
}
