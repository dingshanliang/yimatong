"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Empty,
  List,
  Progress,
  Row,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import {
  CheckCircleOutlined,
  ReloadOutlined,
  RocketOutlined,
} from "@ant-design/icons";
import api, { extractErrorMessage } from "@/lib/api";
import { STATUS_COLORS } from "@/lib/status-colors";

const { Title, Paragraph, Text } = Typography;

interface PublishedVersion {
  id: string;
  version: number;
}

interface PageTemplateOption {
  id: string;
  name: string;
  product_id?: string | null;
  product_name?: string | null;
  published_version?: PublishedVersion | null;
}

interface CampaignOption {
  id: string;
  name: string;
  product_id?: string | null;
  status: string;
}

interface CodeBatchOption {
  id: string;
  batch_code: string;
  product_id: string;
  status: string;
}

interface ReadinessCheck {
  key: string;
  label: string;
  passed: boolean;
  detail: string;
}

interface LaunchRelease {
  id: string;
  status: string;
  ready: boolean;
  content_digest: string;
  page_version_id: string;
  campaign_id: string;
  code_batch_id: string;
  readiness_snapshot: {
    checks: ReadinessCheck[];
    passed_count: number;
    total_count: number;
    ready: boolean;
  };
}

function statusLabel(status: string) {
  switch (status) {
    case "preparing":
      return "配置中";
    case "pending_confirmation":
      return "待确认";
    case "confirmed":
      return "已确认，待上线";
    case "live":
      return "已正式上线";
    case "invalidated":
      return "需重新确认";
    case "suspended":
      return "已暂停";
    default:
      return status;
  }
}

export default function LaunchChecklistPage() {
  const { message } = App.useApp();
  const [templates, setTemplates] = useState<PageTemplateOption[]>([]);
  const [campaigns, setCampaigns] = useState<CampaignOption[]>([]);
  const [batches, setBatches] = useState<CodeBatchOption[]>([]);
  const [pageVersionId, setPageVersionId] = useState<string>();
  const [campaignId, setCampaignId] = useState<string>();
  const [codeBatchId, setCodeBatchId] = useState<string>();
  const [release, setRelease] = useState<LaunchRelease | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);

  const publishedPages = useMemo(
    () => templates.filter((item) => item.published_version),
    [templates]
  );
  const selectedPage = publishedPages.find(
    (item) => item.published_version?.id === pageVersionId
  );
  const matchingCampaigns = campaigns.filter(
    (item) =>
      !selectedPage?.product_id || item.product_id === selectedPage.product_id
  );
  const matchingBatches = batches.filter(
    (item) =>
      !selectedPage?.product_id || item.product_id === selectedPage.product_id
  );

  const loadOptions = async () => {
    setLoading(true);
    try {
      const [pagesResponse, campaignsResponse, batchesResponse] =
        await Promise.all([
          api.get("/page-templates", { params: { page_size: 100 } }),
          api.get("/campaigns", {
            params: { status: "active", page_size: 100 },
          }),
          api.get("/code-batches", {
            params: { status: "activated", page_size: 100 },
          }),
        ]);
      const pages = (pagesResponse.data.items || []) as PageTemplateOption[];
      const activeCampaigns = (campaignsResponse.data.items ||
        []) as CampaignOption[];
      const activeBatches = (batchesResponse.data.items ||
        []) as CodeBatchOption[];
      setTemplates(pages);
      setCampaigns(activeCampaigns);
      setBatches(activeBatches);
      const firstPage = pages.find((item) => item.published_version);
      const firstPageProduct = firstPage?.product_id;
      const firstCampaign = activeCampaigns.find(
        (item) => !firstPageProduct || item.product_id === firstPageProduct
      );
      const firstBatch = activeBatches.find(
        (item) => !firstPageProduct || item.product_id === firstPageProduct
      );
      setPageVersionId(firstPage?.published_version?.id);
      setCampaignId(firstCampaign?.id);
      setCodeBatchId(firstBatch?.id);
    } catch (error) {
      message.error(extractErrorMessage(error, "上线准备数据加载失败"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadOptions();
    // 页面初始化只加载一次；后续刷新由按钮显式触发。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const inspectCurrentRelease = async () => {
    if (!pageVersionId || !campaignId || !codeBatchId) {
      message.warning("请先选择扫码页、上线活动和已激活码批次");
      return;
    }
    setActionLoading(true);
    try {
      const { data } = await api.post<LaunchRelease>("/launch-releases", {
        page_version_id: pageVersionId,
        campaign_id: campaignId,
        code_batch_id: codeBatchId,
      });
      setRelease(data);
    } catch (error) {
      message.error(extractErrorMessage(error, "上线检查失败"));
    } finally {
      setActionLoading(false);
    }
  };

  const confirmAndLaunch = async () => {
    if (!release) return;
    setActionLoading(true);
    try {
      const { data } = await api.post<LaunchRelease>(
        `/launch-releases/${release.id}/confirm-and-launch`,
        {
          idempotency_key: `launch-${release.id}-${release.content_digest.slice(0, 12)}`,
        }
      );
      setRelease(data);
      message.success("已正式上线");
    } catch (error) {
      message.error(extractErrorMessage(error, "上线失败，请先处理未通过项"));
    } finally {
      setActionLoading(false);
    }
  };

  const checks = release?.readiness_snapshot.checks || [];
  const passedCount = release?.readiness_snapshot.passed_count || 0;
  const totalCount = release?.readiness_snapshot.total_count || 0;

  if (loading) {
    return (
      <div className="flex justify-center p-12">
        <Spin />
      </div>
    );
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <Title level={4} className="!mb-1">
            客户上线门禁
          </Title>
          <Text type="secondary">
            系统会根据真实数据判断能否上线，页面上的勾选不会改变结果。
          </Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={() => void loadOptions()}>
          刷新准备数据
        </Button>
      </div>

      <Card className="mb-4" title="选择本次上线内容">
        <Row gutter={[16, 16]}>
          <Col xs={24} md={8}>
            <Text strong>扫码页</Text>
            <Select
              className="mt-2 w-full"
              value={pageVersionId}
              placeholder="选择已发布扫码页"
              options={publishedPages.map((item) => ({
                value: item.published_version?.id,
                label: `${item.name}${item.product_name ? `（${item.product_name}）` : ""} · v${item.published_version?.version}`,
              }))}
              onChange={(value) => {
                setPageVersionId(value);
                const page = publishedPages.find(
                  (item) => item.published_version?.id === value
                );
                const productId = page?.product_id;
                setCampaignId(
                  campaigns.find(
                    (item) => !productId || item.product_id === productId
                  )?.id
                );
                setCodeBatchId(
                  batches.find(
                    (item) => !productId || item.product_id === productId
                  )?.id
                );
                setRelease(null);
              }}
            />
          </Col>
          <Col xs={24} md={8}>
            <Text strong>上线活动</Text>
            <Select
              className="mt-2 w-full"
              value={campaignId}
              placeholder="选择已启用活动"
              options={matchingCampaigns.map((item) => ({
                value: item.id,
                label: item.name,
              }))}
              onChange={(value) => {
                setCampaignId(value);
                setRelease(null);
              }}
            />
          </Col>
          <Col xs={24} md={8}>
            <Text strong>已激活码批次</Text>
            <Select
              className="mt-2 w-full"
              value={codeBatchId}
              placeholder="选择已激活码批次"
              options={matchingBatches.map((item) => ({
                value: item.id,
                label: item.batch_code,
              }))}
              onChange={(value) => {
                setCodeBatchId(value);
                setRelease(null);
              }}
            />
          </Col>
        </Row>
        <Button
          className="mt-4"
          type="primary"
          icon={<CheckCircleOutlined />}
          loading={actionLoading}
          onClick={() => void inspectCurrentRelease()}
        >
          检查当前上线组合
        </Button>
      </Card>

      {!release ? (
        <Card>
          <Empty description="选择内容后开始检查" />
        </Card>
      ) : (
        <Card
          title="上线准备度"
          extra={
            <Tag
              color={
                release.ready ? STATUS_COLORS.success : STATUS_COLORS.warning
              }
            >
              {statusLabel(release.status)}
            </Tag>
          }
        >
          <Progress
            percent={
              totalCount ? Math.round((passedCount / totalCount) * 100) : 0
            }
            status={release.ready ? "success" : "active"}
            format={() => `${passedCount}/${totalCount} 项通过`}
          />
          <List
            className="mt-4"
            dataSource={checks}
            renderItem={(item) => (
              <List.Item>
                <List.Item.Meta
                  avatar={
                    <CheckCircleOutlined
                      className={item.passed ? "text-success" : "text-muted"}
                    />
                  }
                  title={item.label}
                  description={item.detail}
                />
                <Tag
                  color={
                    item.passed ? STATUS_COLORS.success : STATUS_COLORS.warning
                  }
                >
                  {item.passed ? "已通过" : "待处理"}
                </Tag>
              </List.Item>
            )}
          />
          {!release.ready && (
            <Alert
              className="mt-4"
              type="warning"
              showIcon
              message="还有准备工作未完成，品牌方暂时不能确认上线"
            />
          )}
          {release.ready && release.status !== "live" && (
            <Space className="mt-4">
              <Button
                type="primary"
                icon={<RocketOutlined />}
                loading={actionLoading}
                onClick={() => void confirmAndLaunch()}
              >
                确认并上线
              </Button>
              <Paragraph type="secondary" className="!mb-0">
                点击后会记录本次版本确认，并正式上线。
              </Paragraph>
            </Space>
          )}
        </Card>
      )}
    </div>
  );
}
