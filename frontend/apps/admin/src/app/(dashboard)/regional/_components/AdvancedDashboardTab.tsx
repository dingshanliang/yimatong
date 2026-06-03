"use client";

import { useEffect, useState } from "react";
import { App, Card, InputNumber, Select, Space, Statistic, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

interface Props {
  orgId: string;
  orgName: string;
}

export function AdvancedDashboardTab({ orgId, orgName }: Props) {
  const [data, setData] = useState<Record<string, unknown>>({});
  const [daysBack, setDaysBack] = useState(30);
  const [loading, setLoading] = useState(false);
  const [drilldown, setDrilldown] = useState<"member" | "product" | "daily">("member");
  const { message } = App.useApp();

  const fetch = async () => {
    if (!orgId) return;
    setLoading(true);
    try {
      const { data: d } = await api.get(`/regional/orgs/${orgId}/advanced-dashboard`, {
        params: { days_back: daysBack },
      });
      setData(d || {});
    } catch {
      message.error("加载高级看板失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, [orgId, daysBack]);

  const byMember = (data.by_member || []) as Record<string, unknown>[];
  const dailyTrend = (data.daily_trend || []) as Record<string, unknown>[];

  const memberCols: ColumnsType<Record<string, unknown>> = [
    { title: "成员企业", dataIndex: "member_name", key: "member_name" },
    { title: "扫码量", dataIndex: "scan_count", key: "scan_count", sorter: (a, b) => Number(a.scan_count) - Number(b.scan_count) },
    { title: "领取量", dataIndex: "claim_count", key: "claim_count" },
    { title: "上期扫码", dataIndex: "prev_scan_count", key: "prev_scan_count" },
    {
      title: "环比变化",
      dataIndex: "scan_change_pct",
      key: "scan_change_pct",
      render: (v: number | null) => {
        if (v === null || v === undefined) return <Tag>—</Tag>;
        const color = v > 0 ? "green" : v < 0 ? "red" : "default";
        return <Tag color={color}>{v > 0 ? "+" : ""}{v}%</Tag>;
      },
    },
  ];

  const dailyCols: ColumnsType<Record<string, unknown>> = [
    { title: "日期", dataIndex: "date", key: "date" },
    { title: "扫码量", dataIndex: "scan_count", key: "scan_count" },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Space>
          <span className="text-sm text-text-muted">近</span>
          <InputNumber min={7} max={365} value={daysBack} onChange={(v) => setDaysBack(v || 30)} size="small" style={{ width: 70 }} />
          <span className="text-sm text-text-muted">天</span>
          <Select value={drilldown} onChange={setDrilldown} size="small" style={{ width: 120 }}
            options={[
              { value: "member", label: "按成员企业" },
              { value: "daily", label: "按日趋势" },
            ]}
          />
        </Space>
        <span className="text-sm text-text-muted">{orgName} · 高级看板</span>
      </div>

      <div className="mb-4 grid grid-cols-3 gap-4">
        <Card size="small"><Statistic title="成员企业" value={Number(data.member_count ?? 0)} loading={loading} /></Card>
        <Card size="small"><Statistic title="总扫码量" value={Number(data.total_scans ?? 0)} loading={loading} /></Card>
        <Card size="small"><Statistic title="总领取量" value={Number(data.total_claims ?? 0)} loading={loading} /></Card>
      </div>

      {drilldown === "member" && (
        <Table
          columns={memberCols}
          dataSource={byMember}
          rowKey="tenant_id"
          loading={loading}
          size="small"
          pagination={{ pageSize: 20 }}
          title={() => "成员企业扫码对比（含环比）"}
        />
      )}

      {drilldown === "daily" && (
        <Table
          columns={dailyCols}
          dataSource={dailyTrend}
          rowKey="date"
          loading={loading}
          size="small"
          pagination={false}
          title={() => "近 14 天扫码趋势"}
        />
      )}
    </div>
  );
}
