"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  App,
  Button,
  Card,
  Col,
  Descriptions,
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
import { formatDateTime } from "@/lib/format";
import type { Campaign, Benefit } from "@yimatong/shared";

const { Title, Text } = Typography;

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  draft: { label: "草稿", color: "#8c8c8c" },
  active: { label: "进行中", color: "#1d4ed8" },
  paused: { label: "已暂停", color: "#f59e0b" },
  ended: { label: "已结束", color: "#8c8c8c" },
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

export default function CampaignDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { message } = App.useApp();

  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [benefits, setBenefits] = useState<Benefit[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [campaignResp, benefitsResp] = await Promise.all([
        api.get(`/campaigns/${params.id}`),
        api
          .get(`/campaigns/${params.id}/benefits`, {
            params: { page_size: 100 },
          })
          .catch(() => ({ data: { items: [] } })),
      ]);
      setCampaign(campaignResp.data);
      setBenefits(benefitsResp.data.items || []);
    } catch (err) {
      setCampaign(null);
      message.error(extractErrorMessage(err, "加载活动详情失败"));
    } finally {
      setLoading(false);
    }
  }, [params.id, message]);

  useEffect(() => {
    load();
  }, [load]);

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
    color: "#8c8c8c",
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
              title={`权益列表（${benefits.length}）`}
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
