"use client";

import { useEffect, useState } from "react";
import { App, Card, Col, Row, Space, Statistic, Table, Typography, Button, Tag } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title } = Typography;

/* ---------- Repeat Scans ---------- */

function RepeatScansCard() {
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const fetch = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/risk-dashboard/repeat-scans", { params: { min_count: 5, page: 1, page_size: 10 } });
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, []);

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "码 ID", dataIndex: "public_id", key: "public_id" },
    { title: "扫码次数", dataIndex: "scan_count", key: "scan_count" },
    { title: "最近扫码 IP", dataIndex: "last_ip", key: "last_ip", render: (v: string) => v || "—" },
  ];

  return (
    <Card title="重复扫码热点" size="small">
      <Statistic title="异常码数量" value={total} loading={loading} className="mb-4" />
      <Table columns={columns} dataSource={items} rowKey="public_id" loading={loading} size="small" pagination={false} />
    </Card>
  );
}

/* ---------- Cross Region ---------- */

function CrossRegionCard() {
  const [data, setData] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);

  const fetch = async () => {
    setLoading(true);
    try {
      const { data: d } = await api.get("/risk-dashboard/cross-region");
      setData(Array.isArray(d) ? d : d?.items || []);
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, []);

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "码 ID", dataIndex: "public_id", key: "public_id" },
    { title: "预期城市", dataIndex: "expected_city", key: "expected_city", render: (v: string) => v || "—" },
    { title: "实际城市", dataIndex: "detected_city", key: "detected_city" },
    { title: "扫码次数", dataIndex: "scan_count", key: "scan_count" },
  ];

  return (
    <Card title="跨区扫码统计" size="small">
      <Table columns={columns} dataSource={data} rowKey="public_id" loading={loading} size="small" pagination={false} />
    </Card>
  );
}

/* ---------- Diversion Summary ---------- */

function DiversionCard() {
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const fetch = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/risk-dashboard/diversion-summary", { params: { page: 1, page_size: 10 } });
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, []);

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "码 ID", dataIndex: "public_id", key: "public_id" },
    { title: "预期区域", dataIndex: "expected_region", key: "expected_region" },
    { title: "实际城市", dataIndex: "detected_city", key: "detected_city" },
    {
      title: "状态",
      dataIndex: "resolved",
      key: "resolved",
      render: (v: boolean) => <Tag color={v ? "green" : "red"}>{v ? "已处理" : "待处理"}</Tag>,
    },
  ];

  return (
    <Card title="窜货线索汇总" size="small">
      <Statistic title="线索总数" value={total} loading={loading} className="mb-4" />
      <Table columns={columns} dataSource={items} rowKey="id" loading={loading} size="small" pagination={false} />
    </Card>
  );
}

/* ---------- Export ---------- */

/* ---------- Main ---------- */

export default function RiskDashboardPage() {
  const { message } = App.useApp();

  const handleExport = async (dataType: string) => {
    try {
      const exportType = dataType === "alerts" ? "risk_dashboard" : "regional_dashboard";
      const response = await api.post("/analytics/exports", null, {
        params: { export_type: exportType, format: "xlsx" },
        responseType: "blob",
      });
      const blob = new Blob([response.data], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `risk_${dataType}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      message.success("导出成功");
    } catch {
      message.error("导出失败，请确认您有管理员权限");
    }
  };

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">风控看板</Title>
        <Space>
          <Button icon={<DownloadOutlined />} onClick={() => handleExport("alerts")}>导出预警 Excel</Button>
          <Button icon={<DownloadOutlined />} onClick={() => handleExport("diversions")}>导出窜货 Excel</Button>
        </Space>
      </div>
      <Row gutter={[16, 16]}>
        <Col span={12}><RepeatScansCard /></Col>
        <Col span={12}><CrossRegionCard /></Col>
      </Row>
      <div className="mt-4">
        <DiversionCard />
      </div>
    </div>
  );
}

