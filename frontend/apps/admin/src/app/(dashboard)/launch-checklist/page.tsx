"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Empty,
  Input,
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
import { useAuthStore } from "@/lib/auth";
import { STATUS_COLORS } from "@/lib/status-colors";

const { Title, Text } = Typography;

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
  readiness_sample_code?: {
    public_id: string;
    status: string;
    ready: boolean;
  } | null;
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
  const user = useAuthStore((state) => state.user);
  const isBrand = user?.tenant_type === "brand";
  const isActingAgency =
    user?.tenant_type === "agency" && Boolean(user.acting_tenant_id);
  const scopes = user?.agency_scope || [];
  const canPrepare =
    (isBrand && ["admin", "operator"].includes(user?.role || "")) ||
    (isActingAgency && scopes.includes("pages"));
  const canExecute =
    (isBrand && user?.role === "admin") ||
    (isActingAgency && scopes.includes("release:execute"));
  const canConfirm = isBrand && user?.role === "admin";
  const canSuspend = canConfirm;
  const canView = canPrepare || canExecute;
  const releaseBase = isActingAgency
    ? "/ops/launch-releases"
    : "/launch-releases";
  const [templates, setTemplates] = useState<PageTemplateOption[]>([]);
  const [campaigns, setCampaigns] = useState<CampaignOption[]>([]);
  const [batches, setBatches] = useState<CodeBatchOption[]>([]);
  const [pageVersionId, setPageVersionId] = useState<string>();
  const [campaignId, setCampaignId] = useState<string>();
  const [codeBatchId, setCodeBatchId] = useState<string>();
  const [release, setRelease] = useState<LaunchRelease | null>(null);
  const [releases, setReleases] = useState<LaunchRelease[]>([]);
  const [releasePage, setReleasePage] = useState(1);
  const [releaseTotal, setReleaseTotal] = useState(0);
  const [suspensionReason, setSuspensionReason] = useState("");
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
    if (!canView) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const [
        pagesResponse,
        campaignsResponse,
        batchesResponse,
        releasesResponse,
      ] = await Promise.all([
        canPrepare
          ? api.get("/page-templates", { params: { page_size: 100 } })
          : Promise.resolve({ data: { items: [] } }),
        canPrepare
          ? api.get("/campaigns", {
              params: { status: "active", page_size: 100 },
            })
          : Promise.resolve({ data: { items: [] } }),
        canPrepare
          ? api.get("/code-batches", {
              params: { status: "activated", page_size: 100 },
            })
          : Promise.resolve({ data: { items: [] } }),
        api.get(releaseBase, { params: { page: releasePage, page_size: 20 } }),
      ]);
      const pages = (pagesResponse.data.items || []) as PageTemplateOption[];
      const activeCampaigns = (campaignsResponse.data.items ||
        []) as CampaignOption[];
      const activeBatches = (batchesResponse.data.items ||
        []) as CodeBatchOption[];
      setTemplates(pages);
      setCampaigns(activeCampaigns);
      setBatches(activeBatches);
      setReleases((releasesResponse.data.items || []) as LaunchRelease[]);
      setReleaseTotal(Number(releasesResponse.data.total || 0));
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
  }, [canView, releaseBase, releasePage]);

  const inspectCurrentRelease = async () => {
    if (!pageVersionId || !campaignId || !codeBatchId) {
      message.warning("请先选择扫码页、上线活动和已激活码批次");
      return;
    }
    setActionLoading(true);
    try {
      const { data } = await api.post<LaunchRelease>(releaseBase, {
        page_version_id: pageVersionId,
        campaign_id: campaignId,
        code_batch_id: codeBatchId,
        idempotency_key: crypto.randomUUID(),
      });
      setRelease(data);
      setReleases((current) => [
        data,
        ...current.filter((item) => item.id !== data.id),
      ]);
    } catch (error) {
      message.error(extractErrorMessage(error, "上线检查失败"));
    } finally {
      setActionLoading(false);
    }
  };

  const runReleaseAction = async (
    action:
      | "confirm"
      | "launch"
      | "publish"
      | "request-confirmation"
      | "suspend"
      | "resume"
  ) => {
    if (!release) return;
    setActionLoading(true);
    try {
      const pathAction = action === "publish" ? "publish" : action;
      const { data } = await api.post<LaunchRelease>(
        `${releaseBase}/${release.id}/${pathAction}`,
        {
          idempotency_key: crypto.randomUUID(),
          ...(action === "suspend" ? { reason: suspensionReason.trim() } : {}),
        }
      );
      setRelease(data);
      setReleases((current) =>
        current.map((item) => (item.id === data.id ? data : item))
      );
      if (action === "suspend") setSuspensionReason("");
      message.success("上线状态已更新");
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

  if (!canView) {
    return <Empty description="当前账号没有上线发布权限" />;
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
          disabled={!canPrepare}
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
          {totalCount > 0 && (
            <Progress
              percent={Math.round((passedCount / totalCount) * 100)}
              status={release.ready ? "success" : "active"}
              format={() => `${passedCount}/${totalCount} 项通过`}
            />
          )}
          {release.readiness_sample_code && (
            <Alert
              className="mt-4"
              type={release.readiness_sample_code.ready ? "success" : "warning"}
              showIcon
              message={
                release.readiness_sample_code.ready
                  ? "上线样本码已准备"
                  : "上线样本码待准备"
              }
              description={
                <Space direction="vertical" size={2}>
                  <Text code>{release.readiness_sample_code.public_id}</Text>
                  <Text type="secondary">
                    系统从当前码批次选定样本码核对配置；正式上线前不会签发权益凭证。
                  </Text>
                </Space>
              }
            />
          )}
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
              {release.status === "pending_confirmation" && canConfirm && (
                <Button
                  loading={actionLoading}
                  onClick={() => void runReleaseAction("confirm")}
                >
                  确认版本
                </Button>
              )}
              {release.status === "pending_confirmation" &&
                isActingAgency &&
                scopes.includes("pages") && (
                  <Button
                    loading={actionLoading}
                    onClick={() =>
                      void runReleaseAction("request-confirmation")
                    }
                  >
                    提交品牌方确认
                  </Button>
                )}
              {release.status === "confirmed" && canExecute && (
                <Button
                  type="primary"
                  icon={<RocketOutlined />}
                  loading={actionLoading}
                  onClick={() =>
                    void runReleaseAction(isActingAgency ? "publish" : "launch")
                  }
                >
                  执行上线
                </Button>
              )}
              {release.status === "suspended" && canConfirm && (
                <Button
                  loading={actionLoading}
                  onClick={() => void runReleaseAction("resume")}
                >
                  恢复上线
                </Button>
              )}
            </Space>
          )}
          {release.status === "live" && canSuspend && (
            <Space.Compact className="mt-4 w-full">
              <Input
                value={suspensionReason}
                maxLength={500}
                placeholder="填写暂停原因"
                onChange={(event) => setSuspensionReason(event.target.value)}
              />
              <Button
                danger
                disabled={!suspensionReason.trim()}
                loading={actionLoading}
                onClick={() => void runReleaseAction("suspend")}
              >
                暂停上线
              </Button>
            </Space.Compact>
          )}
        </Card>
      )}

      <Card className="mt-4" title="上线记录">
        <List
          dataSource={releases}
          pagination={{
            current: releasePage,
            pageSize: 20,
            total: releaseTotal,
            onChange: setReleasePage,
          }}
          renderItem={(item) => (
            <List.Item
              actions={[
                <Button key="open" type="link" onClick={() => setRelease(item)}>
                  查看
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={statusLabel(item.status)}
                description={item.content_digest}
              />
            </List.Item>
          )}
        />
      </Card>
    </div>
  );
}
