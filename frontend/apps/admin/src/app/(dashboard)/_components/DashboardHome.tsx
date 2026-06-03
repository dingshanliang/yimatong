"use client";

import { useCallback, useEffect, useState } from "react";
import { App, Button, Col, Row, Statistic, Tooltip } from "antd";
import {
  ReloadOutlined,
  ScanOutlined,
  RiseOutlined,
  TeamOutlined,
  GiftOutlined,
} from "@ant-design/icons";
import api, { extractErrorMessage } from "@/lib/api";

import StatusAlerts from "./dashboard/StatusAlerts";
import ConversionFunnel from "./dashboard/ConversionFunnel";
import DashboardCharts from "./dashboard/DashboardCharts";
import CampaignRanking from "./dashboard/CampaignRanking";
import ChannelHealth from "./dashboard/ChannelHealth";
import QuickActions from "./dashboard/QuickActions";
import RecentEvents from "./dashboard/RecentEvents";

interface DashboardData {
  today_scans: number;
  today_uv: number;
  cumulative_scans: number;
  cumulative_first_scans: number;
  period_claim_count: number;
  period_claim_rate: number;
  trend?: { date: string; total_scans: number; uv?: number }[];
  environment_breakdown?: Record<string, number>;
  comparison?: {
    weekly_scans_change: { value: number; direction: string } | null;
    weekly_first_scans_change: { value: number; direction: string } | null;
  };
}

function ChangeIndicator({ change }: { change: { value: number; direction: string } | null | undefined }) {
  if (!change) return null;
  const isUp = change.direction === "up";
  return (
    <span style={{ fontSize: 12, color: isUp ? "#3f8600" : "#cf1322" }}>
      {isUp ? "↑" : "↓"} 较上周 {Math.abs(change.value)}%
    </span>
  );
}

export default function DashboardHome() {
  const { message } = App.useApp();
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchDashboard = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get("/analytics/dashboard", { params: { days_back: 30 } });
      setData(res.data);
    } catch (err) {
      message.error(extractErrorMessage(err, "加载工作台数据失败"));
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void fetchDashboard();
  }, [fetchDashboard]);

  return (
    <div>
      {/* 标题栏 */}
      <div className="mb-4 flex items-center justify-between">
        <h4 className="mb-0" style={{ fontSize: 20, fontWeight: 600 }}>经营看板</h4>
        <Tooltip title="刷新数据">
          <Button
            icon={<ReloadOutlined />}
            onClick={() => void fetchDashboard()}
            loading={loading}
          />
        </Tooltip>
      </div>

      {/* Area 1: 状态提醒 */}
      <StatusAlerts />

      {/* Area 2: 转化漏斗 */}
      <div className="mb-6">
        <ConversionFunnel />
      </div>

      {/* 统计卡片 */}
      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} sm={12} lg={6}>
          <div style={{ borderLeft: "3px solid #1677ff", padding: "12px 16px", background: "var(--ant-color-bg-container)", borderRadius: 6 }}>
            <Statistic title="今日扫码" value={data?.today_scans ?? 0} prefix={<ScanOutlined />} loading={loading} />
          </div>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <div style={{ padding: "12px 16px", background: "var(--ant-color-bg-container)", borderRadius: 6 }}>
            <Statistic title="今日 UV" value={data?.today_uv ?? 0} prefix={<TeamOutlined />} loading={loading} />
          </div>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <div style={{ padding: "12px 16px", background: "var(--ant-color-bg-container)", borderRadius: 6 }}>
            <Statistic title="累计扫码" value={data?.cumulative_scans ?? 0} prefix={<RiseOutlined />} loading={loading} />
            <ChangeIndicator change={data?.comparison?.weekly_scans_change} />
          </div>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <div style={{ padding: "12px 16px", background: "var(--ant-color-bg-container)", borderRadius: 6 }}>
            <Statistic
              title="期间领券"
              value={data?.period_claim_count ?? 0}
              suffix={data?.period_claim_rate ? `(${data.period_claim_rate}%)` : ""}
              prefix={<GiftOutlined />}
              loading={loading}
            />
          </div>
        </Col>
      </Row>

      {/* Area 3: 趋势图 */}
      <div className="mb-6">
        <DashboardCharts initialTrend={data?.trend || []} loading={loading} />
      </div>

      {/* Area 4 & 5: 排行面板 */}
      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} lg={12}>
          <CampaignRanking />
        </Col>
        <Col xs={24} lg={12}>
          <ChannelHealth />
        </Col>
      </Row>

      {/* Area 6: 快速操作 + 最近动态 */}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={8}>
          <QuickActions />
        </Col>
        <Col xs={24} lg={16}>
          <RecentEvents />
        </Col>
      </Row>
    </div>
  );
}
