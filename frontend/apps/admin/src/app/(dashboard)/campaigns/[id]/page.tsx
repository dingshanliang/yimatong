"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  App,
  Button,
  Card,
  Col,
  Descriptions,
  Pagination,
  Progress,
  Row,
  Space,
  Spin,
  Statistic,
  Tag,
  Typography,
} from "antd";
import { ArrowLeftOutlined } from "@ant-design/icons";
import api, { extractErrorMessage } from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { resolveCampaignAccess } from "@/lib/campaign-access";
import { formatDateTime } from "@/lib/format";
import { STATUS_COLORS } from "@/lib/status-colors";
import type { Campaign, Benefit } from "@yimatong/shared";

const { Title, Text } = Typography;

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  draft: { label: "草稿", color: STATUS_COLORS.neutral },
  active: { label: "进行中", color: STATUS_COLORS.processing },
  paused: { label: "已暂停", color: STATUS_COLORS.warning },
  ended: { label: "已结束", color: STATUS_COLORS.neutral },
};

const CAMPAIGN_TYPE_MAP: Record<string, string> = {
  coupon: "优惠券",
  lottery: "抽奖",
  points: "积分",
};

const BENEFIT_TYPE_MAP: Record<string, string> = {
  coupon: "优惠券",
  points: "积分",
  gift: "实物礼品",
  lottery_chance: "抽奖机会",
};

const BENEFIT_PAGE_SIZE = 20;

export default function CampaignDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { message } = App.useApp();
  const user = useAuthStore((state) => state.user);
  const access = resolveCampaignAccess(user);

  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [benefits, setBenefits] = useState<Benefit[]>([]);
  const [benefitPage, setBenefitPage] = useState(1);
  const [benefitTotal, setBenefitTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!access.canView) return;
    setLoading(true);
    try {
      const [campaignResp, benefitsResp] = await Promise.all([
        api.get(`/campaigns/${params.id}`),
        api
          .get(`/campaigns/${params.id}/benefits`, {
            params: { page: benefitPage, page_size: BENEFIT_PAGE_SIZE },
          })
          .catch(() => ({ data: { items: [], total: 0 } })),
      ]);
      setCampaign(campaignResp.data);
      setBenefits(benefitsResp.data.items || []);
      setBenefitTotal(benefitsResp.data.total || 0);
    } catch (err) {
      setCampaign(null);
      message.error(extractErrorMessage(err, "加载活动详情失败"));
    } finally {
      setLoading(false);
    }
  }, [params.id, message, benefitPage, access.canView]);

  useEffect(() => {
    if (access.canView) void load();
  }, [access.canView, load]);

  if (!access.canView) {
    return (
      <div className="py-10 text-center">
        <Title level={4} type="secondary">
          当前账号无权查看活动
        </Title>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex justify-center py-20">
        <Spin size="large" />
      </div>
    );
  }

  if (!campaign) {
    return (
      <div className="py-10 text-center">
        <Title level={4} type="secondary">
          活动不存在或已被删除
        </Title>
        <Button type="primary" onClick={() => router.push("/campaigns")}>
          返回活动列表
        </Button>
      </div>
    );
  }

  const statusInfo = STATUS_MAP[campaign.status] || {
    label: campaign.status,
    color: STATUS_COLORS.neutral,
  };
  const stockPercent =
    campaign.stock_total > 0
      ? Math.round((campaign.stock_used / campaign.stock_total) * 100)
      : 0;

  return (
    <div>
      <div className="mb-4 flex items-center gap-3">
        <Button
          icon={<ArrowLeftOutlined />}
          onClick={() => router.push("/campaigns")}
        >
          返回
        </Button>
        <Title level={4} className="!mb-0">
          {campaign.name}
        </Title>
        <Tag color={statusInfo.color}>{statusInfo.label}</Tag>
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={16}>
          <Card title="基本信息" size="small">
            <Descriptions column={{ xs: 1, sm: 2 }} bordered size="small">
              <Descriptions.Item label="活动名称">
                {campaign.name}
              </Descriptions.Item>
              <Descriptions.Item label="类型">
                {CAMPAIGN_TYPE_MAP[campaign.campaign_type] ||
                  campaign.campaign_type}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={statusInfo.color}>{statusInfo.label}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="关联产品">
                {campaign.product_name || "未关联"}
              </Descriptions.Item>
              <Descriptions.Item label="开始时间">
                {formatDateTime(campaign.start_at)}
              </Descriptions.Item>
              <Descriptions.Item label="结束时间">
                {formatDateTime(campaign.end_at)}
              </Descriptions.Item>
              <Descriptions.Item label="活动说明" span={2}>
                {campaign.description || "无"}
              </Descriptions.Item>
            </Descriptions>
          </Card>

          {benefits.length > 0 && (
            <Card
              title={`权益列表（${benefitTotal}）`}
              size="small"
              style={{ marginTop: 16 }}
            >
              <Descriptions column={1} bordered size="small">
                {benefits.map((b) => {
                  const usagePercent =
                    b.stock_total > 0
                      ? Math.round((b.stock_used / b.stock_total) * 100)
                      : 0;
                  return (
                    <Descriptions.Item
                      key={b.id}
                      label={
                        <Space>
                          <Text strong>{b.name}</Text>
                          <Tag>
                            {BENEFIT_TYPE_MAP[b.benefit_type] || b.benefit_type}
                          </Tag>
                        </Space>
                      }
                    >
                      <Space
                        direction="vertical"
                        size={0}
                        style={{ width: "100%" }}
                      >
                        <Text type="secondary">
                          库存 {b.stock_used}/{b.stock_total}（{usagePercent}%）
                        </Text>
                        <Progress percent={usagePercent} size="small" />
                        <Text type="secondary">
                          每人限领 {b.per_person_limit} 次
                        </Text>
                      </Space>
                    </Descriptions.Item>
                  );
                })}
              </Descriptions>
              {benefitTotal > BENEFIT_PAGE_SIZE && (
                <div className="mt-4 flex justify-end">
                  <Pagination
                    current={benefitPage}
                    pageSize={BENEFIT_PAGE_SIZE}
                    total={benefitTotal}
                    showSizeChanger={false}
                    onChange={setBenefitPage}
                  />
                </div>
              )}
            </Card>
          )}
        </Col>

        <Col xs={24} lg={8}>
          <Card title="活动数据" size="small">
            <Row gutter={[8, 16]}>
              <Col span={12}>
                <Statistic title="权益数" value={campaign.benefit_count} />
              </Col>
              <Col span={12}>
                <Statistic title="已领取" value={campaign.claim_count} />
              </Col>
              <Col span={12}>
                <Statistic title="企微添加" value={campaign.wecom_add_count} />
              </Col>
              <Col span={12}>
                <Statistic
                  title="库存消耗"
                  value={`${campaign.stock_used}/${campaign.stock_total}`}
                />
              </Col>
            </Row>
            {campaign.stock_total > 0 && (
              <div className="mt-3">
                <Text type="secondary">库存消耗进度</Text>
                <Progress percent={stockPercent} size="small" />
              </div>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
