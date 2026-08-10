"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import { useRouter, usePathname } from "next/navigation";
import {
  Button,
  App,
  ConfigProvider,
  Layout,
  Menu,
  Avatar,
  Dropdown,
  Select,
  Space,
  Spin,
  Tooltip,
} from "antd";
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
  RocketOutlined,
  RobotOutlined,
  GlobalOutlined,
  MoonOutlined,
  SunOutlined,
  BgColorsOutlined,
  SwapOutlined,
} from "@ant-design/icons";
import type { MenuProps } from "antd";
import { useAuthStore } from "@/lib/auth";
import { canViewRoleDirectory } from "@/lib/account-access";
import { catalogAccessForPrincipal } from "@/lib/catalog-access";
import { codeAccessForPrincipal } from "@/lib/code-access";
import {
  agencyScopeMenuRoutes,
  canManageAgencyAuthorizations,
} from "@/lib/agency-access";
import { extractErrorMessage } from "@/lib/api";
import { I18nProvider, useI18n } from "@/lib/i18n";
import { useAdminTheme } from "@/lib/theme-provider";
import zhCN from "antd/locale/zh_CN";
import enUS from "antd/locale/en_US";
import OnboardingWizard from "./_components/OnboardingWizard";
import TenantPlanReadOnly from "./_components/TenantPlanReadOnly";
import { filterMenuItemsByFeatures } from "./_components/menu-entitlement";
import {
  TENANT_PLAN_EXPIRED_EVENT,
  isTenantPlanExpired,
  setTenantPlanReadOnly,
  tenantEntitlementKey,
} from "@/lib/plan-entitlement";

const { Header, Sider, Content } = Layout;

const LOCALE_MAP: Record<
  string,
  Parameters<typeof ConfigProvider>[0]["locale"]
> = {
  "zh-CN": zhCN,
  "en-US": enUS,
};

const MENU_OPEN_KEY_RULES = [
  {
    key: "catalog-group",
    prefixes: ["/brands", "/products", "/skus", "/batches"],
  },
  { key: "traceability-group", prefixes: ["/codes", "/pages"] },
  { key: "growth-group", prefixes: ["/campaigns", "/benefits", "/members"] },
  {
    key: "channels-group",
    prefixes: [
      "/channels",
      "/channel-portal",
      "/store-portal",
      "/regional",
      "/accounts",
    ],
  },
  {
    key: "/analytics",
    prefixes: ["/analytics", "/campaign-analytics", "/gmv"],
  },
  { key: "/risk-center", prefixes: ["/risk-center", "/risk-dashboard"] },
  {
    key: "integrations-group",
    prefixes: ["/connectors", "/integrations", "/imports", "/crm-sync"],
  },
  {
    key: "governance-group",
    prefixes: ["/risk", "/launch-checklist", "/pilot"],
  },
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
    items: ["/agency", "/channel-portal", "/store-portal"],
  },
  agency: {
    mode: "allowlist",
    items: [
      "/",
      "/ai-assistant",
      "/agency",
      "/pages",
      "/campaigns",
      "/products",
      "/codes",
      "/launch-checklist",
      "/pilot",
      "/analytics",
      "settings-group",
      "/settings/brand-profile",
      "/settings/roles",
      "/settings/compliance",
      "/settings/tenant",
      "/settings/branding",
      "/settings/audit-logs",
      "/i18n",
    ],
  },
  regional_org: {
    mode: "blocklist",
    items: [
      "/agency",
      "/skus",
      "/batches",
      "/benefits",
      "/channels",
      "/accounts",
      "/risk-center",
      "/integrations",
      "/connectors",
      "/risk",
      "/crm-sync",
      "/imports",
      "/channel-portal",
      "/store-portal",
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
  const { user, hydrate, logout, exitAgencyContext } = useAuthStore();
  const { message } = App.useApp();
  const [exitingAgencyContext, setExitingAgencyContext] = useState(false);
  const [serverReportedPlanExpired, setServerReportedPlanExpired] =
    useState(false);
  const { locale, setLocale, t } = useI18n();
  const { isDark, toggleMode } = useAdminTheme();
  const {
    data: currentTenant,
    mutate: refreshCurrentTenant,
    isValidating: refreshingPlan,
  } = useSWR<{
    tenant_id: string;
    plan: string;
    plan_expires_at: string | null;
    read_only: boolean;
    enabled_features?: Record<string, boolean> | null;
  }>(user ? tenantEntitlementKey(user.acting_tenant_id) : null, {
    refreshInterval: 60_000,
    revalidateOnFocus: true,
  });
  const planExpired =
    serverReportedPlanExpired ||
    currentTenant?.read_only === true ||
    isTenantPlanExpired(currentTenant?.plan_expires_at);

  useEffect(() => {
    const markExpired = () => setServerReportedPlanExpired(true);
    window.addEventListener(TENANT_PLAN_EXPIRED_EVENT, markExpired);
    return () =>
      window.removeEventListener(TENANT_PLAN_EXPIRED_EVENT, markExpired);
  }, []);

  useEffect(() => {
    if (currentTenant && !isTenantPlanExpired(currentTenant.plan_expires_at)) {
      setServerReportedPlanExpired(false);
    }
  }, [currentTenant]);

  useEffect(() => {
    setTenantPlanReadOnly(planExpired);
    return () => setTenantPlanReadOnly(false);
  }, [planExpired]);

  useEffect(() => {
    hydrate();
    if (!localStorage.getItem("access_token")) {
      router.replace("/login");
    }
  }, [hydrate, router]);

  const selectedKeys = [pathname];

  const handleExitAgencyContext = async () => {
    setExitingAgencyContext(true);
    try {
      await exitAgencyContext();
      message.success("已退出客户工作区");
      router.push("/agency");
    } catch (error) {
      message.error(extractErrorMessage(error, "退出客户工作区失败，请重试"));
    } finally {
      setExitingAgencyContext(false);
    }
  };

  const openKeys = MENU_OPEN_KEY_RULES.filter(({ prefixes }) =>
    prefixes.some(
      (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`)
    )
  ).map(({ key }) => key);
  const catalogAccess = catalogAccessForPrincipal(user);
  const codeAccess = codeAccessForPrincipal(user);

  const channelMenuChildren: MenuProps["items"] = [
    { key: "/channels", icon: <ShopOutlined />, label: t("menu.channels") },
    { key: "/regional", icon: <TeamOutlined />, label: t("menu.regional") },
    { key: "/accounts", icon: <TeamOutlined />, label: t("menu.accounts") },
    {
      key: "/channel-portal",
      icon: <ShopOutlined />,
      label: t("menu.channel-portal"),
    },
    {
      key: "/store-portal",
      icon: <ShopOutlined />,
      label: t("menu.store-portal"),
    },
  ];

  const menuItems: MenuProps["items"] = [
    { key: "/", icon: <DashboardOutlined />, label: t("menu.dashboard") },
    {
      key: "/ai-assistant",
      icon: <RobotOutlined />,
      label: t("menu.ai-assistant"),
    },
    {
      key: "catalog-group",
      icon: <AppstoreOutlined />,
      label: t("menu.group.catalog"),
      children: [
        ...(catalogAccess.canRead
          ? [
              {
                key: "/brands",
                icon: <TagOutlined />,
                label: t("menu.brands"),
              },
              {
                key: "/products",
                icon: <AppstoreOutlined />,
                label: t("menu.products"),
              },
              {
                key: "/skus",
                icon: <ProfileOutlined />,
                label: t("menu.skus"),
              },
              {
                key: "/batches",
                icon: <DatabaseOutlined />,
                label: t("menu.batches"),
              },
            ]
          : []),
      ],
    },
    {
      key: "traceability-group",
      icon: <QrcodeOutlined />,
      label: t("menu.group.traceability"),
      children: [
        { key: "/codes", icon: <QrcodeOutlined />, label: t("menu.codes") },
        {
          key: "/codes/takeover",
          icon: <SwapOutlined />,
          label: t("menu.takeover"),
        },
        { key: "/pages", icon: <FileTextOutlined />, label: t("menu.pages") },
      ],
    },
    {
      key: "growth-group",
      icon: <GiftOutlined />,
      label: t("menu.group.growth"),
      children: [
        {
          key: "/campaigns",
          icon: <GiftOutlined />,
          label: t("menu.campaigns"),
        },
        {
          key: "/benefits",
          icon: <SafetyCertificateOutlined />,
          label: t("menu.benefits"),
        },
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
    {
      key: "/analytics",
      icon: <LineChartOutlined />,
      label: t("menu.deep-analytics"),
    },
    {
      key: "/risk-center",
      icon: <SafetyCertificateOutlined />,
      label: t("menu.risk-center"),
    },
    ...(user?.tenant_type === "brand" && codeAccess.canManage
      ? [
          {
            key: "/exports",
            icon: <ExportOutlined />,
            label: t("menu.exports"),
          },
        ]
      : []),
    {
      key: "integrations-group",
      icon: <DatabaseOutlined />,
      label: t("menu.integrations"),
      children: [
        {
          key: "/integrations",
          icon: <DatabaseOutlined />,
          label: t("menu.integrations"),
        },
        {
          key: "/connectors",
          icon: <DatabaseOutlined />,
          label: t("menu.connectors"),
        },
      ],
    },
    {
      key: "governance-group",
      icon: <SafetyCertificateOutlined />,
      label: t("menu.group.governance"),
      children: [
        {
          key: "/risk",
          icon: <SafetyCertificateOutlined />,
          label: t("menu.risk"),
        },
        {
          key: "/launch-checklist",
          icon: <CheckSquareOutlined />,
          label: t("menu.launch-checklist"),
        },
        {
          key: "/pilot",
          icon: <RocketOutlined />,
          label: t("menu.pilot"),
        },
      ],
    },
    {
      key: "settings-group",
      icon: <SettingOutlined />,
      label: t("menu.settings"),
      children: [
        ...(canManageAgencyAuthorizations(user)
          ? [
              {
                key: "/settings/agency-authorizations",
                icon: <TeamOutlined />,
                label: "代运营授权",
              },
            ]
          : []),
        {
          key: "/settings/brand-profile",
          icon: <BgColorsOutlined />,
          label: t("menu.brand-profile"),
        },
        ...(canViewRoleDirectory(user?.role?.toLowerCase())
          ? [
              {
                key: "/settings/roles",
                icon: <UserAddOutlined />,
                label: t("menu.roles"),
              },
            ]
          : []),
        {
          key: "/settings/compliance",
          icon: <SafetyCertificateOutlined />,
          label: t("menu.compliance"),
        },
        {
          key: "/settings/tenant",
          icon: <ShopOutlined />,
          label: t("menu.tenant"),
        },
        {
          key: "/settings/branding",
          icon: <GlobalOutlined />,
          label: t("menu.custom-domain"),
        },
        {
          key: "/settings/audit-logs",
          icon: <FileTextOutlined />,
          label: t("menu.audit-logs"),
        },
        { key: "/i18n", icon: <GlobalOutlined />, label: t("menu.i18n") },
      ],
    },
  ];

  const tenantType = (user?.tenant_type || "brand") as TenantType;
  const policy = MENU_PERMISSIONS[tenantType] || MENU_PERMISSIONS.brand;
  const portalOverride = ROLE_PORTAL_MAP[user?.role?.toLowerCase() || ""];

  const permissionFilteredMenuItems = (() => {
    if (!menuItems) return menuItems;

    // Portal users (distributor/store_guide) see only their portal
    if (portalOverride) {
      const dashboardItem = menuItems.find(
        (item) => item != null && "key" in item && item.key === "/"
      );
      const portalItem = menuItems
        .flatMap((item) =>
          item && "children" in item && Array.isArray(item.children)
            ? item.children
            : []
        )
        .find(
          (item) => item != null && "key" in item && item.key === portalOverride
        );
      return [dashboardItem, portalItem].filter(
        (item): item is NonNullable<typeof item> => item != null
      );
    }

    // Agency in client context: show brand-like menu filtered by agency_scope
    if (tenantType === "agency" && user?.acting_tenant_id) {
      const scope = user.agency_scope || [];
      const scopeAllowlist = new Set(
        scope.flatMap((item) => agencyScopeMenuRoutes(item))
      );

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
  })();

  const filteredMenuItems = filterMenuItemsByFeatures(
    permissionFilteredMenuItems,
    currentTenant?.enabled_features
  );

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
      onClick: async () => {
        try {
          await logout();
          router.replace("/login");
        } catch (error) {
          message.error(
            extractErrorMessage(error, "退出失败，请检查网络后重试")
          );
          if (!useAuthStore.getState().user) {
            router.replace("/login");
          }
        }
      },
    },
  ];

  return (
    <ConfigProvider locale={LOCALE_MAP[locale]}>
      <Layout
        className="admin-shell min-h-screen"
        style={{ minHeight: "100vh" }}
      >
        <Sider
          breakpoint="lg"
          collapsedWidth={0}
          width={220}
          className="admin-sider"
          style={{ minHeight: "100vh" }}
        >
          <div className="my-4 flex h-10 items-center justify-center border-b border-white/10 pb-4">
            <span className="text-lg font-bold text-white">
              {t("common.brand")}
            </span>
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
        <Layout
          className="admin-workspace flex flex-col"
          style={{ minHeight: "100vh" }}
        >
          <Header className="admin-header flex items-center justify-between px-6">
            {/* Agency context indicator */}
            {user?.tenant_type === "agency" && user?.acting_tenant_id && (
              <div
                style={{
                  background: "var(--ymt-color-brand-primary)",
                  color: "var(--ymt-color-text-inverse)",
                  padding: "4px 12px",
                  borderRadius: "var(--ymt-radius-sm)",
                  fontSize: "var(--ymt-font-size-sm)",
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                }}
              >
                <span>客户: {user.acting_tenant_id}</span>
                <Button
                  size="small"
                  type="text"
                  style={{
                    color: "var(--ymt-color-text-inverse)",
                    padding: "0 4px",
                  }}
                  loading={exitingAgencyContext}
                  onClick={handleExitAgencyContext}
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
          <Content className="admin-content my-4 rounded-lg p-5 max-w-360 mx-auto w-full flex-1">
            <TenantPlanReadOnly
              active={planExpired}
              enabledFeatures={currentTenant?.enabled_features}
              refreshing={refreshingPlan}
              onRefresh={() => void refreshCurrentTenant()}
            >
              {user?.tenant_type === "brand" && <OnboardingWizard />}
              {children}
            </TenantPlanReadOnly>
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
