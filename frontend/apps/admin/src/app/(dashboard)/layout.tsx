"use client";

import { useEffect, useMemo } from "react";
import { useRouter, usePathname } from "next/navigation";
import { Button, ConfigProvider, Layout, Menu, Avatar, Dropdown, Select, Space, Spin, Tooltip } from "antd";
import {
  DashboardOutlined,
  AppstoreOutlined,
  QrcodeOutlined,
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
  GlobalOutlined,
  MoonOutlined,
  SunOutlined,
} from "@ant-design/icons";
import type { MenuProps } from "antd";
import { useAuthStore } from "@/lib/auth";
import { I18nProvider, useI18n } from "@/lib/i18n";
import { useAdminTheme } from "@/lib/theme-provider";
import zhCN from "antd/locale/zh_CN";
import enUS from "antd/locale/en_US";

const { Header, Sider, Content } = Layout;

const LOCALE_MAP: Record<string, Parameters<typeof ConfigProvider>[0]["locale"]> = {
  "zh-CN": zhCN,
  "en-US": enUS,
};

const MENU_OPEN_KEY_RULES = [
  { key: "catalog-group", prefixes: ["/brands", "/products", "/skus", "/batches"] },
  { key: "traceability-group", prefixes: ["/codes", "/pages"] },
  { key: "growth-group", prefixes: ["/campaigns", "/benefits", "/members"] },
  { key: "channels-group", prefixes: ["/channels", "/channel-portal", "/store-portal", "/regional", "/accounts"] },
  { key: "/analytics", prefixes: ["/analytics", "/campaign-analytics", "/gmv"] },
  { key: "/risk-center", prefixes: ["/risk-center", "/risk-dashboard"] },
  { key: "integrations-group", prefixes: ["/connectors", "/integrations", "/imports", "/crm-sync"] },
  { key: "governance-group", prefixes: ["/risk", "/launch-checklist"] },
  { key: "settings-group", prefixes: ["/settings", "/i18n"] },
];

// --- Menu Permission Configuration ---
type TenantType = "brand" | "agency" | "regional_org";

interface MenuPolicy {
  mode: "allowlist" | "blocklist";
  items: string[];
}

const MENU_PERMISSIONS: Record<TenantType, MenuPolicy> = {
  brand: {
    mode: "blocklist",
    items: ["/agency"],
  },
  agency: {
    mode: "allowlist",
    items: [
      "/", "/ai-assistant",
      "/agency",
      "/pages", "/campaigns", "/products", "/codes",
      "/launch-checklist",
      "/analytics", "/exports",
      "settings-group", "/settings/roles", "/settings/compliance", "/settings/tenant", "/settings/audit-logs", "/i18n",
    ],
  },
  regional_org: {
    mode: "blocklist",
    items: [
      "/agency", "/skus", "/batches", "/benefits", "/channels",
      "/accounts", "/risk-center", "/integrations", "/connectors",
      "/risk", "/crm-sync", "/imports",
    ],
  },
};

// Role-based overrides for distributor/store_guide
const ROLE_PORTAL_MAP: Record<string, string> = {
  distributor: "/channel-portal",
  store_guide: "/store-portal",
};

function DashboardInner({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { user, hydrate, logout } = useAuthStore();
  const { locale, setLocale, t } = useI18n();
  const { isDark, toggleMode } = useAdminTheme();

  useEffect(() => {
    hydrate();
    if (!localStorage.getItem("access_token")) {
      router.replace("/login");
    }
  }, [hydrate, router]);

  const selectedKeys = [pathname];

  const openKeys = MENU_OPEN_KEY_RULES
    .filter(({ prefixes }) => prefixes.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`)))
    .map(({ key }) => key);

  const channelMenuChildren: MenuProps["items"] = [
    { key: "/channels", icon: <ShopOutlined />, label: t("menu.channels") },
    { key: "/regional", icon: <TeamOutlined />, label: t("menu.regional") },
    { key: "/accounts", icon: <TeamOutlined />, label: t("menu.accounts") },
  ];

  const menuItems: MenuProps["items"] = [
    { key: "/", icon: <DashboardOutlined />, label: t("menu.dashboard") },
    { key: "/ai-assistant", icon: <RobotOutlined />, label: t("menu.ai-assistant") },
    {
      key: "catalog-group",
      icon: <AppstoreOutlined />,
      label: t("menu.group.catalog"),
      children: [
        { key: "/brands", icon: <TagOutlined />, label: t("menu.brands") },
        { key: "/products", icon: <AppstoreOutlined />, label: t("menu.products") },
        { key: "/skus", icon: <ProfileOutlined />, label: t("menu.skus") },
        { key: "/batches", icon: <DatabaseOutlined />, label: t("menu.batches") },
      ],
    },
    {
      key: "traceability-group",
      icon: <QrcodeOutlined />,
      label: t("menu.group.traceability"),
      children: [
        { key: "/codes", icon: <QrcodeOutlined />, label: t("menu.codes") },
        { key: "/pages", icon: <FileTextOutlined />, label: t("menu.pages") },
      ],
    },
    {
      key: "growth-group",
      icon: <GiftOutlined />,
      label: t("menu.group.growth"),
      children: [
        { key: "/campaigns", icon: <GiftOutlined />, label: t("menu.campaigns") },
        { key: "/benefits", icon: <SafetyCertificateOutlined />, label: t("menu.benefits") },
        { key: "/members", icon: <UserOutlined />, label: t("menu.members") },
      ],
    },
    { key: "/agency", icon: <TeamOutlined />, label: t("menu.agency") },
    {
      key: "channels-group",
      icon: <ShopOutlined />,
      label: t("menu.group.channels"),
      children: channelMenuChildren,
    },
    { key: "/analytics", icon: <LineChartOutlined />, label: t("menu.deep-analytics") },
    { key: "/risk-center", icon: <SafetyCertificateOutlined />, label: t("menu.risk-center") },
    { key: "/exports", icon: <ExportOutlined />, label: t("menu.exports") },
    {
      key: "integrations-group",
      icon: <DatabaseOutlined />,
      label: t("menu.integrations"),
      children: [
        { key: "/integrations", icon: <DatabaseOutlined />, label: t("menu.integrations") },
        { key: "/connectors", icon: <DatabaseOutlined />, label: t("menu.connectors") },
      ],
    },
    {
      key: "governance-group",
      icon: <SafetyCertificateOutlined />,
      label: t("menu.group.governance"),
      children: [
        { key: "/risk", icon: <SafetyCertificateOutlined />, label: t("menu.risk") },
        { key: "/launch-checklist", icon: <CheckSquareOutlined />, label: t("menu.launch-checklist") },
      ],
    },
    {
      key: "settings-group",
      icon: <SettingOutlined />,
      label: t("menu.settings"),
      children: [
        { key: "/settings/roles", icon: <UserAddOutlined />, label: t("menu.roles") },
        { key: "/settings/compliance", icon: <SafetyCertificateOutlined />, label: t("menu.compliance") },
        { key: "/settings/tenant", icon: <ShopOutlined />, label: t("menu.tenant") },
        { key: "/settings/audit-logs", icon: <FileTextOutlined />, label: t("menu.audit-logs") },
        { key: "/i18n", icon: <GlobalOutlined />, label: t("menu.i18n") },
      ],
    },
  ];

  const tenantType = (user?.tenant_type || "brand") as TenantType;
  const policy = MENU_PERMISSIONS[tenantType] || MENU_PERMISSIONS.brand;
  const portalOverride = ROLE_PORTAL_MAP[user?.role?.toLowerCase() || ""];

  const filteredMenuItems = useMemo(() => {
    if (!menuItems) return menuItems;

    // Portal users (distributor/store_guide) see only their portal
    if (portalOverride) {
      return menuItems.filter(
        (item): item is NonNullable<typeof item> =>
          item != null && "key" in item && (item.key === "/" || item.key === portalOverride)
      );
    }

    // Agency in client context: show brand-like menu filtered by agency_scope
    if (tenantType === "agency" && user?.acting_tenant_id) {
      const scope = user.agency_scope || [];
      const scopeAllowlist = new Set([
        "/", // always show dashboard
        ...scope,
        "/analytics",
        "settings-group",
      ]);

      function isScopeAllowed(key: string): boolean {
        return scopeAllowlist.has(key);
      }

      function filterScopeItems(items: MenuProps["items"]): MenuProps["items"] {
        if (!items) return items;
        return items
          .filter((item) => {
            if (!item || !("key" in item)) return true;
            const key = item.key as string;
            // Hide agency menu when in client context
            if (key === "/agency") return false;
            if (!("children" in item)) return isScopeAllowed(key);
            return true; // groups kept, children filtered below
          })
          .map((item) => {
            if (!item || !("children" in item)) return item;
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const children = (item as any).children;
            if (!Array.isArray(children)) return item;
            const filtered = filterScopeItems(children) ?? [];
            // Remove group if empty
            if (filtered.length === 0) return null;
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            return { ...(item as any), children: filtered } as any;
          })
          .filter((item): item is NonNullable<typeof item> => item !== null);
      }

      return filterScopeItems(menuItems);
    }

    function isAllowed(key: string): boolean {
      if (policy.mode === "allowlist") {
        return policy.items.includes(key);
      }
      // blocklist
      return !policy.items.includes(key);
    }

    function filterItems(items: MenuProps["items"]): MenuProps["items"] {
      if (!items) return items;
      return items
        .filter((item) => {
          if (!item || !("key" in item)) return true;
          const key = item.key as string;
          if (!("children" in item)) return isAllowed(key);
          return true; // groups kept, children filtered below
        })
        .map((item) => {
          if (!item || !("children" in item)) return item;
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const children = (item as any).children;
          if (!Array.isArray(children)) return item;
          const filtered = filterItems(children) ?? [];
          // If group itself is not allowed and has no children, remove it
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const groupKey = (item as any).key as string;
          if (filtered.length === 0 || !isAllowed(groupKey)) {
            // Group is blocked or empty - remove if empty
            if (filtered.length === 0) return null;
          }
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          return { ...(item as any), children: filtered } as any;
        })
        .filter((item): item is NonNullable<typeof item> => item !== null);
    }

    return filterItems(menuItems);
  }, [menuItems, policy, portalOverride, tenantType, user?.acting_tenant_id, user?.agency_scope]);

  if (!user) {
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
      label: t("common.logout"),
      onClick: () => {
        logout();
        router.replace("/login");
      },
    },
  ];

  return (
    <ConfigProvider locale={LOCALE_MAP[locale]}>
      <Layout className="admin-shell min-h-screen" style={{ minHeight: "100vh" }}>
        <Sider
          breakpoint="lg"
          collapsedWidth={0}
          width={220}
          className="admin-sider"
          style={{ minHeight: "100vh" }}
        >
          <div className="my-4 flex h-10 items-center justify-center border-b border-white/10 pb-4">
            <span className="text-lg font-bold text-white">{t("common.brand")}</span>
          </div>
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={selectedKeys}
            defaultOpenKeys={openKeys}
            items={filteredMenuItems}
            onClick={({ key }) => {
              if (key.startsWith("/")) router.push(key);
            }}
          />
        </Sider>
        <Layout className="admin-workspace flex flex-col" style={{ minHeight: "100vh" }}>
          <Header className="admin-header flex items-center justify-between px-6">
            {/* Agency context indicator */}
            {user?.tenant_type === "agency" && user?.acting_tenant_id && (
              <div style={{
                background: "#722ed1",
                color: "white",
                padding: "4px 12px",
                borderRadius: 4,
                fontSize: 13,
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}>
                <span>客户: {user.acting_tenant_id}</span>
                <Button
                  size="small"
                  type="text"
                  style={{ color: "white", padding: "0 4px" }}
                  onClick={() => {
                    useAuthStore.getState().exitAgencyContext();
                    router.push("/");
                  }}
                >
                  退出客户
                </Button>
              </div>
            )}
            <Select
              value={locale}
              onChange={setLocale}
              size="small"
              variant="borderless"
              style={{ width: 110 }}
              options={[
                { value: "zh-CN", label: "中文" },
                { value: "en-US", label: "English" },
              ]}
              suffixIcon={<GlobalOutlined />}
            />
            <Space size={12}>
              <Tooltip title={isDark ? "切换浅色模式" : "切换深色模式"}>
                <Button
                  aria-label={isDark ? "切换浅色模式" : "切换深色模式"}
                  shape="circle"
                  icon={isDark ? <SunOutlined /> : <MoonOutlined />}
                  onClick={toggleMode}
                />
              </Tooltip>
              <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
                <div className="flex cursor-pointer items-center gap-2">
                  <Avatar icon={<UserOutlined />} size="small" />
                  <span>{user.name || user.email}</span>
                </div>
              </Dropdown>
            </Space>
          </Header>
          <Content
            className="admin-content my-4 rounded-lg p-5 max-w-[1440px] mx-auto w-full flex-1"
          >
            {children}
          </Content>
        </Layout>
      </Layout>
    </ConfigProvider>
  );
}

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <I18nProvider>
      <DashboardInner>{children}</DashboardInner>
    </I18nProvider>
  );
}
