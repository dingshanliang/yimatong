"use client";

import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Col,
  Row,
  Space,
  Statistic,
  Typography,
} from "antd";
import {
  DownloadOutlined,
  BellOutlined,
  SwapRightOutlined,
  HeartOutlined,
} from "@ant-design/icons";
import api from "@/lib/api";
import {
  AlertIndicator,
  CrossRegionCard,
  DiversionCard,
  RepeatScansCard,
  ChannelHealthCard,
  ConversionCard,
  useRiskExport,
  useAuthStore,
} from "../risk-dashboard/_components/RiskDashboardComponents";
import { ChannelHealthScoreTitle } from "../_components/HealthScoreHeader";

// 骨架规范说明：本页是多卡片风控仪表盘（指标卡 + 多张含 Table 的业务卡），
// 非 CRUD 列表页；components.md 的「列表页四段式骨架」不适用。
// 各卡片用 antd loading/Statistic/Table 内置态，空/加载态由 antd 默认渲染。

const { Title } = Typography;

/* ---------- Summary Indicators ---------- */

function SummaryIndicators() {
  const { message } = App.useApp();
  const [alertCount, setAlertCount] = useState<number>(0);
  const [diversionUnresolved, setDiversionUnresolved] = useState<number>(0);
  const [avgHealthScore, setAvgHealthScore] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchSummary = useCallback(async () => {
    setLoading(true);
    try {
      const [alertRes, diversionRes, healthRes] = await Promise.allSettled([
        api.get("/risk-dashboard/alerts", {
          params: { page: 1, page_size: 1, resolved: false },
        }),
        api.get("/risk-dashboard/diversion-summary", {
          params: { page: 1, page_size: 1 },
        }),
        api.get("/channel-analytics/health-scores", {
          params: { dimension: "distributor" },
        }),
      ]);

      if (alertRes.status === "fulfilled") {
        setAlertCount(alertRes.value.data?.total ?? 0);
      }
      if (diversionRes.status === "fulfilled") {
        setDiversionUnresolved(diversionRes.value.data?.unresolved_count ?? 0);
      }
      if (healthRes.status === "fulfilled") {
        const scores = healthRes.value.data?.scores || [];
        if (scores.length > 0) {
          const avg = Math.round(
            scores.reduce(
              (sum: number, s: Record<string, unknown>) =>
                sum + Number(s.health_score ?? 0),
              0
            ) / scores.length
          );
          setAvgHealthScore(avg);
        }
      }
    } catch {
      message.error("加载风控摘要数据失败");
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void fetchSummary();
  }, [fetchSummary]);

  return (
    <Row gutter={[16, 16]} className="mb-4">
      <Col xs={24} sm={8}>
        <Card loading={loading}>
          <Statistic
            title="待处理告警"
            value={alertCount}
            prefix={<BellOutlined />}
            valueStyle={
              alertCount > 0
                ? { color: "var(--ymt-color-feedback-danger)" }
                : undefined
            }
          />
        </Card>
      </Col>
      <Col xs={24} sm={8}>
        <Card loading={loading}>
          <Statistic
            title="未处理窜货线索"
            value={diversionUnresolved}
            prefix={<SwapRightOutlined />}
            valueStyle={
              diversionUnresolved > 0
                ? { color: "var(--ymt-color-feedback-danger)" }
                : undefined
            }
          />
        </Card>
      </Col>
      <Col xs={24} sm={8}>
        <Card loading={loading}>
          <Statistic
            title={<ChannelHealthScoreTitle />}
            value={avgHealthScore ?? "—"}
            prefix={<HeartOutlined />}
            valueStyle={
              avgHealthScore !== null
                ? {
                    color:
                      avgHealthScore >= 80
                        ? "var(--ymt-color-feedback-success)"
                        : avgHealthScore >= 60
                          ? "var(--ymt-color-feedback-warning)"
                          : "var(--ymt-color-feedback-danger)",
                  }
                : undefined
            }
            suffix={avgHealthScore !== null ? "/ 100" : undefined}
          />
        </Card>
      </Col>
    </Row>
  );
}

/* ---------- Main ---------- */

export default function RiskCenterPage() {
  const { handleExport, exportReasonDialog } = useRiskExport();
  const tenantId = useAuthStore((s) => s.user?.tenant_id ?? null);

  return (
    <div>
      {exportReasonDialog}
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">
          风控中心
        </Title>
        <Space>
          <AlertIndicator tenantId={tenantId} />
          <Button
            icon={<DownloadOutlined />}
            onClick={() => handleExport("alerts")}
          >
            导出预警
          </Button>
          <Button
            icon={<DownloadOutlined />}
            onClick={() => handleExport("diversions")}
          >
            导出窜货
          </Button>
        </Space>
      </div>

      <SummaryIndicators />

      <Row gutter={[16, 16]}>
        <Col span={12}>
          <RepeatScansCard />
        </Col>
        <Col span={12}>
          <CrossRegionCard />
        </Col>
      </Row>
      <div className="mt-4">
        <Row gutter={[16, 16]}>
          <Col span={12}>
            <ChannelHealthCard />
          </Col>
          <Col span={12}>
            <ConversionCard />
          </Col>
        </Row>
      </div>
      <div className="mt-4">
        <DiversionCard />
      </div>
    </div>
  );
}
