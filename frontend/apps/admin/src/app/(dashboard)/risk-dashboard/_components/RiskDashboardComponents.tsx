"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  App,
  Badge,
  Button,
  Card,
  Col,
  Empty,
  List,
  Popover,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  InputNumber,
  Typography,
} from "antd";
import { BellOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import {
  newExportIdempotencyKey,
  useExportReasonDialog,
} from "@/components/ExportReasonDialog";
import { useAuthStore } from "@/lib/auth";
import { channelAccessForPrincipal } from "@/lib/channel-access";
import { STATUS_COLORS } from "@/lib/status-colors";
import { ChannelHealthScoreHeader } from "../../_components/HealthScoreHeader";
import { ChannelConversionRateHeader } from "../../_components/MetricHeaders";

/* ---------- SSE Alert Indicator ---------- */

type AlertListItem = Record<string, unknown> & { key: string };

export function AlertIndicator({ tenantId }: { tenantId: string | null }) {
  const [alerts, setAlerts] = useState<Record<string, unknown>[]>([]);
  const [recentAlerts, setRecentAlerts] = useState<AlertListItem[]>([]);
  const [listLoading, setListLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [connected, setConnected] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const connectRef = useRef<() => Promise<void>>(() => Promise.resolve());

  const scheduleRetry = useCallback(() => {
    const delay = Math.min(1000 * Math.pow(2, retryCountRef.current), 30000);
    retryCountRef.current += 1;
    retryTimerRef.current = setTimeout(() => {
      void connectRef.current();
    }, delay);
  }, []);

  // 弹层打开时拉取最近告警（GET /risk-alerts），与 SSE 实时推送互补
  const fetchRecentAlerts = useCallback(async () => {
    setListLoading(true);
    try {
      const { data } = await api.get("/risk-alerts", {
        params: { page: 1, page_size: 10 },
      });
      const items = (Array.isArray(data) ? data : data?.items || []) as Record<
        string,
        unknown
      >[];
      setRecentAlerts(
        items.map((item, i) => ({
          ...item,
          key: String(item.id ?? `db-${i}`),
        }))
      );
    } catch {
      setRecentAlerts([]);
    } finally {
      setListLoading(false);
    }
  }, []);

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (next) void fetchRecentAlerts();
  };

  useEffect(() => {
    const doConnect = async () => {
      if (!tenantId) return;

      try {
        const { data } = await api.post("/risk-dashboard/alerts/ticket");
        const ticket = data.ticket;
        if (!ticket) return;

        eventSourceRef.current?.close();

        const base = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
        const es = new EventSource(
          `${base}/api/v1/risk-dashboard/alerts/stream?ticket=${ticket}`
        );
        eventSourceRef.current = es;

        es.onopen = () => {
          setConnected(true);
          retryCountRef.current = 0;
        };
        es.onerror = () => {
          setConnected(false);
          es.close();
          eventSourceRef.current = null;
          scheduleRetry();
        };
        es.onmessage = (e) => {
          try {
            const msg = JSON.parse(e.data);
            setAlerts((prev) => [msg, ...prev].slice(0, 20));
          } catch {
            /* ignore parse errors */
          }
        };
      } catch {
        setConnected(false);
        scheduleRetry();
      }
    };

    connectRef.current = doConnect;
    void doConnect();

    return () => {
      eventSourceRef.current?.close();
      eventSourceRef.current = null;
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
    };
  }, [tenantId, scheduleRetry]);

  // SSE 实时消息排在最前（无 resolved 字段，视为待处理），其后是库里最近告警；
  // 按 public_id + detail 去重，避免同一条告警（实时 + 落库）重复展示
  const combined: AlertListItem[] = [
    ...alerts.map((item, i) => ({ ...item, key: `live-${i}` })),
    ...recentAlerts.filter(
      (item) =>
        !alerts.some(
          (live) =>
            live.public_id === item.public_id && live.detail === item.detail
        )
    ),
  ].slice(0, 20);

  const popoverContent = (
    <div style={{ width: 320 }}>
      {listLoading ? (
        <div className="flex justify-center py-4">
          <Spin size="small" />
        </div>
      ) : combined.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="暂无风控告警"
        />
      ) : (
        <List
          size="small"
          dataSource={combined}
          renderItem={(item) => (
            <List.Item>
              <div className="w-full">
                <div className="flex items-center justify-between gap-2 mb-1">
                  <Tag color={STATUS_COLORS.warning}>
                    {String(item.alert_type ?? "告警")}
                  </Tag>
                  {typeof item.resolved === "boolean" && (
                    <Tag
                      color={
                        item.resolved
                          ? STATUS_COLORS.success
                          : STATUS_COLORS.error
                      }
                    >
                      {item.resolved ? "已处理" : "待处理"}
                    </Tag>
                  )}
                </div>
                <div className="text-xs text-text-muted break-all">
                  {String(item.public_id ?? "")}
                  {item.detail ? ` · ${String(item.detail)}` : ""}
                </div>
              </div>
            </List.Item>
          )}
        />
      )}
    </div>
  );

  return (
    <>
      {!connected && retryCountRef.current > 0 && (
        <Alert
          type="warning"
          message="实时推送已断开，正在重连..."
          showIcon
          banner
          className="mb-4"
        />
      )}
      <Popover
        content={popoverContent}
        title="最近风控告警"
        trigger="click"
        placement="bottomRight"
        open={open}
        onOpenChange={handleOpenChange}
      >
        <Badge count={alerts.length} size="small" offset={[2, 0]}>
          <Button
            icon={<BellOutlined />}
            type={connected ? "default" : "dashed"}
            size="small"
          >
            {connected ? "实时告警" : "未连接"}
          </Button>
        </Badge>
      </Popover>
    </>
  );
}

/* ---------- Repeat Scans ---------- */

export function RepeatScansCard() {
  const { message } = App.useApp();
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/risk-dashboard/repeat-scans", {
        params: { min_count: 5, page: 1, page_size: 10 },
      });
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch {
      message.error("加载重复扫码数据失败");
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "码 ID", dataIndex: "public_id", key: "public_id" },
    { title: "扫码次数", dataIndex: "scan_count", key: "scan_count" },
    {
      title: "不同 IP",
      dataIndex: "distinct_ips",
      key: "distinct_ips",
      render: (v: number) => v ?? "—",
    },
  ];

  return (
    <Card title="重复扫码热点" size="small">
      <Statistic
        title="异常码数量"
        value={total}
        loading={loading}
        className="mb-4"
      />
      <Table
        columns={columns}
        dataSource={items}
        rowKey="public_id"
        loading={loading}
        size="small"
        pagination={false}
      />
    </Card>
  );
}

/* ---------- Cross Region ---------- */

export function CrossRegionCard() {
  const { message } = App.useApp();
  const [stats, setStats] = useState<Record<string, unknown>>({});
  const [daysBack, setDaysBack] = useState(30);
  const [loading, setLoading] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/risk-dashboard/cross-region", {
        params: { days_back: daysBack },
      });
      setStats(data || {});
    } catch {
      message.error("加载跨区扫码数据失败");
    } finally {
      setLoading(false);
    }
  }, [daysBack, message]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  const byRegion = (stats.by_region || []) as Record<string, unknown>[];
  const byCity = (stats.by_detected_city || []) as Record<string, unknown>[];
  const byCode = (stats.by_code || []) as Record<string, unknown>[];

  const regionCols: ColumnsType<Record<string, unknown>> = [
    { title: "预期区域", dataIndex: "region", key: "region" },
    { title: "跨区次数", dataIndex: "count", key: "count" },
  ];

  const cityCols: ColumnsType<Record<string, unknown>> = [
    { title: "实际扫码城市", dataIndex: "city", key: "city" },
    { title: "跨区次数", dataIndex: "count", key: "count" },
  ];

  return (
    <Card
      title="跨区扫码统计"
      size="small"
      extra={
        <Space>
          <span className="text-text-muted text-sm">近</span>
          <InputNumber
            min={1}
            max={365}
            value={daysBack}
            onChange={(v) => setDaysBack(v || 30)}
            size="small"
            style={{ width: 70 }}
          />
          <span className="text-text-muted text-sm">天</span>
        </Space>
      }
    >
      <Row gutter={16} className="mb-4">
        <Col span={8}>
          <Statistic
            title="跨区线索总数"
            value={Number(stats.total_clues ?? 0)}
            loading={loading}
          />
        </Col>
        <Col span={8}>
          <Statistic
            title="待处理"
            value={Number(stats.unresolved_count ?? 0)}
            loading={loading}
            valueStyle={{
              color:
                Number(stats.unresolved_count ?? 0) > 0
                  ? "var(--ymt-color-feedback-danger)"
                  : undefined,
            }}
          />
        </Col>
      </Row>
      <Row gutter={16}>
        <Col span={12}>
          <Table
            columns={regionCols}
            dataSource={byRegion.slice(0, 5)}
            rowKey="region"
            loading={loading}
            size="small"
            pagination={false}
            title={() => "按预期区域"}
          />
        </Col>
        <Col span={12}>
          <Table
            columns={cityCols}
            dataSource={byCity.slice(0, 5)}
            rowKey="city"
            loading={loading}
            size="small"
            pagination={false}
            title={() => "按实际城市"}
          />
        </Col>
      </Row>
      {byCode.length > 0 && (
        <div className="mt-4">
          <Table
            columns={[
              { title: "码 ID", dataIndex: "public_id", key: "public_id" },
              { title: "跨区次数", dataIndex: "count", key: "count" },
            ]}
            dataSource={byCode}
            rowKey="public_id"
            loading={loading}
            size="small"
            pagination={false}
            title={() => "跨区频次 Top 10"}
          />
        </div>
      )}
    </Card>
  );
}

/* ---------- Channel Health Scores ---------- */

export function ChannelHealthCard() {
  const { message } = App.useApp();
  const [scores, setScores] = useState<Record<string, unknown>[]>([]);
  const [dimension, setDimension] = useState<string>("distributor");
  const [loading, setLoading] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/channel-analytics/health-scores", {
        params: { dimension },
      });
      setScores(data.scores || []);
    } catch {
      message.error("加载渠道健康评分失败");
    } finally {
      setLoading(false);
    }
  }, [dimension, message]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "渠道名称", dataIndex: "name", key: "name" },
    { title: "扫码量", dataIndex: "scan_count", key: "scan_count" },
    { title: "UV", dataIndex: "scan_uv", key: "scan_uv" },
    {
      title: <ChannelHealthScoreHeader />,
      dataIndex: "health_score",
      key: "health_score",
      render: (v: number) => {
        const color =
          v >= 80
            ? STATUS_COLORS.success
            : v >= 60
              ? STATUS_COLORS.warning
              : STATUS_COLORS.error;
        return <Tag color={color}>{v}</Tag>;
      },
    },
    {
      title: "重复率%",
      dataIndex: "repeat_rate",
      key: "repeat_rate",
      render: (v: number) => `${v}%`,
    },
    {
      title: "跨区率%",
      dataIndex: "cross_region_rate",
      key: "cross_region_rate",
      render: (v: number) => `${v}%`,
    },
    {
      title: "异常率%",
      dataIndex: "anomaly_rate",
      key: "anomaly_rate",
      render: (v: number) => `${v}%`,
    },
  ];

  return (
    <Card
      title="渠道健康评分"
      size="small"
      extra={
        <Select
          value={dimension}
          onChange={setDimension}
          size="small"
          style={{ width: 100 }}
          options={[
            { label: "经销商", value: "distributor" },
            { label: "区域", value: "region" },
            { label: "门店", value: "store" },
          ]}
        />
      }
    >
      <Table
        columns={columns}
        dataSource={scores}
        rowKey="name"
        loading={loading}
        size="small"
        pagination={{ pageSize: 10 }}
      />
    </Card>
  );
}

/* ---------- Conversion Comparison ---------- */

export function ConversionCard() {
  const { message } = App.useApp();
  const [data, setData] = useState<Record<string, unknown>[]>([]);
  const [dimension, setDimension] = useState<string>("distributor");
  const [loading, setLoading] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const { data: d } = await api.get(
        "/channel-analytics/conversion-comparison",
        { params: { dimension } }
      );
      setData(d.comparison || []);
    } catch {
      message.error("加载渠道转化率数据失败");
    } finally {
      setLoading(false);
    }
  }, [dimension, message]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "渠道名称", dataIndex: "name", key: "name" },
    { title: "扫码 UV", dataIndex: "scan_uv", key: "scan_uv" },
    {
      title: "确认权益",
      dataIndex: "confirmed_claims",
      key: "confirmed_claims",
      render: () => <Tag>待归因</Tag>,
    },
    {
      title: <ChannelConversionRateHeader />,
      dataIndex: "conversion_rate",
      key: "conversion_rate",
      render: () => <Tag>不可用</Tag>,
    },
    {
      title: "vs 平均",
      dataIndex: "vs_average",
      key: "vs_average",
      render: () => <Tag>不可用</Tag>,
    },
  ];

  return (
    <Card
      title="渠道流量与转化归因"
      size="small"
      extra={
        <Select
          value={dimension}
          onChange={setDimension}
          size="small"
          style={{ width: 100 }}
          options={[
            { label: "经销商", value: "distributor" },
            { label: "区域", value: "region" },
            { label: "门店", value: "store" },
          ]}
        />
      }
    >
      <Table
        columns={columns}
        dataSource={data}
        rowKey="name"
        loading={loading}
        size="small"
        pagination={{ pageSize: 10 }}
      />
    </Card>
  );
}

/* ---------- Diversion Summary ---------- */

export function DiversionCard() {
  const { message } = App.useApp();
  const user = useAuthStore((state) => state.user);
  const access = channelAccessForPrincipal(user);
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/risk-dashboard/diversion-summary", {
        params: { page: 1, page_size: 20 },
      });
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch {
      message.error("加载窜货线索数据失败");
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    if (access.canRead) void fetchData();
  }, [access.canRead, fetchData]);

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "码 ID", dataIndex: "public_id", key: "public_id" },
    { title: "预期区域", dataIndex: "expected_region", key: "expected_region" },
    { title: "实际城市", dataIndex: "detected_city", key: "detected_city" },
    {
      title: "状态",
      dataIndex: "resolved",
      key: "resolved",
      render: (v: boolean) => (
        <Tag color={v ? STATUS_COLORS.success : STATUS_COLORS.error}>
          {v ? "已处理" : "待处理"}
        </Tag>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 80,
      render: () => (
        <Typography.Text type="secondary">请到渠道管理调查</Typography.Text>
      ),
    },
  ];

  if (!access.canRead) return null;

  return (
    <Card title="窜货线索汇总" size="small">
      <Statistic
        title="线索总数"
        value={total}
        loading={loading}
        className="mb-4"
      />
      <Table
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={loading}
        size="small"
        pagination={false}
      />
    </Card>
  );
}

/* ---------- Export Helper ---------- */

export function useRiskExport() {
  const { message } = App.useApp();
  const { requestReason, exportReasonDialog } = useExportReasonDialog();

  const handleExport = async (dataType: string) => {
    const reason = await requestReason();
    if (!reason) return;
    try {
      const response = await api.post(
        "/risk-dashboard/export",
        {
          data_type: dataType,
          reason,
        },
        {
          headers: { "Idempotency-Key": newExportIdempotencyKey() },
          responseType: "blob",
        }
      );
      const blob = new Blob([response.data], {
        type: "text/csv;charset=utf-8",
      });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `risk_${dataType}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      message.success("导出成功");
    } catch {
      message.error("导出失败，请确认您有管理员权限");
    }
  };

  return { handleExport, exportReasonDialog };
}

export { useAuthStore };
