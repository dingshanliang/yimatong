"use client";

import { useEffect, useState } from "react";
import api from "@/lib/api";
import {
  newExportIdempotencyKey,
  useExportReasonDialog,
} from "@/components/ExportReasonDialog";
import { ClaimConversionRateHeader } from "../../_components/MetricHeaders";
import {
  Button,
  Card,
  Col,
  InputNumber,
  Row,
  Space,
  Statistic,
  Table,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";

const { Text } = Typography;

interface DashboardData {
  member_count: number;
  product_count: number;
  total_scans: number;
  total_claims: number;
  days_back: number;
  by_member: Array<{
    tenant_id: string;
    member_name: string;
    scan_count: number;
    claim_count: number;
  }>;
  by_product: Array<{
    product_id: string;
    product_name: string;
    scan_count: number;
  }>;
}

const memberColumns: ColumnsType<DashboardData["by_member"][0]> = [
  { title: "企业名称", dataIndex: "member_name", key: "member_name" },
  {
    title: "扫码量",
    dataIndex: "scan_count",
    key: "scan_count",
    sorter: (a, b) => a.scan_count - b.scan_count,
  },
  {
    title: "领取量",
    dataIndex: "claim_count",
    key: "claim_count",
    sorter: (a, b) => a.claim_count - b.claim_count,
  },
  {
    title: <ClaimConversionRateHeader />,
    key: "conversion",
    render: (_: unknown, r: DashboardData["by_member"][0]) => {
      const rate = r.scan_count
        ? ((r.claim_count / r.scan_count) * 100).toFixed(1)
        : "0";
      const num = parseFloat(rate);
      return (
        <Text type={num >= 5 ? "success" : num >= 1 ? "warning" : "danger"}>
          {rate}%
        </Text>
      );
    },
  },
];

const productColumns: ColumnsType<DashboardData["by_product"][0]> = [
  { title: "产品名称", dataIndex: "product_name", key: "product_name" },
  {
    title: "扫码量",
    dataIndex: "scan_count",
    key: "scan_count",
    sorter: (a, b) => a.scan_count - b.scan_count,
  },
];

export function DashboardTab({
  orgId,
  orgName,
}: {
  orgId: string;
  orgName: string;
}) {
  const { requestReason, exportReasonDialog } = useExportReasonDialog();
  const [data, setData] = useState<DashboardData>({
    member_count: 0,
    product_count: 0,
    total_scans: 0,
    total_claims: 0,
    days_back: 30,
    by_member: [],
    by_product: [],
  });
  const [loading, setLoading] = useState(false);
  const [daysBack, setDaysBack] = useState(30);

  const fetch = async () => {
    if (!orgId) return;
    setLoading(true);
    try {
      const { data: d } = await api.get(
        `/regional/orgs/${orgId}/dashboard?days_back=${daysBack}`
      );
      setData(d || {});
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetch();
  }, [orgId, daysBack]);

  const handleExport = async () => {
    const reason = await requestReason();
    if (!reason) return;
    try {
      const response = await api.post(
        "/analytics/exports",
        {
          export_type: "regional_dashboard",
          format: "xlsx",
          org_id: orgId,
          days_back: daysBack,
          reason,
        },
        {
          headers: { "Idempotency-Key": newExportIdempotencyKey() },
          responseType: "blob",
        }
      );
      const blob = new Blob([response.data], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${orgName || "regional"}-dashboard.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch {
      /* silent */
    }
  };

  return (
    <div>
      {exportReasonDialog}
      <div className="mb-4 flex items-center justify-between">
        <Space>
          <span className="text-sm text-text-muted">统计天数</span>
          <InputNumber
            min={7}
            max={365}
            value={daysBack}
            onChange={(v) => v && setDaysBack(v)}
          />
        </Space>
        <Button onClick={handleExport}>导出 Excel</Button>
      </div>

      <Row gutter={[16, 16]} className="mb-6">
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="成员企业"
              value={data.member_count}
              loading={loading}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="授权产品"
              value={data.product_count}
              loading={loading}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="总扫码量"
              value={data.total_scans}
              loading={loading}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="总领取量"
              value={data.total_claims}
              loading={loading}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={16}>
        <Col span={14}>
          <Card title="按成员企业统计" size="small">
            <Table
              columns={memberColumns}
              dataSource={data.by_member}
              rowKey="tenant_id"
              loading={loading}
              size="small"
              pagination={false}
            />
          </Card>
        </Col>
        <Col span={10}>
          <Card title="按产品统计" size="small">
            <Table
              columns={productColumns}
              dataSource={data.by_product}
              rowKey="product_id"
              loading={loading}
              size="small"
              pagination={false}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
