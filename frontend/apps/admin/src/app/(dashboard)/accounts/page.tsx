"use client";

import { useEffect, useState, useCallback } from "react";
import { Alert, App, Button, Form, Input, Modal, Select, Table, Tabs, Tag, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title } = Typography;

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

export default function AccountsPage() {
  const { message } = App.useApp();
  const [activeTab, setActiveTab] = useState("orgs");
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(false);
  const [orgModalOpen, setOrgModalOpen] = useState(false);
  const [accountModalOpen, setAccountModalOpen] = useState(false);
  const [createdAccount, setCreatedAccount] = useState<Account | null>(null);
  const [orgForm] = Form.useForm();
  const [accountForm] = Form.useForm();

  const fetchOrgs = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/organizations");
      setOrgs(Array.isArray(data) ? data : data.items || []);
    } catch {
      message.error("加载组织列表失败");
    } finally {
      setLoading(false);
    }
  }, [message]);

  const fetchAccounts = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/accounts");
      setAccounts(Array.isArray(data) ? data : data.items || []);
    } catch {
      message.error("加载账户列表失败");
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    if (activeTab === "orgs") fetchOrgs();
    else fetchAccounts();
  }, [activeTab, fetchOrgs, fetchAccounts]);

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
      fetchAccounts();
      fetchOrgs();
    } catch {
      message.error("创建失败");
    }
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

  const accountColumns: ColumnsType<Account> = [
    { title: "姓名", dataIndex: "name", key: "name" },
    { title: "邮箱", dataIndex: "email", key: "email" },
    {
      title: "所属组织",
      dataIndex: "organization_name",
      key: "organization_name",
      render: (v: string, record) => <span data-testid={`account-org-name-${record.id}`}>{v || "未分配"}</span>,
    },
  ];

  const openAccountModal = () => {
    setCreatedAccount(null);
    accountForm.setFieldsValue({ organization_id: orgs[0]?.id });
    setAccountModalOpen(true);
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
                <div className="mb-4">
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => setOrgModalOpen(true)}>
                    新建组织
                  </Button>
                </div>
                <Table columns={orgColumns} dataSource={orgs} rowKey="id" loading={loading && activeTab === "orgs"} />
              </>
            ),
          },
          {
            key: "accounts",
            label: "账户管理",
            children: (
              <>
                <div className="mb-4">
                  <Button type="primary" icon={<PlusOutlined />} onClick={openAccountModal}>
                    新建账户
                  </Button>
                </div>
                <Table columns={accountColumns} dataSource={accounts} rowKey="id" loading={loading && activeTab === "accounts"} />
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
          setAccountModalOpen(false);
          setCreatedAccount(null);
          accountForm.resetFields();
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
            description={`登录邮箱：${createdAccount.email}，临时密码：${createdAccount.initial_password}`}
          />
        )}
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
          <Alert type="info" showIcon message="系统会生成一次性临时密码，创建后请立即交付给账号使用人。" />
        </Form>
      </Modal>
    </div>
  );
}
