"use client";

import { useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { App, Layout, Menu, Button, Dropdown, Typography } from "antd";
import {
  DashboardOutlined,
  TeamOutlined,
  CrownOutlined,
  DashboardFilled,
  HeartOutlined,
  ShopOutlined,
  FileTextOutlined,
  BarChartOutlined,
  SettingOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  SunOutlined,
  MoonOutlined,
  LogoutOutlined,
  KeyOutlined,
} from "@ant-design/icons";
import type { MenuProps } from "antd";
import { usePlatformAuth } from "@/lib/platform-auth";
import { extractErrorMessage } from "@/lib/api";
import { usePlatformTheme } from "@/lib/theme-provider";

const { Header, Sider, Content } = Layout;
const { Text } = Typography;

const menuItems: MenuProps["items"] = [
  { key: "/", icon: <DashboardOutlined />, label: "首页看板" },
  { key: "/tenants", icon: <TeamOutlined />, label: "租户管理" },
  { key: "/invite-codes", icon: <KeyOutlined />, label: "邀请码" },
  { key: "/plans", icon: <CrownOutlined />, label: "套餐管理" },
  { key: "/quota", icon: <DashboardFilled />, label: "额度监控" },
  { key: "/health", icon: <HeartOutlined />, label: "客户健康度" },
  { key: "/providers", icon: <ShopOutlined />, label: "服务商管理" },
  { key: "/audit-logs", icon: <FileTextOutlined />, label: "审计日志" },
  { key: "/analytics", icon: <BarChartOutlined />, label: "数据分析" },
  { key: "/settings", icon: <SettingOutlined />, label: "系统配置" },
];

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const router = useRouter();
  const pathname = usePathname();
  const logout = usePlatformAuth((s) => s.logout);
  const logoutLoading = usePlatformAuth((s) => s.loading);
  const { isDark, toggleMode } = usePlatformTheme();
  const { message } = App.useApp();

  const handleMenuClick: MenuProps["onClick"] = ({ key }) => {
    router.push(key);
  };

  const handleLogout = async () => {
    try {
      await logout();
      router.replace("/login");
    } catch (error) {
      message.error(extractErrorMessage(error, "退出登录失败，请重试"));
    }
  };

  const userMenuItems: MenuProps["items"] = [
    {
      key: "theme",
      icon: isDark ? <SunOutlined /> : <MoonOutlined />,
      label: isDark ? "亮色模式" : "暗色模式",
      onClick: toggleMode,
    },
    { type: "divider" },
    {
      key: "logout",
      icon: <LogoutOutlined />,
      label: "退出登录",
      danger: true,
      disabled: logoutLoading,
      onClick: handleLogout,
    },
  ];

  return (
    <Layout className="platform-shell h-full min-h-screen">
      <Sider
        className="platform-sider"
        trigger={null}
        collapsible
        collapsed={collapsed}
        width={220}
        collapsedWidth={64}
        style={{ borderRight: "1px solid rgba(255,255,255,0.06)" }}
      >
        <div
          style={{
            height: 56,
            display: "flex",
            alignItems: "center",
            justifyContent: collapsed ? "center" : "flex-start",
            padding: collapsed ? 0 : "0 20px",
            borderBottom: "1px solid rgba(255,255,255,0.06)",
          }}
        >
          <Text
            strong
            style={{
              color: "var(--ymt-color-text-inverse)",
              fontSize: collapsed
                ? "var(--ymt-font-size-base)"
                : "var(--ymt-font-size-md)",
              whiteSpace: "nowrap",
              overflow: "hidden",
            }}
          >
            {collapsed ? "YM" : "一码通平台"}
          </Text>
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[pathname]}
          items={menuItems}
          onClick={handleMenuClick}
          style={{ borderRight: 0, marginTop: 4 }}
        />
      </Sider>
      <Layout>
        <Header
          className="platform-header"
          style={{
            padding: "0 24px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            height: 56,
            lineHeight: 56,
          }}
        >
          <Button
            type="text"
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed(!collapsed)}
            style={{ fontSize: 16 }}
          />
          <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
            <Button type="text" style={{ color: "inherit" }}>
              平台管理员
            </Button>
          </Dropdown>
        </Header>
        <Content
          className="platform-content"
          style={{ margin: 16, padding: 24, borderRadius: 8, minHeight: 280 }}
        >
          {children}
        </Content>
      </Layout>
    </Layout>
  );
}
