"use client";

import { useCallback, useEffect, useState } from "react";
import { App, Card, Col, Row, Statistic, Tabs, Typography } from "antd";
import api from "@/lib/api";
import { ConnectorsTab } from "./_components/ConnectorsTab";
import { CouponPoolsTab } from "./_components/CouponPoolsTab";
import { DeliveriesTab } from "./_components/DeliveriesTab";
import type { Connector } from "./_components/types";

const { Title } = Typography;

export default function ConnectorsPage() {
  const { message } = App.useApp();
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [loading, setLoading] = useState(false);
  const [connectorTypes, setConnectorTypes] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState("connectors");

  const fetchConnectors = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/connectors/connectors");
      setConnectors(data);
    } catch { message.error("加载连接器失败"); }
    finally { setLoading(false); }
  }, []);

  const fetchTypes = useCallback(async () => {
    try {
      const { data } = await api.get("/connectors/connectors/types");
      setConnectorTypes(data.types || []);
    } catch { setConnectorTypes(["generic_http", "coupon_pool"]); }
  }, []);

  useEffect(() => { fetchConnectors(); fetchTypes(); }, [fetchConnectors, fetchTypes]);

  const enabledCount = connectors.filter((c) => c.enabled).length;

  const tabItems = [
    { key: "connectors", label: "连接器管理", children: <ConnectorsTab connectors={connectors} loading={loading} connectorTypes={connectorTypes} onRefresh={fetchConnectors} /> },
    { key: "coupon-pools", label: "券码池", children: <CouponPoolsTab /> },
    { key: "deliveries", label: "发放记录", children: <DeliveriesTab /> },
  ];

  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}><Card><Statistic title="连接器总数" value={connectors.length} /></Card></Col>
        <Col span={8}><Card><Statistic title="已启用" value={enabledCount} valueStyle={{ color: "#3f8600" }} /></Card></Col>
        <Col span={8}><Card><Statistic title="连接器状态" value={enabledCount} suffix={`/ ${connectors.length}`} /></Card></Col>
      </Row>
      <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabItems} />
    </div>
  );
}
