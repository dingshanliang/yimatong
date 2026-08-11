"use client";

import { useCallback, useEffect, useState } from "react";
import { App, Card, Col, Row, Statistic, Tabs } from "antd";
import api from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { resolveCampaignAccess } from "@/lib/campaign-access";
import { ConnectorsTab } from "./_components/ConnectorsTab";
import { CouponPoolsTab } from "./_components/CouponPoolsTab";
import { DeliveriesTab } from "./_components/DeliveriesTab";
import type { Connector } from "./_components/types";

export default function ConnectorsPage() {
  const { message } = App.useApp();
  const user = useAuthStore((state) => state.user);
  const access = resolveCampaignAccess(user);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [connectorsTotal, setConnectorsTotal] = useState(0);
  const [connectorsPage, setConnectorsPage] = useState(1);
  const connectorsPageSize = 20;
  const [loading, setLoading] = useState(false);
  const [connectorTypes, setConnectorTypes] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState("connectors");

  const fetchConnectors = useCallback(async () => {
    if (!access.canView) return;
    setLoading(true);
    try {
      const { data } = await api.get("/connectors/connectors", {
        params: { page: connectorsPage, page_size: connectorsPageSize },
      });
      setConnectors(data.items || []);
      setConnectorsTotal(data.total || 0);
    } catch {
      message.error("加载连接器失败");
    } finally {
      setLoading(false);
    }
  }, [access.canView, connectorsPage, message]);

  const fetchTypes = useCallback(async () => {
    if (!access.canView) return;
    try {
      const { data } = await api.get("/connectors/connectors/types");
      setConnectorTypes(data.types || []);
    } catch {
      setConnectorTypes(["generic_http", "coupon_pool"]);
    }
  }, [access.canView]);

  useEffect(() => {
    fetchConnectors();
    fetchTypes();
  }, [fetchConnectors, fetchTypes]);

  const enabledCount = connectors.filter((c) => c.enabled).length;

  if (!access.canView) {
    return <Card>当前账号无权查看连接器与券码池</Card>;
  }

  const tabItems = [
    {
      key: "connectors",
      label: "连接器管理",
      children: (
        <ConnectorsTab
          connectors={connectors}
          loading={loading}
          connectorTypes={connectorTypes}
          page={connectorsPage}
          pageSize={connectorsPageSize}
          total={connectorsTotal}
          onPageChange={setConnectorsPage}
          onRefresh={fetchConnectors}
        />
      ),
    },
    { key: "coupon-pools", label: "券码池", children: <CouponPoolsTab /> },
    { key: "deliveries", label: "发放记录", children: <DeliveriesTab /> },
  ];

  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <Card>
            <Statistic title="连接器总数" value={connectorsTotal} />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic
              title="本页已启用"
              value={enabledCount}
              valueStyle={{ color: "var(--ymt-color-feedback-success)" }}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic
              title="连接器状态"
              value={enabledCount}
              suffix={`/ ${connectors.length}`}
            />
          </Card>
        </Col>
      </Row>
      <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabItems} />
    </div>
  );
}
