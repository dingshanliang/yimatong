"use client";

import { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { Layout, Menu, Avatar, Dropdown, Spin } from "antd";
import {
  DashboardOutlined,
  AppstoreOutlined,
  QrcodeOutlined,
  BarChartOutlined,
  FileTextOutlined,
  GiftOutlined,
  LogoutOutlined,
  UserOutlined,
  TeamOutlined,
  TagOutlined,
  ProfileOutlined,
  DatabaseOutlined,
  LineChartOutlined,
  ExportOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  UserAddOutlined,
  ShopOutlined,
  CheckSquareOutlined,
  RobotOutlined,
} from "@ant-design/icons";
import type { MenuProps } from "antd";
import { useAuthStore } from "@/lib/auth";

const { Header, Sider, Content } = Layout;

const menuItems: MenuProps["items"] = [
  { key: "/", icon: <DashboardOutlined />, label: "工作台" },
  { key: "/brands", icon: <TagOutlined />, label: "品牌管理" },
  { key: "/products", icon: <AppstoreOutlined />, label: "产品管理" },
  { key: "/skus", icon: <ProfileOutlined />, label: "SKU 管理" },
  { key: "/batches", icon: <DatabaseOutlined />, label: "生产批次" },
  { key: "/codes", icon: <QrcodeOutlined />, label: "码管理" },
  { key: "/pages", icon: <FileTextOutlined />, label: "页面管理" },
  { key: "/ai-assistant", icon: <RobotOutlined />, label: "AI 助手" },
  { key: "/campaigns", icon: <GiftOutlined />, label: "活动管理" },
  { key: "/benefits", icon: <SafetyCertificateOutlined />, label: "权益管理" },
  {
    key: "analytics-group",
    icon: <BarChartOutlined />,
    label: "数据统计",
    children: [
      { key: "/stats", icon: <BarChartOutlined />, label: "扫码统计" },
      { key: "/campaign-analytics", icon: <LineChartOutlined />, label: "活动看板" },
      { key: "/risk-dashboard", icon: <SafetyCertificateOutlined />, label: "风控看板" },
      { key: "/exports", icon: <ExportOutlined />, label: "导出管理" },
    ],
  },
  { key: "/channels", icon: <ShopOutlined />, label: "渠道管理" },
  { key: "/risk", icon: <SafetyCertificateOutlined />, label: "风控中心" },
  { key: "/members", icon: <UserOutlined />, label: "会员积分" },
  { key: "/gmv", icon: <LineChartOutlined />, label: "GMV 归因" },
  { key: "/regional", icon: <TeamOutlined />, label: "区域品牌" },
  { key: "/accounts", icon: <TeamOutlined />, label: "组织账户" },
  {
    key: "settings-group",
    icon: <SettingOutlined />,
    label: "系统设置",
    children: [
      { key: "/settings/roles", icon: <UserAddOutlined />, label: "角色权限" },
      { key: "/settings/compliance", icon: <SafetyCertificateOutlined />, label: "合规设置" },
      { key: "/settings/tenant", icon: <ShopOutlined />, label: "租户设置" },
      { key: "/settings/audit-logs", icon: <FileTextOutlined />, label: "操作日志" },
    ],
  },
  { key: "/agency", icon: <TeamOutlined />, label: "代运营" },
  { key: "/launch-checklist", icon: <CheckSquareOutlined />, label: "上线检查" },
];

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const { user, hydrate, logout } = useAuthStore();

  // 同步初始化：lazy initializer 在首次渲染时同步读取 localStorage，
  // 避免 useEffect 异步竞争导致的误判重定向
  const [ready] = useState(() => {
    if (typeof window === "undefined") return false;
    return !!localStorage.getItem("access_token");
  });

  useEffect(() => {
    if (!ready) {
      router.replace("/login");
      return;
    }
    // 确保 store 已 hydrate（幂等操作）
    hydrate();
  }, [ready, hydrate, router]);

  // 从 pathname 提取选中的菜单 key
  const selectedKeys = [pathname];

  // 展开包含当前路径的子菜单
  const openKeys: string[] = [];
  if (pathname.startsWith("/stats") || pathname.startsWith("/campaign-analytics") || pathname.startsWith("/exports") || pathname.startsWith("/risk-dashboard")) {
    openKeys.push("analytics-group");
  }
  if (pathname.startsWith("/settings")) {
    openKeys.push("settings-group");
  }

  if (!ready || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spin size="large" />
      </div>
    );
  }

  const userMenuItems: MenuProps["items"] = [
    {
      key: "logout",
      icon: <LogoutOutlined />,
      label: "退出登录",
      onClick: () => {
        logout();
        router.replace("/login");
      },
    },
  ];

  return (
    <Layout className="min-h-screen">
      <Sider breakpoint="lg" collapsedWidth={0} width={220}>
        <div className="my-4 flex h-10 items-center justify-center">
          <span className="text-lg font-bold text-white">一码通</span>
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={selectedKeys}
          defaultOpenKeys={openKeys}
          items={menuItems}
          onClick={({ key }) => {
            if (key.startsWith("/")) router.push(key);
          }}
        />
      </Sider>
      <Layout>
        <Header className="flex items-center justify-end bg-white px-6 shadow-sm">
          <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
            <div className="flex cursor-pointer items-center gap-2">
              <Avatar icon={<UserOutlined />} size="small" />
              <span>{user.name || user.email}</span>
            </div>
          </Dropdown>
        </Header>
        <Content className="m-6 rounded-lg bg-white p-6 shadow-sm">
          {children}
        </Content>
      </Layout>
    </Layout>
  );
}
