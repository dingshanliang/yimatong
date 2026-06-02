"use client";

import { useEffect, useState, useCallback } from "react";
import { Alert, App, Button, Dropdown, Form, Input, Modal, Select, Table, Tabs, Tag, Typography } from "antd";
import type { MenuProps } from "antd";
import { CopyOutlined, DownOutlined, EditOutlined, KeyOutlined, PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { useCrud } from "@/lib/hooks";

const { Text, Title } = Typography;

interface Organization {
  id: string;
  name: string;
  parent_id?: string;
  account_count?: number;
}

interface Account {
  id: string;
  email: string;
  name: string;
  organization_id: string;
  organization_name?: string;
  tenant_id: string;
  initial_password?: string;
}

interface Role {
  id: string;
  name: string;
  description?: string;
  permissions?: string[];
  is_active?: boolean;
}

export default function AccountsPage() {
  const { message, modal } = App.useApp();
  const [activeTab, setActiveTab] = useState("orgs");
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [orgsLoading, setOrgsLoading] = useState(false);
  const [orgModalOpen, setOrgModalOpen] = useState(false);
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
      const { data } = await api.get("/organizations");
      setOrgs(Array.isArray(data) ? data : data.items || []);
    } catch {
      message.error("加载组织列表失败");
    } finally {
      setOrgsLoading(false);
    }
  }, [message]);

  useEffect(() => {
    if (activeTab === "orgs") fetchOrgs();
  }, [activeTab, fetchOrgs]);

  const handleCreateOrg = async (values: { name: string }) => {
    try {
      await api.post("/organizations", values);
      message.success("组织创建成功");
      setOrgModalOpen(false);
      orgForm.resetFields();
      fetchOrgs();
    } catch {
      message.error("创建失败");
    }
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

  const orgColumns: ColumnsType<Organization> = [
    { title: "组织名称", dataIndex: "name", key: "name" },
    {
      title: "账户数",
      dataIndex: "account_count",
      key: "account_count",
      render: (v: number, record) => <Tag data-testid={`org-account-count-${record.id}`}>{v || 0}</Tag>,
    },
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
  ];

  const accountColumns: ColumnsType<Account> = [
    { title: "姓名", dataIndex: "name", key: "name" },
    { title: "邮箱", dataIndex: "email", key: "email" },
    {
      title: "所属组织",
      dataIndex: "organization_name",
      key: "organization_name",
      render: (v: string, record) => <span data-testid={`account-org-name-${record.id}`}>{v || "未分配"}</span>,
    },
    {
      title: "操作",
      key: "action",
      width: 100,
      render: (_: unknown, record: Account) => (
        <Dropdown menu={{ items: getAccountMenuItems(record) }}>
          <Button type="text" icon={<DownOutlined />} aria-label={`操作菜单-${record.name}`} />
        </Dropdown>
      ),
    },
  ];

  const openAccountModal = () => {
    setCreatedAccount(null);
    accountForm.setFieldsValue({ organization_id: orgs[0]?.id });
    setAccountModalOpen(true);
  };

  const handleEditAccount = async (values: Record<string, string>) => {
    if (!editingAccount) return;
    try {
      await api.patch(`/accounts/${editingAccount.id}`, {
        name: values.name,
        organization_id: values.organization_id,
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
        <Title level={4} className="!mb-0">组织与账户</Title>
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
                <div className="mb-4 flex items-center gap-4">
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => setOrgModalOpen(true)}>
                    新建组织
                  </Button>
                  <Input.Search
                    placeholder="搜索组织名称"
                    allowClear
                    style={{ width: 260 }}
                    onSearch={(value) => {
                      if (value) {
                        setOrgs((prev) => prev.filter((o) => o.name.includes(value)));
                      } else {
                        fetchOrgs();
                      }
                    }}
                  />
                </div>
                <Table columns={orgColumns} dataSource={orgs} rowKey="id" loading={orgsLoading} />
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
                  description="所属组织决定账号可查看和操作的数据范围。创建账户后，系统会发放一次性临时密码给使用人登录。"
                />
                <div className="mb-4 flex items-center gap-4">
                  <Button type="primary" icon={<PlusOutlined />} onClick={openAccountModal}>
                    新建账户
                  </Button>
                  <Input.Search
                    placeholder="搜索姓名或邮箱"
                    allowClear
                    style={{ width: 260 }}
                    onSearch={(value) => setAccountsFilter(value ? { q: value } : {})}
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
      <Modal title="新建组织" open={orgModalOpen} onCancel={() => setOrgModalOpen(false)} onOk={() => orgForm.submit()}>
        <Form form={orgForm} layout="vertical" onFinish={handleCreateOrg}>
          <Form.Item name="name" label="组织名称" rules={[{ required: true, message: "请输入组织名称" }]}>
            <Input />
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
              content: "临时密码仅在此处显示一次，关闭后将无法再次查看。请确保已复制密码。",
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
                  登录邮箱：{createdAccount.email}，临时密码：<Text code>{createdAccount.initial_password}</Text>。
                  临时密码只在这里显示一次，请立即交付给账号使用人。
                </div>
                <Button
                  className="mt-2"
                  size="small"
                  aria-label="复制临时密码"
                  icon={<CopyOutlined />}
                  onClick={() => copyInitialPassword(createdAccount.initial_password!)}
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
        <Form form={accountForm} layout="vertical" onFinish={handleCreateAccount}>
          <Form.Item name="email" label="邮箱" rules={[{ required: true, type: "email", message: "请输入有效邮箱" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="name" label="姓名" rules={[{ required: true, message: "请输入姓名" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="organization_id" label="所属组织" rules={[{ required: true, message: "请选择组织" }]}>
            <Select placeholder="选择组织" options={orgs.map((o) => ({ value: o.id, label: o.name }))} />
          </Form.Item>
          <Form.Item name="role_ids" label="角色">
            <Select
              mode="multiple"
              placeholder="选择角色（可选）"
              options={availableRoles.map((r) => ({ value: r.id, label: r.name }))}
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
          <Button type="primary" icon={<CopyOutlined />} onClick={copyResetLink}>
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
              <div className="mt-2 text-xs text-gray-500">链接 1 小时内有效，用户设置新密码后自动失效。</div>
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
          <Form.Item name="name" label="姓名" rules={[{ required: true, message: "请输入姓名" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="organization_id" label="所属组织" rules={[{ required: true, message: "请选择组织" }]}>
            <Select placeholder="选择组织" options={orgs.map((o) => ({ value: o.id, label: o.name }))} />
          </Form.Item>
          <Form.Item name="role_ids" label="角色">
            <Select
              mode="multiple"
              placeholder="选择角色（可选）"
              options={availableRoles.map((r) => ({ value: r.id, label: r.name }))}
              allowClear
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
