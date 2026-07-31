"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  App,
  Button,
  Card,
  Descriptions,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { ArrowLeftOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { formatDate } from "@/lib/format";
import { STATUS_COLORS } from "@/lib/status-colors";

const { Title, Text } = Typography;

interface SKUDetail {
  id: string;
  product_id: string;
  product_name?: string;
  code: string;
  name: string;
  specifications?: Record<string, string>;
  package_type?: string;
  barcode?: string;
  image_url?: string;
  status: string;
  created_at?: string;
}

interface ProductionBatchItem {
  id: string;
  batch_code: string;
  product_name?: string;
  sku_name?: string;
  production_date: string;
  expiry_date: string;
  origin?: string;
  status: string;
}

interface CodeBatchItem {
  id: string;
  batch_code: string;
  product_name?: string;
  sku_name?: string;
  quantity: number;
  status: string;
  code_type: string;
  generation_mode: string;
  created_at?: string;
}

type TabItem = ProductionBatchItem | CodeBatchItem;

type TabKey = "profile" | "batches" | "code-batches";

interface TabState<T> {
  items: T[];
  total: number;
  page: number;
  loading: boolean;
}

function createTabState<T>(): TabState<T> {
  return { items: [], total: 0, page: 1, loading: false };
}

const SKU_STATUS_MAP: Record<string, { label: string; color: string }> = {
  active: { label: "启用", color: STATUS_COLORS.success },
  inactive: { label: "停用", color: STATUS_COLORS.neutral },
};

const BATCH_STATUS_MAP: Record<string, { label: string; color: string }> = {
  active: { label: "有效", color: STATUS_COLORS.success },
  recalled: { label: "已召回", color: STATUS_COLORS.error },
  expired: { label: "已过期", color: STATUS_COLORS.neutral },
};

const CODE_STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: "待生成", color: STATUS_COLORS.neutral },
  generating: { label: "生成中", color: STATUS_COLORS.processing },
  completed: { label: "已生成", color: STATUS_COLORS.success },
  activated: { label: "已激活", color: STATUS_COLORS.processing },
  failed: { label: "失败", color: STATUS_COLORS.error },
};

const CODE_TYPE_LABELS: Record<string, string> = {
  single: "普通二维码",
  paired: "内外双码",
  outer: "外包装码",
  inner: "内包装码",
};

const GEN_MODE_LABELS: Record<string, string> = {
  item_level: "一物一码",
  batch_level: "一批一码",
};

export default function SKUDetailPage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const skuId = params.id;
  const { message } = App.useApp();

  const [sku, setSku] = useState<SKUDetail | null>(null);
  const [skuLoading, setSkuLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<TabKey>("profile");

  const [batches, setBatches] =
    useState<TabState<ProductionBatchItem>>(createTabState);
  const [codeBatches, setCodeBatches] =
    useState<TabState<CodeBatchItem>>(createTabState);

  const fetchSKU = async () => {
    try {
      setSkuLoading(true);
      const { data } = await api.get<SKUDetail>(`/skus/${skuId}`);
      setSku(data);
    } catch {
      message.error("获取 SKU 详情失败");
    } finally {
      setSkuLoading(false);
    }
  };

  const fetchTabData = async (tab: TabKey, page = 1) => {
    if (tab === "profile") return;

    const endpointMap: Record<Exclude<TabKey, "profile">, string> = {
      batches: "/production-batches",
      "code-batches": "/code-batches",
    };

    const setters: Record<
      Exclude<TabKey, "profile">,
      (s: TabState<TabItem>) => void
    > = {
      batches: (s) =>
        setBatches({ ...s, items: s.items as ProductionBatchItem[] }),
      "code-batches": (s) =>
        setCodeBatches({ ...s, items: s.items as CodeBatchItem[] }),
    };

    const prev = {
      batches,
      "code-batches": codeBatches,
    }[tab];

    setters[tab]({ ...prev, loading: true });

    try {
      const { data } = await api.get(endpointMap[tab], {
        params: { sku_id: skuId, page, page_size: 20 },
      });
      setters[tab]({
        items: data.items || [],
        total: data.total || 0,
        page,
        loading: false,
      });
    } catch {
      message.error("获取数据失败");
      setters[tab]({ ...prev, loading: false });
    }
  };

  useEffect(() => {
    if (skuId) {
      fetchSKU();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skuId]);

  useEffect(() => {
    if (!skuId) return;
    const stateMap: Record<TabKey, TabState<unknown>> = {
      profile: { items: [], total: 0, page: 1, loading: false },
      batches,
      "code-batches": codeBatches,
    };
    if (activeTab !== "profile" && stateMap[activeTab].items.length === 0) {
      fetchTabData(activeTab, 1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, skuId]);

  const batchColumns: ColumnsType<ProductionBatchItem> = [
    {
      title: "批次号",
      dataIndex: "batch_code",
      key: "batch_code",
    },
    {
      title: "产品",
      dataIndex: "product_name",
      key: "product_name",
      render: (v?: string) => v || "-",
    },
    { title: "生产日期", dataIndex: "production_date", key: "production_date" },
    { title: "保质期至", dataIndex: "expiry_date", key: "expiry_date" },
    {
      title: "产地",
      dataIndex: "origin",
      key: "origin",
      render: (v?: string) => v || "-",
    },
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
    { title: "数量", dataIndex: "quantity", key: "quantity" },
    {
      title: "码类型",
      dataIndex: "code_type",
      key: "code_type",
      render: (v: string) => CODE_TYPE_LABELS[v] || v,
    },
    {
      title: "生成模式",
      dataIndex: "generation_mode",
      key: "generation_mode",
      render: (v: string) => GEN_MODE_LABELS[v] || v,
    },
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

  const renderTable = <T extends object>(
    columns: ColumnsType<T>,
    state: TabState<T>,
    onPageChange: (page: number) => void
  ) => (
    <Table
      columns={columns}
      dataSource={state.items}
      rowKey="id"
      loading={state.loading}
      pagination={{
        current: state.page,
        total: state.total,
        pageSize: 20,
        onChange: onPageChange,
        showTotal: (t) => `共 ${t} 条`,
      }}
    />
  );

  if (!sku && skuLoading) {
    return <div className="py-20 text-center text-text-muted">加载中...</div>;
  }

  if (!sku) {
    return <div className="py-20 text-center text-text-muted">SKU 不存在</div>;
  }

  const specText =
    sku.specifications && Object.keys(sku.specifications).length
      ? Object.entries(sku.specifications)
          .map(([k, v]) => `${k}: ${v}`)
          .join(", ")
      : "未填写";

  return (
    <div>
      <div className="mb-5 flex items-start justify-between gap-4">
        <Space direction="vertical" size={4}>
          <Button
            type="link"
            className="!px-0"
            icon={<ArrowLeftOutlined />}
            onClick={() => router.push("/skus")}
          >
            返回 SKU 列表
          </Button>
          <Title level={4} className="!mb-0">
            {sku.name}
          </Title>
          <Text type="secondary">
            {sku.product_name || "未关联产品"} · {sku.code} ·{" "}
            {SKU_STATUS_MAP[sku.status]?.label || sku.status}
          </Text>
        </Space>
      </div>

      <div className="mb-5 grid grid-cols-1 gap-4 md:grid-cols-4">
        <Card>
          <Statistic title="编码" value={sku.code} />
        </Card>
        <Card>
          <Statistic title="包装类型" value={sku.package_type || "未填写"} />
        </Card>
        <Card>
          <Statistic title="条码/GTIN" value={sku.barcode || "未填写"} />
        </Card>
        <Card>
          <Statistic title="规格" value={specText} />
        </Card>
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={(k) => setActiveTab(k as TabKey)}
        items={[
          {
            key: "profile",
            label: "基础资料",
            children: (
              <Descriptions bordered size="small" column={2}>
                <Descriptions.Item label="SKU 编码">
                  {sku.code}
                </Descriptions.Item>
                <Descriptions.Item label="SKU 名称">
                  {sku.name}
                </Descriptions.Item>
                <Descriptions.Item label="所属产品">
                  <Button
                    type="link"
                    className="!px-0"
                    onClick={() => router.push(`/products/${sku.product_id}`)}
                  >
                    {sku.product_name || sku.product_id}
                  </Button>
                </Descriptions.Item>
                <Descriptions.Item label="包装类型">
                  {sku.package_type || "未填写"}
                </Descriptions.Item>
                <Descriptions.Item label="条码/GTIN">
                  {sku.barcode || "未填写"}
                </Descriptions.Item>
                <Descriptions.Item label="规格">{specText}</Descriptions.Item>
                <Descriptions.Item label="状态">
                  <Tag
                    color={
                      SKU_STATUS_MAP[sku.status]?.color || STATUS_COLORS.neutral
                    }
                  >
                    {SKU_STATUS_MAP[sku.status]?.label || sku.status}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="创建时间">
                  {formatDate(sku.created_at)}
                </Descriptions.Item>
              </Descriptions>
            ),
          },
          {
            key: "batches",
            label: `生产批次 (${batches.total || 0})`,
            children: renderTable(batchColumns, batches, (p) =>
              fetchTabData("batches", p)
            ),
          },
          {
            key: "code-batches",
            label: `码批次 (${codeBatches.total || 0})`,
            children: renderTable(codeBatchColumns, codeBatches, (p) =>
              fetchTabData("code-batches", p)
            ),
          },
        ]}
      />
    </div>
  );
}
