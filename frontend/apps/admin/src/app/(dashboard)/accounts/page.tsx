"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import {
  Alert,
  App,
  Button,
  Dropdown,
  Form,
  Input,
  Modal,
  Select,
  Table,
  Tabs,
  Tag,
  TreeSelect,
  Typography,
} from "antd";
import type { MenuProps } from "antd";
import {
  CheckCircleOutlined,
  CopyOutlined,
  DeleteOutlined,
  DownOutlined,
  EditOutlined,
  KeyOutlined,
  PlusOutlined,
  StopOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { useCrud } from "@/lib/hooks";
import { STATUS_COLORS } from "@/lib/status-colors";

const { Text, Title } = Typography;

interface Organization {
  id: string;
  name: string;
  parent_id?: string;
  account_count?: number;
  children?: Organization[];
}

interface Account {
  id: string;
  email: string;
  name: string;
  organization_id: string;
  organization_name?: string;
  tenant_id: string;
  initial_password?: string;
  is_active: boolean;
  roles?: Role[];
}

interface Role {
  id: string;
  name: string;
  description?: string;
  permissions?: string[];
  is_active?: boolean;
}

const ROLE_LABELS: Record<string, string> = {
  admin: "租户管理员",
  operator: "运营人员",
  viewer: "受限成员（无业务权限）",
};

/** Build tree structure from flat organization list */
function buildOrgTree(orgs: Organization[]): Organization[] {
  const map = new Map<string, Organization & { children: Organization[] }>();
  const roots: Organization[] = [];

  // First pass: create nodes with empty children
  for (const org of orgs) {
    map.set(org.id, { ...org, children: [] });
  }

  // Second pass: link children to parents
  for (const org of orgs) {
    const node = map.get(org.id)!;
    if (org.parent_id && map.has(org.parent_id)) {
      map.get(org.parent_id)!.children.push(node);
    } else {
      roots.push(node);
    }
  }

  return roots;
}

/** Build TreeSelect data from flat org list, excluding self and descendants */
function buildTreeSelectData(
  orgs: Organization[],
  excludeId?: string
): {
  title: string;
  value: string;
  children?: { title: string; value: string }[];
}[] {
  const tree = buildOrgTree(orgs);

  function filterNode(node: Organization): {
    title: string;
    value: string;
    children?: { title: string; value: string }[];
  } | null {
    if (node.id === excludeId) return null;
    const filteredChildren = (node.children || [])
      .map(filterNode)
      .filter(Boolean) as {
      title: string;
      value: string;
      children?: { title: string; value: string }[];
    }[];
    return {
      title: node.name,
      value: node.id,
      ...(filteredChildren.length > 0 ? { children: filteredChildren } : {}),
    };
  }

  return tree.map(filterNode).filter(Boolean) as {
    title: string;
    value: string;
    children?: { title: string; value: string }[];
  }[];
}

export default function AccountsPage() {
  const role = useAuthStore((state) => state.user?.role?.toLowerCase());

  if (role === "viewer") {
    return (
      <div>
        <Title level={4}>组织与账户</Title>
        <Alert
          type="info"
          showIcon
          message="当前角色不能查看账户目录"
          description="如需查看或调整组织与账户，请联系租户管理员处理。"
        />
      </div>
    );
  }

  return <AccountsWorkspace canManage={role === "admin"} />;
}

function AccountsWorkspace({ canManage }: { canManage: boolean }) {
  const { message, modal } = App.useApp();
  const [activeTab, setActiveTab] = useState("orgs");
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [orgsLoading, setOrgsLoading] = useState(false);
  const [orgModalOpen, setOrgModalOpen] = useState(false);
  const [editingOrg, setEditingOrg] = useState<Organization | null>(null);
  const [accountModalOpen, setAccountModalOpen] = useState(false);
  const [createdAccount, setCreatedAccount] = useState<Account | null>(null);
  const [resetLinkModalOpen, setResetLinkModalOpen] = useState(false);
  const [resetLink, setResetLink] = useState("");
  const [resetLoading, setResetLoading] = useState(false);
  const [orgForm] = Form.useForm();
  const [accountForm] = Form.useForm();
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editingAccount, setEditingAccount] = useState<Account | null>(null);
  const [editForm] = Form.useForm();
  const [statusTarget, setStatusTarget] = useState<Account | null>(null);
  const [statusReason, setStatusReason] = useState("");
  const [statusLoading, setStatusLoading] = useState(false);

  // Accounts via useCrud (paginated, SWR-backed)
  const {
    items: accounts,
    total: accountsTotal,
    page: accountsPage,
    loading: accountsLoading,
    setPage: setAccountsPage,
    setFilter: setAccountsFilter,
    mutate: mutateAccounts,
  } = useCrud<Account>("/accounts");

  // Roles list for account create/edit
  const { items: availableRoles } = useCrud<Role>("/roles");

  const fetchOrgs = useCallback(async () => {
    setOrgsLoading(true);
    try {
      const { data } = await api.get<Organization[]>("/organizations/tree");
      setOrgs(data);
    } catch {
      message.error("加载组织列表失败");
    } finally {
      setOrgsLoading(false);
    }
  }, [message]);

  useEffect(() => {
    if (activeTab === "orgs") fetchOrgs();
  }, [activeTab, fetchOrgs]);

  // Tree data for org table
  const orgTreeData = useMemo(() => buildOrgTree(orgs), [orgs]);

  // TreeSelect data for create/edit modals
  const treeSelectData = useMemo(
    () => buildTreeSelectData(orgs, editingOrg?.id),
    [orgs, editingOrg?.id]
  );

  const handleCreateOrg = async (values: {
    name: string;
    parent_id?: string;
  }) => {
    try {
      await api.post("/organizations", {
        name: values.name,
        parent_id: values.parent_id || null,
      });
      message.success("组织创建成功");
      setOrgModalOpen(false);
      orgForm.resetFields();
      fetchOrgs();
    } catch {
      message.error("创建失败");
    }
  };

  const handleEditOrg = async (values: {
    name: string;
    parent_id?: string;
  }) => {
    if (!editingOrg) return;
    try {
      await api.patch(`/organizations/${editingOrg.id}`, {
        name: values.name,
        parent_id: values.parent_id || null,
      });
      message.success("组织更新成功");
      setEditingOrg(null);
      orgForm.resetFields();
      fetchOrgs();
    } catch {
      message.error("更新失败");
    }
  };

  const handleDeleteOrg = (org: Organization) => {
    modal.confirm({
      title: "删除组织",
      content: `确定删除「${org.name}」吗？删除后不可恢复。`,
      okText: "确定删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        try {
          await api.delete(`/organizations/${org.id}`);
          message.success("组织已删除");
          fetchOrgs();
        } catch (err: unknown) {
          const detail = (err as { response?: { data?: { detail?: string } } })
            ?.response?.data?.detail;
          message.error(detail || "删除失败");
        }
      },
    });
  };

  const handleCreateAccount = async (values: Record<string, string>) => {
    try {
      const { data } = await api.post("/accounts", values);
      message.success("账户创建成功");
      setCreatedAccount(data);
      accountForm.resetFields();
      mutateAccounts();
      fetchOrgs();
    } catch {
      message.error("创建失败");
    }
  };

  const copyInitialPassword = async (password: string) => {
    try {
      await navigator.clipboard.writeText(password);
      message.success("临时密码已复制");
    } catch {
      message.error("复制失败，请手动复制");
    }
  };

  const copyResetLink = async () => {
    try {
      await navigator.clipboard.writeText(resetLink);
      message.success("重置链接已复制");
    } catch {
      message.error("复制失败，请手动复制");
    }
  };

  const handleResetPassword = (account: Account) => {
    modal.confirm({
      title: "生成密码重置链接",
      content: `确定为「${account.name}」（${account.email}）生成密码重置链接吗？`,
      okText: "确定生成",
      cancelText: "取消",
      onOk: async () => {
        setResetLoading(true);
        try {
          const { data } = await api.post("/auth/generate-reset-token", {
            account_id: account.id,
          });
          setResetLink(data.reset_url);
          setResetLinkModalOpen(true);
        } catch {
          message.error("生成重置链接失败");
        } finally {
          setResetLoading(false);
        }
      },
    });
  };

  const updateAccountStatus = async (
    account: Account,
    isActive: boolean,
    reason: string
  ) => {
    setStatusLoading(true);
    try {
      await api.patch(`/accounts/${account.id}/status`, {
        is_active: isActive,
        reason,
      });
      message.success(
        isActive ? "账户已重新启用" : "账户已停用，原登录状态已失效"
      );
      mutateAccounts();
      setStatusTarget(null);
      setStatusReason("");
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      message.error(detail || (isActive ? "启用失败" : "停用失败"));
      throw err;
    } finally {
      setStatusLoading(false);
    }
  };

  const handleConfirmDisable = async () => {
    if (!statusTarget) return;
    const reason = statusReason.trim();
    if (reason.length < 2) {
      message.error("请填写停用原因");
      return;
    }
    await updateAccountStatus(statusTarget, false, reason);
  };

  const openEditOrgModal = (org: Organization) => {
    setEditingOrg(org);
    orgForm.setFieldsValue({
      name: org.name,
      parent_id: org.parent_id || undefined,
    });
    setOrgModalOpen(true);
  };

  const getOrgMenuItems = (record: Organization): MenuProps["items"] => [
    {
      key: "edit",
      label: "编辑组织",
      icon: <EditOutlined />,
      onClick: () => openEditOrgModal(record),
    },
    {
      key: "delete",
      label: "删除组织",
      icon: <DeleteOutlined />,
      danger: true,
      onClick: () => handleDeleteOrg(record),
    },
  ];

  const orgColumns: ColumnsType<Organization> = [
    { title: "组织名称", dataIndex: "name", key: "name" },
    {
      title: "账户数",
      dataIndex: "account_count",
      key: "account_count",
      width: 120,
      render: (v: number, record) => (
        <Tag data-testid={`org-account-count-${record.id}`}>{v || 0}</Tag>
      ),
    },
    ...(canManage
      ? [
          {
            title: "操作",
            key: "action",
            width: 80,
            render: (_: unknown, record: Organization) => (
              <Dropdown menu={{ items: getOrgMenuItems(record) }}>
                <Button
                  type="text"
                  icon={<DownOutlined />}
                  aria-label={`操作菜单-${record.name}`}
                />
              </Dropdown>
            ),
          } satisfies ColumnsType<Organization>[number],
        ]
      : []),
  ];

  const getAccountMenuItems = (record: Account): MenuProps["items"] => [
    {
      key: "edit",
      label: "编辑账户",
      icon: <EditOutlined />,
      onClick: () => {
        setEditingAccount(record);
        editForm.setFieldsValue({
          name: record.name,
          organization_id: record.organization_id,
          role_ids: record.roles?.map((role) => role.id) ?? [],
        });
        setEditModalOpen(true);
      },
    },
    {
      key: "reset",
      label: "重置密码",
      icon: <KeyOutlined />,
      onClick: () => handleResetPassword(record),
    },
    record.is_active !== false
      ? {
          key: "disable",
          label: "停用账户",
          icon: <StopOutlined />,
          danger: true,
          onClick: () => {
            setStatusTarget(record);
            setStatusReason("");
          },
        }
      : {
          key: "enable",
          label: "重新启用",
          icon: <CheckCircleOutlined />,
          onClick: () => {
            void updateAccountStatus(record, true, "管理员重新启用账户").catch(
              () => undefined
            );
          },
        },
  ];

  const accountColumns: ColumnsType<Account> = [
    { title: "姓名", dataIndex: "name", key: "name" },
    { title: "邮箱", dataIndex: "email", key: "email" },
    {
      title: "角色",
      key: "roles",
      render: (_: unknown, record) =>
        record.roles?.length ? (
          record.roles.map((role) => (
            <Tag key={role.id}>{ROLE_LABELS[role.name] || role.name}</Tag>
          ))
        ) : (
          <Text type="secondary">未分配</Text>
        ),
    },
    {
      title: "所属组织",
      dataIndex: "organization_name",
      key: "organization_name",
      render: (v: string, record) => (
        <span data-testid={`account-org-name-${record.id}`}>
          {v || "未分配"}
        </span>
      ),
    },
    {
      title: "状态",
      dataIndex: "is_active",
      key: "is_active",
      width: 100,
      render: (isActive: boolean, record) => (
        <Tag
          color={
            isActive !== false ? STATUS_COLORS.success : STATUS_COLORS.neutral
          }
          data-testid={`account-status-${record.id}`}
        >
          {isActive !== false ? "已启用" : "已停用"}
        </Tag>
      ),
    },
    ...(canManage
      ? [
          {
            title: "操作",
            key: "action",
            width: 100,
            render: (_: unknown, record: Account) => (
              <Dropdown menu={{ items: getAccountMenuItems(record) }}>
                <Button
                  type="text"
                  icon={<DownOutlined />}
                  aria-label={`操作菜单-${record.name}`}
                />
              </Dropdown>
            ),
          } satisfies ColumnsType<Account>[number],
        ]
      : []),
  ];

  const openAccountModal = () => {
    setCreatedAccount(null);
    accountForm.setFieldsValue({ organization_id: orgs[0]?.id });
    setAccountModalOpen(true);
  };

  const handleEditAccount = async (values: {
    name: string;
    organization_id: string;
    role_ids?: string[];
  }) => {
    if (!editingAccount) return;
    try {
      await api.patch(`/accounts/${editingAccount.id}`, {
        name: values.name,
        organization_id: values.organization_id,
        role_ids: values.role_ids ?? [],
      });
      message.success("账户更新成功");
      setEditModalOpen(false);
      setEditingAccount(null);
      editForm.resetFields();
      mutateAccounts();
      fetchOrgs();
    } catch {
      message.error("更新失败");
    }
  };

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">
          组织与账户
        </Title>
      </div>
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "orgs",
            label: "组织管理",
            children: (
              <>
                <Alert
                  className="mb-4"
                  type="info"
                  showIcon
                  message="组织用于账户归类和日常管理"
                  description="组织本身不会自动限制数据范围；账号可查看和操作的内容由角色与权限决定。"
                />
                <div className="mb-4 flex items-center gap-4">
                  {canManage && (
                    <Button
                      type="primary"
                      icon={<PlusOutlined />}
                      onClick={() => {
                        setEditingOrg(null);
                        orgForm.resetFields();
                        setOrgModalOpen(true);
                      }}
                    >
                      新建组织
                    </Button>
                  )}
                  <Input.Search
                    placeholder="搜索组织名称"
                    allowClear
                    style={{ width: 260 }}
                    onSearch={(value) => {
                      if (value) {
                        setOrgs((prev) =>
                          prev.filter((o) => o.name.includes(value))
                        );
                      } else {
                        fetchOrgs();
                      }
                    }}
                  />
                </div>
                <Table
                  columns={orgColumns}
                  dataSource={orgTreeData}
                  rowKey="id"
                  loading={orgsLoading}
                  pagination={false}
                  indentSize={20}
                  defaultExpandAllRows
                />
              </>
            ),
          },
          {
            key: "accounts",
            label: "账户管理",
            children: (
              <>
                <Alert
                  className="mb-4"
                  type="info"
                  showIcon
                  message="账户用于员工或渠道伙伴登录后台"
                  description="所属组织用于归类和管理账号，不会自动限制数据范围；角色与权限决定账号可查看和操作的内容。管理员创建账户后，系统会发放一次性临时密码。"
                />
                {!canManage && (
                  <Alert
                    className="mb-4"
                    type="warning"
                    showIcon
                    message="当前为只读模式"
                    description="运营人员可以查看组织和账户信息；新建、编辑、重置密码及停用或启用账户由租户管理员处理。"
                  />
                )}
                <div className="mb-4 flex items-center gap-4">
                  {canManage && (
                    <Button
                      type="primary"
                      icon={<PlusOutlined />}
                      onClick={openAccountModal}
                    >
                      新建账户
                    </Button>
                  )}
                  <Input.Search
                    placeholder="搜索姓名或邮箱"
                    allowClear
                    style={{ width: 260 }}
                    onSearch={(value) =>
                      setAccountsFilter(value ? { q: value } : {})
                    }
                  />
                </div>
                <Table
                  columns={accountColumns}
                  dataSource={accounts}
                  rowKey="id"
                  loading={accountsLoading}
                  pagination={{
                    current: accountsPage,
                    total: accountsTotal,
                    pageSize: 20,
                    onChange: setAccountsPage,
                    showTotal: (t) => `共 ${t} 条`,
                  }}
                />
              </>
            ),
          },
        ]}
      />

      {/* Create/Edit Organization Modal */}
      <Modal
        title={editingOrg ? "编辑组织" : "新建组织"}
        open={orgModalOpen}
        onCancel={() => {
          setOrgModalOpen(false);
          setEditingOrg(null);
          orgForm.resetFields();
        }}
        onOk={() => orgForm.submit()}
        okText={editingOrg ? "保存" : "创建"}
      >
        <Form
          form={orgForm}
          layout="vertical"
          onFinish={editingOrg ? handleEditOrg : handleCreateOrg}
        >
          <Form.Item
            name="name"
            label="组织名称"
            rules={[{ required: true, message: "请输入组织名称" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="parent_id" label="上级组织">
            <TreeSelect
              placeholder="无（顶级组织）"
              allowClear
              treeData={treeSelectData}
              treeDefaultExpandAll
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="新建账户"
        open={accountModalOpen}
        onCancel={() => {
          if (createdAccount?.initial_password) {
            modal.confirm({
              title: "确认关闭？",
              content:
                "临时密码仅在此处显示一次，关闭后将无法再次查看。请确保已复制密码。",
              okText: "确认关闭",
              cancelText: "继续查看",
              onOk: () => {
                setAccountModalOpen(false);
                setCreatedAccount(null);
                accountForm.resetFields();
              },
            });
          } else {
            setAccountModalOpen(false);
            setCreatedAccount(null);
            accountForm.resetFields();
          }
        }}
        onOk={() => accountForm.submit()}
        okText="创建账户"
        width={520}
      >
        {createdAccount?.initial_password && (
          <Alert
            className="mb-4"
            data-testid="account-initial-password-alert"
            type="success"
            showIcon
            message="账号已创建"
            description={
              <div>
                <div>
                  登录邮箱：{createdAccount.email}，临时密码：
                  <Text code>{createdAccount.initial_password}</Text>。
                  临时密码只在这里显示一次，请立即交付给账号使用人。
                </div>
                <Button
                  className="mt-2"
                  size="small"
                  aria-label="复制临时密码"
                  icon={<CopyOutlined />}
                  onClick={() =>
                    copyInitialPassword(createdAccount.initial_password!)
                  }
                >
                  复制临时密码
                </Button>
              </div>
            }
          />
        )}
        <Alert
          className="mb-4"
          type="info"
          showIcon
          message="不需要手动设置密码"
          description="提交后系统会生成一次性临时密码。临时密码只在创建成功后显示一次，请当场交付给使用人。"
        />
        <Form
          form={accountForm}
          layout="vertical"
          onFinish={handleCreateAccount}
        >
          <Form.Item
            name="email"
            label="邮箱"
            rules={[
              { required: true, type: "email", message: "请输入有效邮箱" },
            ]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="name"
            label="姓名"
            rules={[{ required: true, message: "请输入姓名" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="organization_id"
            label="所属组织"
            rules={[{ required: true, message: "请选择组织" }]}
          >
            <TreeSelect
              placeholder="选择组织"
              treeData={buildTreeSelectData(orgs)}
              treeDefaultExpandAll
            />
          </Form.Item>
          <Form.Item name="role_ids" label="角色">
            <Select
              mode="multiple"
              placeholder="选择角色（可选）"
              options={availableRoles.map((r) => ({
                value: r.id,
                label: ROLE_LABELS[r.name] || r.name,
              }))}
              allowClear
            />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title="密码重置链接"
        open={resetLinkModalOpen}
        onCancel={() => {
          setResetLinkModalOpen(false);
          setResetLink("");
        }}
        footer={
          <Button
            type="primary"
            icon={<CopyOutlined />}
            onClick={copyResetLink}
          >
            复制链接
          </Button>
        }
        width={520}
      >
        <Alert
          className="mb-4"
          type="success"
          showIcon
          message="重置链接已生成"
          description={
            <div>
              <div>请将此链接通过微信、企微等方式发送给账号使用人：</div>
              <Text code className="mt-2 block break-all text-xs">
                {resetLink}
              </Text>
              <div className="mt-2 text-xs text-text-muted">
                链接 1 小时内有效，用户设置新密码后自动失效。
              </div>
            </div>
          }
        />
      </Modal>
      <Modal
        title="编辑账户"
        open={editModalOpen}
        onCancel={() => {
          setEditModalOpen(false);
          setEditingAccount(null);
          editForm.resetFields();
        }}
        onOk={() => editForm.submit()}
        okText="保存"
        width={520}
      >
        <Form form={editForm} layout="vertical" onFinish={handleEditAccount}>
          <Form.Item
            name="name"
            label="姓名"
            rules={[{ required: true, message: "请输入姓名" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="organization_id"
            label="所属组织"
            rules={[{ required: true, message: "请选择组织" }]}
          >
            <TreeSelect
              placeholder="选择组织"
              treeData={buildTreeSelectData(orgs)}
              treeDefaultExpandAll
            />
          </Form.Item>
          <Form.Item name="role_ids" label="角色">
            <Select
              mode="multiple"
              placeholder="选择角色（可选）"
              options={availableRoles.map((r) => ({
                value: r.id,
                label: ROLE_LABELS[r.name] || r.name,
              }))}
              allowClear
            />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={statusTarget ? `停用「${statusTarget.name}」` : "停用账户"}
        open={Boolean(statusTarget)}
        onCancel={() => {
          setStatusTarget(null);
          setStatusReason("");
        }}
        onOk={handleConfirmDisable}
        okText="确认停用"
        cancelText="取消"
        okButtonProps={{ danger: true, loading: statusLoading }}
        destroyOnHidden
      >
        <Alert
          className="mb-4"
          type="warning"
          showIcon
          message="停用后，该账户当前登录状态会立即失效"
          description="重新启用后，账号使用人需要重新登录。"
        />
        <label className="mb-2 block" htmlFor="account-disable-reason">
          停用原因
        </label>
        <Input.TextArea
          id="account-disable-reason"
          aria-label="停用原因"
          value={statusReason}
          onChange={(event) => setStatusReason(event.target.value)}
          placeholder="例如：员工离职、外部协作结束或账号存在风险"
          maxLength={200}
          showCount
          autoSize={{ minRows: 3, maxRows: 5 }}
        />
      </Modal>
    </div>
  );
}
