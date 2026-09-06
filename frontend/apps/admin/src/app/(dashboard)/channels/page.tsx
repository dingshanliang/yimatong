"use client";

import {
  Alert,
  Button,
  Card,
  Col,
  Row,
  Statistic,
  Tabs,
  Typography,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { useAuthStore } from "@/lib/auth";
import {
  channelAccessForPrincipal,
  type ChannelAccess,
} from "@/lib/channel-access";
import { useChannelsWorkspace } from "./_components/useChannelsWorkspace";
import { DistributorsTab } from "./_components/DistributorsTab";
import { RegionsTab } from "./_components/RegionsTab";
import { StoresTab } from "./_components/StoresTab";
import { ScopesTab } from "./_components/ScopesTab";
import { AssignTab } from "./_components/AssignTab";
import { DiversionTab } from "./_components/DiversionTab";
import { ChannelsModals } from "./_components/ChannelsModals";
import { tenantFeatureEnabled } from "@/lib/plan-entitlement";

export { PROVINCE_CITY_OPTIONS } from "./_components/shared";

const { Title, Text } = Typography;

export default function ChannelsPage() {
  const user = useAuthStore((state) => state.user);
  const access = channelAccessForPrincipal(user);
  if (!access.canRead) {
    return <Alert type="warning" message="当前账号无渠道管理权限" />;
  }
  return <ChannelsWorkspace access={access} />;
}

function ChannelsWorkspace({ access }: { access: ChannelAccess }) {
  const w = useChannelsWorkspace(access);
  const { overview, loadData, activeTab, setActiveTab, tenantFeatures } = w;

  return (
    <div>
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <Title level={4} className="!mb-1">
            渠道管理
          </Title>
          <Text type="secondary">
            维护渠道组织，登记已赋码货品流向，并跟进跨区扫码线索。
          </Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={loadData}>
          刷新
        </Button>
      </div>

      <Row gutter={[16, 16]} className="mb-5">
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic
              title="经销商"
              value={overview?.distributor_count || 0}
            />
          </Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic title="区域" value={overview?.region_count || 0} />
          </Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic title="门店" value={overview?.store_count || 0} />
          </Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic
              title="已登记码量"
              value={overview?.allocated_quantity || 0}
            />
          </Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic
              title="待处理线索"
              value={overview?.pending_diversion_count || 0}
            />
          </Card>
        </Col>
      </Row>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        onTabClick={setActiveTab}
        items={[
          {
            key: "distributors",
            label: "经销商",
            children: <DistributorsTab w={w} access={access} />,
          },
          {
            key: "regions",
            label: "区域",
            children: <RegionsTab w={w} access={access} />,
          },
          {
            key: "stores",
            label: "门店",
            children: <StoresTab w={w} access={access} />,
          },
          {
            key: "scopes",
            label: "账号授权",
            children: <ScopesTab w={w} access={access} />,
          },
          {
            key: "assign",
            label: "流向登记",
            children: <AssignTab w={w} access={access} />,
          },
          {
            key: "diversion",
            label: "窜货线索",
            children: <DiversionTab w={w} access={access} />,
          },
        ].filter((item) => {
          if (item.key === "scopes" && !access.canScope) return false;
          if (item.key === "stores") {
            return tenantFeatureEnabled(tenantFeatures, "channel_portal");
          }
          return true;
        })}
      />
      <ChannelsModals w={w} access={access} />
    </div>
  );
}
